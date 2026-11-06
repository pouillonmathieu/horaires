import copy
import io
import zipfile
from collections import defaultdict
from datetime import date

import pytest

from app.connecteurs import ConnecteurFichier
from app.exemple import EXEMPLE, jeu_exemple
from app.fichiers import (
    ErreurImport, donnees_vers_csv_zip, donnees_vers_excel, excel_vers_donnees, horaire_vers_excel, horaire_vers_ics,
)
from app.modeles import JOURS, JeuDeDonnees
from app.solveur.diagnostic import avant_planification, cycles_prerequis
from app.solveur.moteur import Parametres, planifier
from scripts.generer_instance import generer


@pytest.fixture(scope="module")
def ds():
    return jeu_exemple()


@pytest.fixture(scope="module")
def resultat(ds):
    return planifier(ds, Parametres(duree_max_s=30)).en_dict()


def verifier_coherence(ds: JeuDeDonnees, r: dict):
    """Vérifie toutes les contraintes dures sur un horaire calculé."""
    groupes = {g.id: g for g in ds.groupes}
    locaux = {l.id: l for l in ds.locaux}
    ens = {e.id: e for e in ds.enseignants}
    cours = {c.id: c for m in ds.modules for c in m.cours}
    occupe = defaultdict(int)
    for s in r["seances"]:
        d = date.fromisoformat(s["date"])
        g, c, l = groupes[s["groupe"]], cours[s["cours"]], locaux[s["local"]]
        cle = (s["date"], s["creneau"])
        occupe[("g", s["groupe"]) + cle] += 1
        occupe[("l", s["local"]) + cle] += 1
        for e in s["enseignants"]:
            occupe[("e", e) + cle] += 1
            assert ens[e].disponible(d, s["creneau"]), f"{e} indisponible le {d}"
        assert not any(f.contient(d) for f in ds.calendrier.fermetures), "cours un jour de fermeture"
        assert not any(st.bloque(d) for st in g.stages), "cours pendant un stage"
        assert l.capacite >= g.effectif
        assert set(c.equipements) <= set(l.equipements)
        assert not any(i.bloque(d, s["creneau"]) for i in l.indisponibilites)
        assert any(a.enseignants == s["enseignants"] for a in c.attributions_pour(g.id))
        assert not g.creneaux or s["creneau"] in g.creneaux
        assert JOURS[d.weekday()] in (g.jours_cours or ds.calendrier.jours_cours)
    doublons = [k for k, n in occupe.items() if n > 1]
    assert not doublons, f"doubles réservations : {doublons[:5]}"

    # prérequis : toutes les séances du module préalable avant la première du suivant
    bornes = {}
    for s in r["seances"]:
        k = (s["groupe"], s["module"])
        cle = (s["date"], s["heure_debut"])
        lo, hi = bornes.get(k, (cle, cle))
        bornes[k] = (min(lo, cle), max(hi, cle))
    for g in ds.groupes:
        for mid in g.modules:
            for pre in ds.module(mid).prerequis:
                if (g.id, pre) in bornes and (g.id, mid) in bornes:
                    assert bornes[(g.id, pre)][1] < bornes[(g.id, mid)][0], f"{g.id} : {pre} doit précéder {mid}"

    # rythme fixe : mêmes créneaux hebdomadaires du début à la fin, même local sur chaque créneau
    debuts, fins, salles = defaultdict(dict), defaultdict(dict), defaultdict(set)
    for s in r["seances"]:
        c = cours[s["cours"]]
        if (c.rythme or ds.calendrier.rythme_defaut) != "fixe":
            continue
        u, w = (s["groupe"], s["cours"], s["enseignant"]), (s["jour"], s["creneau"])
        sem = date.fromisoformat(s["date"]).toordinal() // 7
        debuts[u][w] = min(debuts[u].get(w, sem), sem)
        fins[u][w] = max(fins[u].get(w, sem), sem)
        salles[(u, w)].add(s["local"])
    for u in debuts:
        assert max(debuts[u].values()) - min(debuts[u].values()) <= 1, f"{u} : début décalé"
        assert max(fins[u].values()) - min(fins[u].values()) <= 2, f"{u} : fin décalée"
    assert all(len(v) == 1 for v in salles.values())


def test_solution_complete(resultat):
    assert resultat["statut"] in ("OPTIMAL", "REALISABLE")
    assert all(l["periodes_manquantes"] == 0 for l in resultat["synthese"]), \
        [l for l in resultat["synthese"] if l["periodes_manquantes"]]


def test_contraintes_respectees(ds, resultat):
    verifier_coherence(ds, resultat)


def test_co_enseignement_repartition_et_attributions_par_groupe(resultat):
    synth = {(l["groupe"], l["cours"], l["enseignant"]): l for l in resultat["synthese"]}
    assert synth[("AS1", "AS-HYG-PR", "LAMBERT + MERTENS")]["periodes_planifiees"] == 40
    assert synth[("AS1", "AS-SOINS-PR", "LAMBERT")]["periodes_planifiees"] == 60
    assert synth[("AS1", "AS-SOINS-PR", "MERTENS")]["periodes_planifiees"] == 40
    assert ("CPT2", "CPT-BASE-TH", "VERMEULEN") in synth and ("CPT", "CPT-BASE-TH", "PEETERS") in synth
    co = [s for s in resultat["seances"] if s["cours"] == "AS-HYG-PR"]
    assert co and all(s["enseignants"] == ["LAMBERT", "MERTENS"] for s in co)


def test_materiel_et_plafonds(ds, resultat):
    mannequin = defaultdict(int)
    lambert = defaultdict(int)
    for s in resultat["seances"]:
        if s["cours"] in ("AS-HYG-PR", "AS-SOINS-PR"):
            mannequin[(s["date"], s["creneau"])] += 1
        if "LAMBERT" in s["enseignants"]:
            lambert[date.fromisoformat(s["date"]).isocalendar()[:2]] += s["periodes"]
    assert max(mannequin.values()) <= 1
    assert max(lambert.values()) <= 16


def test_rythme_fixe(resultat):
    tva = defaultdict(list)
    for s in resultat["seances"]:
        if s["cours"] == "CPT-TVA-1":
            tva[(s["jour"], s["creneau"])].append(s["date"])
    assert tva
    debuts = sorted(min(v) for v in tva.values())
    assert (date.fromisoformat(debuts[-1]) - date.fromisoformat(debuts[0])).days < 7


def test_rythme_fixe_partout():
    brut = copy.deepcopy(EXEMPLE)
    brut["calendrier"]["rythme_defaut"] = "fixe"
    ds = JeuDeDonnees.model_validate(brut)
    r = planifier(ds, Parametres(duree_max_s=20)).en_dict()
    verifier_coherence(ds, r)


def test_salles_specialisees_preservees(resultat):
    # un cours sans besoin de PC ne doit pas occuper le labo informatique
    assert not any(s["local"] == "INFO1" and s["module"].startswith(("CPT-BASE", "CPT-TVA")) for s in resultat["seances"])


def test_manque_signale_sans_planter():
    brut = copy.deepcopy(EXEMPLE)
    c = brut["modules"][0]["cours"][0]
    c["periodes"] = c["attributions"][0]["periodes"] = 2000  # impossible à placer entièrement
    ds = JeuDeDonnees.model_validate(brut)
    assert any(d["niveau"] == "erreur" for d in avant_planification(ds))
    r = planifier(ds, Parametres(duree_max_s=20)).en_dict()
    assert r["statut"] in ("OPTIMAL", "REALISABLE")
    assert sum(l["periodes_manquantes"] for l in r["synthese"]) > 0
    verifier_coherence(ds, r)


def test_etablissement_genere_coherent():
    ds = JeuDeDonnees.model_validate(generer(nb_groupes=12, nb_enseignants=25, nb_modules=50, graine=3))
    r = planifier(ds, Parametres(duree_max_s=40)).en_dict()
    assert r["statut"] in ("OPTIMAL", "REALISABLE")
    verifier_coherence(ds, r)
    requis = sum(l["periodes_requises"] for l in r["synthese"])
    manque = sum(l["periodes_manquantes"] for l in r["synthese"])
    assert manque / requis < 0.1


def test_validation_references():
    brut = copy.deepcopy(EXEMPLE)
    brut["groupes"][0]["modules"] = ["INEXISTANT"]
    with pytest.raises(ValueError, match="module inconnu"):
        JeuDeDonnees.model_validate(brut)
    brut = copy.deepcopy(EXEMPLE)
    brut["modules"][0]["cours"][0]["attributions"][0]["periodes"] = 50
    with pytest.raises(ValueError, match="attributions = 50 périodes"):
        JeuDeDonnees.model_validate(brut)


def test_cycle_prerequis():
    brut = copy.deepcopy(EXEMPLE)
    brut["modules"][0]["prerequis"] = ["AS-SOINS"]
    assert cycles_prerequis(JeuDeDonnees.model_validate(brut))


def test_aller_retour_excel_et_exports(ds, resultat):
    assert excel_vers_donnees(donnees_vers_excel(ds)) == ds
    assert horaire_vers_excel(resultat, "t")[:2] == b"PK"
    ics = horaire_vers_ics(resultat["seances"][:3], "x").decode()
    assert ics.count("BEGIN:VEVENT") == 3


def test_import_csv_tolerant(ds):
    z = donnees_vers_csv_zip(ds)
    assert ConnecteurFichier([("export.zip", z)]).lire_donnees() == ds
    fichiers = []
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        for n in zf.namelist():
            t = zf.read(n).decode("utf-8-sig").replace("2027-01-11", "11/01/2027")
            if n == "cours.csv":
                t = t.replace(";", "\t")
            fichiers.append((n.upper(), t.encode("cp1252")))  # export Windows, noms en majuscules
    assert ConnecteurFichier(fichiers).lire_donnees() == ds
    with pytest.raises(ErreurImport, match="manquant"):
        ConnecteurFichier([f for f in fichiers if f[0] != "COURS.CSV"]).lire_donnees()
    with pytest.raises(ErreurImport, match="non reconnu"):
        ConnecteurFichier(fichiers + [("divers.csv", b"a;b")]).lire_donnees()


def test_import_partiel_complete_par_un_jeu_existant(ds):
    from app.fichiers import csv_vers_donnees
    export = {  # export minimal d'un logiciel de gestion, en formats « une ligne par lien »
        "modules.csv": "id;nom\n" + "\n".join(f"{m.id};{m.nom}" for m in ds.modules),
        "prerequis.csv": "module;prerequis\n" + "\n".join(f"{m.id};{p}" for m in ds.modules for p in m.prerequis),
        "cours.csv": "id;module;nom;periodes\n" + "\n".join(
            f"{c.id};{m.id};{c.nom};{c.periodes}" for m in ds.modules for c in m.cours),
        "attributions.csv": "cours;enseignants;periodes;groupes\n" + "\n".join(
            f"{c.id};{'|'.join(a.enseignants)};{a.periodes};{'|'.join(a.groupes)}"
            for m in ds.modules for c in m.cours for a in c.attributions),
        "groupes.csv": "id;nom;effectif\n" + "\n".join(f"{g.id};{g.nom};{g.effectif + 1}" for g in ds.groupes),
        "parcours.csv": "groupe;module\n" + "\n".join(f"{g.id};{m}" for g in ds.groupes for m in g.modules),
    }
    fichiers = [(n, t.encode("cp1252")) for n, t in export.items()]
    with pytest.raises(ErreurImport, match="manquant"):
        csv_vers_donnees(fichiers)
    r = csv_vers_donnees(fichiers, complement=ds)
    assert r.calendrier == ds.calendrier and r.locaux == ds.locaux and r.enseignants == ds.enseignants
    assert [m.prerequis for m in r.modules] == [m.prerequis for m in ds.modules]
    assert [c.equipements for m in r.modules for c in m.cours] == [c.equipements for m in ds.modules for c in m.cours]
    g = {x.id: x for x in r.groupes}["AS1"]
    assert g.effectif == 13 and g.stages and g.local_prefere == "A102" and g.modules == ds.groupes[0].modules


def test_identifiants_du_logiciel_de_gestion():
    brut = copy.deepcopy(EXEMPLE)
    brut["modules"][0]["id"] = "UE 7/12 Fondements"
    brut["groupes"][0]["modules"][0] = "UE 7/12 Fondements"
    brut["modules"][2]["prerequis"] = ["UE 7/12 Fondements"]
    JeuDeDonnees.model_validate(brut)
    for mauvais in ("a;b", "x:y", "<script>"):
        brut["modules"][0]["id"] = mauvais
        with pytest.raises(ValueError):
            JeuDeDonnees.model_validate(brut)
