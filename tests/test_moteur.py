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


def _minutes(h):
    return int(h[:2]) * 60 + int(h[3:])


def verifier_coherence(ds: JeuDeDonnees, r: dict):
    """Vérifie toutes les contraintes dures sur un horaire calculé."""
    groupes = {g.id: g for g in ds.groupes}
    locaux = {l.id: l for l in ds.locaux}
    ens = {e.id: e for e in ds.enseignants}
    cours = {c.id: c for m in ds.modules for c in m.cours}
    ue_du_cours = {c.id: m for m in ds.modules for c in m.cours}
    et = ds.etablissement
    occupe = defaultdict(int)
    charge_cours = defaultdict(int)  # (groupe, cours, semaine) -> périodes
    sites = defaultdict(list)  # (enseignant, date) -> [(implantation, début, fin)]
    for s in r["seances"]:
        d = date.fromisoformat(s["date"])
        g, c = groupes[s["groupe"]], cours[s["cours"]]
        plages = s["creneau"].split("+")
        rangs = range(s["debut_periode"], s["debut_periode"] + s["periodes"])
        for rang in rangs:
            occupe[("g", s["groupe"], s["date"], rang)] += 1
            if not s["distanciel"]:
                occupe[("l", s["local"], s["date"], rang)] += 1
            for e in s["enseignants"]:
                occupe[("e", e, s["date"], rang)] += 1
        for e in s["enseignants"]:
            assert all(ens[e].disponible(d, p) for p in plages), f"{e} indisponible le {d}"
            if not s["distanciel"] and s["implantation"]:
                sites[(e, s["date"])].append((s["implantation"], _minutes(s["heure_debut"]), _minutes(s["heure_fin"])))
        assert not ds.calendrier.ferme(d), "cours un jour de fermeture"
        ue = ue_du_cours[c.id]
        site = c.implantation or ue.implantation or g.implantation
        assert s["distanciel"] or s["implantation"] == site, "implantation du cours / de l'UE non respectée"
        assert not any(ds.calendrier.ferme_pour(d, p, site) for p in plages), "cours pendant une fermeture partielle"
        debut, fin = c.date_debut or ue.date_debut, c.date_fin or ue.date_fin
        assert (not debut or d >= debut) and (not fin or d <= fin), "séance hors des dates de l'UE / du cours"
        charge_cours[(g.id, c.id, d.isocalendar()[:2])] += s["periodes"]
        assert not any(st.bloque(d) for st in g.stages), "cours pendant un stage"
        assert any(a.enseignants == s["enseignants"] for a in c.attributions_pour(g.id))
        assert not g.creneaux or all(p in g.creneaux for p in plages)
        assert JOURS[d.weekday()] in (g.jours_cours or ds.calendrier.jours_cours)
        if s["distanciel"]:
            assert s["local"] == "Distanciel"
            continue
        l = locaux[s["local"]]
        assert l.capacite >= g.effectif
        assert set(c.equipements) <= set(l.equipements)
        assert not c.locaux or l.id in c.locaux
        assert s["implantation"] is None or l.implantation in (None, s["implantation"]), "local hors implantation"
        assert not any(i.bloque(d, p) for i in l.indisponibilites for p in plages)
        assert all(l.ouvert(JOURS[d.weekday()], p) for p in plages), f"local {l.id} pas disponible le {d} ({plages})"
        for e in s["enseignants"]:
            assert not s["implantation"] or not ens[e].implantations or s["implantation"] in ens[e].implantations
    doublons = [k for k, n in occupe.items() if n > 1]
    assert not doublons, f"doubles réservations : {doublons[:5]}"
    for (gid, cid, _), n in charge_cours.items():
        rythme = cours[cid].periodes_semaine or ue_du_cours[cid].periodes_semaine
        assert not rythme or n <= rythme, f"rythme dépassé pour {gid}/{cid} : {n} > {rythme}"
    for (e, jour), lst in sites.items():
        lst.sort(key=lambda x: x[1])
        for (s1, _d1, f1), (s2, d2, _f2) in zip(lst, lst[1:]):
            if s1 != s2:
                assert et.multi_sites_meme_jour, f"{e} sur deux implantations le {jour}"
                assert d2 - f1 >= et.delai_deplacement_min, f"{e} : déplacement trop court le {jour}"

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
        u, w = (s["groupe"], s["cours"], s["enseignant"], s["distanciel"]), (s["jour"], s["debut_periode"], s["periodes"])
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
    synth = {(l["groupe"], l["cours"], l["enseignant"]): l for l in resultat["synthese"] if not l["distanciel"]}
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
    with pytest.raises(ValueError, match="UE inconnue"):
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


# ---------------------------------------------------------------------------- paramétrage d'établissement
def petit_jeu(**modifs):
    """Deux implantations, un enseignant partagé : de quoi tester déplacements, blocs, distanciel, contrats."""
    brut = {
        "nom": "Petit",
        "etablissement": {"implantations": [{"id": "X"}, {"id": "Y"}], "delai_deplacement_min": 90},
        "calendrier": {"date_debut": "2027-01-04", "date_fin": "2027-03-26", "jours_cours": ["lun", "mar"],
                       "creneaux": [{"id": "matin", "heure_debut": "08:30", "heure_fin": "12:00", "periodes": 4},
                                    {"id": "aprem", "heure_debut": "13:00", "heure_fin": "16:20", "periodes": 4}]},
        "locaux": [{"id": "LX", "implantation": "X", "capacite": 20}, {"id": "LY", "implantation": "Y", "capacite": 20}],
        "enseignants": [{"id": "T"}, {"id": "U"}],
        "modules": [{"id": "MA", "cours": [{"id": "CA", "periodes": 16, "attributions": [{"enseignants": ["T"], "periodes": 16}]}]},
                    {"id": "MB", "cours": [{"id": "CB", "periodes": 16, "attributions": [{"enseignants": ["T"], "periodes": 16}]}]}],
        "groupes": [{"id": "GA", "effectif": 10, "implantation": "X", "modules": ["MA"], "creneaux": ["matin"]},
                    {"id": "GB", "effectif": 10, "implantation": "Y", "modules": ["MB"], "creneaux": ["aprem"]}],
    }
    for chemin, valeur in modifs.items():
        cible = brut
        *debut, fin = chemin.split(".")
        for k in debut:
            cible = cible[int(k)] if k.isdigit() else cible[k]
        cible[int(fin) if fin.isdigit() else fin] = valeur
    return JeuDeDonnees.model_validate(brut)


def _planifier(ds, duree=15):
    r = planifier(ds, Parametres(duree_max_s=duree)).en_dict()
    assert r["statut"] in ("OPTIMAL", "REALISABLE")
    verifier_coherence(ds, r)
    return r


def test_deplacement_entre_implantations():
    r = _planifier(petit_jeu())
    assert sum(l["periodes_manquantes"] for l in r["synthese"]) == 0
    par_jour = defaultdict(set)
    for s in r["seances"]:
        par_jour[s["date"]].add(s["implantation"])
    assert all(len(v) == 1 for v in par_jour.values()), "délai de 90 min : jamais deux sites le même jour"


def test_un_seul_site_par_jour():
    ds = petit_jeu(**{"etablissement.multi_sites_meme_jour": False, "etablissement.delai_deplacement_min": 0})
    r = _planifier(ds)
    assert all(len({s["implantation"] for s in r["seances"] if s["date"] == d}) == 1 for d in {s["date"] for s in r["seances"]})


def test_enseignant_limite_a_une_implantation():
    ds = petit_jeu(**{"enseignants.0.implantations": ["X"]})
    assert any("n'enseigne pas sur l'implantation Y" in d["message"] for d in avant_planification(ds))
    r = _planifier(ds)
    manque = {l["cours"]: l["periodes_manquantes"] for l in r["synthese"]}
    assert manque == {"CA": 0, "CB": 16}


def test_blocs_de_2_et_journee_complete():
    ds = petit_jeu(**{"modules.0.cours.0.bloc": "2", "modules.1.cours.0.bloc": "8", "groupes.1.creneaux": []})
    r = _planifier(ds)
    assert sum(l["periodes_manquantes"] for l in r["synthese"]) == 0
    assert {s["periodes"] for s in r["seances"] if s["cours"] == "CA"} == {2}
    journees = [s for s in r["seances"] if s["cours"] == "CB"]
    assert journees and all(s["periodes"] == 8 and s["heure_debut"] == "08:30" and s["heure_fin"] == "16:20" for s in journees)


def test_cours_hybride():
    ds = petit_jeu(**{"modules.0.cours.0.periodes_distanciel": 8})
    r = _planifier(ds)
    dist = [s for s in r["seances"] if s["cours"] == "CA" and s["distanciel"]]
    assert sum(s["periodes"] for s in dist) == 8 and all(s["local"] == "Distanciel" for s in dist)
    assert sum(s["periodes"] for s in r["seances"] if s["cours"] == "CA" and not s["distanciel"]) == 8


def test_contrat_et_conges():
    ds = petit_jeu(**{"enseignants.0.date_fin": "2027-02-15",
                      "calendrier.fermetures": [{"date_debut": "2027-01-11", "date_fin": "2027-01-22", "type": "conges"}]})
    r = _planifier(ds)
    assert all(s["date"] <= "2027-02-15" for s in r["seances"])
    assert not any("2027-01-11" <= s["date"] <= "2027-01-22" for s in r["seances"])
    ouvert = petit_jeu(**{"calendrier.cours_pendant_conges": True,
                          "calendrier.fermetures": [{"date_debut": "2027-01-11", "date_fin": "2027-01-22", "type": "conges"},
                                                    {"date_debut": "2027-01-18", "date_fin": "2027-01-18", "type": "ferie"}]})
    assert not ouvert.calendrier.ferme(date(2027, 1, 12)) and ouvert.calendrier.ferme(date(2027, 1, 18))


def test_jours_speciaux():
    speciaux = [
        {"date_debut": "2027-01-05", "date_fin": "2027-01-05", "motif": "Formation continue", "type": "special"},
        {"date_debut": "2027-01-11", "date_fin": "2027-01-11", "motif": "Portes ouvertes", "type": "special", "implantations": ["X"]},
        {"date_debut": "2027-01-12", "date_fin": "2027-01-12", "motif": "Fête de l'école", "type": "special", "creneaux": ["aprem"]},
    ]
    # « cours pendant les congés » ne rouvre pas les jours spéciaux
    ds = petit_jeu(**{"calendrier.fermetures": speciaux, "calendrier.cours_pendant_conges": True})
    cal = ds.calendrier
    assert cal.ferme(date(2027, 1, 5)) and not cal.ferme(date(2027, 1, 11)) and not cal.ferme(date(2027, 1, 12))
    assert cal.ferme_pour(date(2027, 1, 11), "matin", "X") and not cal.ferme_pour(date(2027, 1, 11), "matin", "Y")
    assert cal.ferme_pour(date(2027, 1, 12), "aprem", "Y") and not cal.ferme_pour(date(2027, 1, 12), "matin", "X")
    r = _planifier(ds)  # verifier_coherence contrôle chaque séance contre les fermetures partielles
    assert sum(l["periodes_manquantes"] for l in r["synthese"]) == 0
    assert not any(s["date"] == "2027-01-05" for s in r["seances"])
    assert not any(s["date"] == "2027-01-11" and s["groupe"] == "GA" for s in r["seances"])
    assert not any(s["date"] == "2027-01-12" and s["groupe"] == "GB" for s in r["seances"])
    with pytest.raises(ValueError, match="créneau inconnu"):
        petit_jeu(**{"calendrier.fermetures": [{"date_debut": "2027-01-05", "date_fin": "2027-01-05", "creneaux": ["nuit"]}]})
    with pytest.raises(ValueError, match="implantation inconnue"):
        petit_jeu(**{"calendrier.fermetures": [{"date_debut": "2027-01-05", "date_fin": "2027-01-05", "implantations": ["Z"]}]})


def test_disponibilites_des_locaux():
    # LX n'est disponible que le lundi : le groupe GA (site X) n'a cours que le lundi
    ds = petit_jeu(**{"locaux.0.disponibilites": ["lun:*"]})
    r = _planifier(ds)  # verifier_coherence contrôle la disponibilité de chaque local utilisé
    assert sum(l["periodes_manquantes"] for l in r["synthese"]) == 0
    assert {s["jour"] for s in r["seances"] if s["groupe"] == "GA"} == {"lun"}
    # LY n'est disponible que le mardi matin, or GB n'a cours que l'après-midi : le diagnostic l'explique
    ds = petit_jeu(**{"locaux.1.disponibilites": ["mar:matin"]})
    msg = [d for d in avant_planification(ds) if d["sujet"].startswith("GB")]
    assert msg and "locaux compatibles" in msg[0]["message"] and "LY" in msg[0]["suggestion"]
    r = planifier(ds, Parametres(duree_max_s=10)).en_dict()
    verifier_coherence(ds, r)
    assert {l["cours"]: l["periodes_manquantes"] for l in r["synthese"]} == {"CA": 0, "CB": 16}
    with pytest.raises(ValueError, match="créneau inconnu"):
        petit_jeu(**{"locaux.0.disponibilites": ["lun:nuit"]})


def test_parametres_d_ue_herites_par_les_cours():
    # UE MA : dates, implantation Y et rythme de 4 p./semaine, hérités par le cours CA
    ds = petit_jeu(**{"modules.0.date_debut": "2027-02-01", "modules.0.date_fin": "2027-03-19",
                      "modules.0.implantation": "Y", "modules.0.periodes_semaine": 4,
                      "groupes.1.modules": [], "etablissement.delai_deplacement_min": 0})
    r = _planifier(ds)  # verifier_coherence : dates, implantation et rythme de chaque séance
    ca = [s for s in r["seances"] if s["cours"] == "CA"]
    assert sum(s["periodes"] for s in ca) == 16
    assert all("2027-02-01" <= s["date"] <= "2027-03-19" and s["implantation"] == "Y" and s["local"] == "LY" for s in ca)
    semaines = defaultdict(int)
    for s in ca:
        semaines[date.fromisoformat(s["date"]).isocalendar()[1]] += s["periodes"]
    assert max(semaines.values()) <= 4 and len(semaines) >= 4
    # le cours peut préciser les siens : ils l'emportent sur ceux de l'UE
    ds = petit_jeu(**{"modules.0.date_debut": "2027-02-01", "modules.0.implantation": "Y", "modules.0.periodes_semaine": 4,
                      "modules.0.cours.0.date_debut": "2027-01-04", "modules.0.cours.0.implantation": "X",
                      "modules.0.cours.0.periodes_semaine": 8, "groupes.1.modules": []})
    r = _planifier(ds)
    ca = [s for s in r["seances"] if s["cours"] == "CA"]
    assert all(s["implantation"] == "X" for s in ca) and min(s["date"] for s in ca) < "2027-02-01"
    # diagnostic : rythme plus petit qu'une séance, ou trop lent pour la période
    trop_petit = petit_jeu(**{"modules.0.periodes_semaine": 2})
    assert any("plus petit qu'une séance" in d["message"] for d in avant_planification(trop_petit))
    trop_lent = petit_jeu(**{"modules.0.periodes_semaine": 4, "modules.0.date_fin": "2027-01-17"})
    assert any("rythme de 4 p./semaine trop lent" in d["message"] for d in avant_planification(trop_lent))
    with pytest.raises(ValueError, match="implantation inconnue"):
        petit_jeu(**{"modules.0.implantation": "Z"})


def test_plage_du_samedi():
    ds = petit_jeu(**{"calendrier.jours_cours": ["lun", "mar", "sam"],
                      "calendrier.creneaux": [{"id": "matin", "heure_debut": "08:30", "heure_fin": "12:00", "periodes": 4, "jours": ["lun", "mar"]},
                                              {"id": "aprem", "heure_debut": "13:00", "heure_fin": "16:20", "periodes": 4},
                                              {"id": "sam", "heure_debut": "08:00", "heure_fin": "12:10", "periodes": 5, "jours": ["sam"]}],
                      "groupes.0.creneaux": ["sam"], "modules.0.cours.0.periodes": 15,
                      "modules.0.cours.0.attributions.0.periodes": 15})
    r = _planifier(ds)
    ca = [s for s in r["seances"] if s["cours"] == "CA"]
    assert ca and all(s["jour"] == "sam" and s["periodes"] == 5 for s in ca)


def test_calendrier_officiel_fwb():
    from app.calendrier_fwb import calendrier_fwb, paques
    assert paques(2027) == date(2027, 3, 28) and paques(2028) == date(2028, 4, 16)
    publies = {  # dates publiées par la FWB (lundi → vendredi)
        2025: ("2025-08-25", "2026-07-03", [("2025-10-20", "2025-10-31"), ("2025-12-22", "2026-01-02"),
                                            ("2026-02-16", "2026-02-27"), ("2026-04-27", "2026-05-08")]),
        2026: ("2026-08-24", "2027-07-02", [("2026-10-19", "2026-10-30"), ("2026-12-21", "2027-01-01"),
                                            ("2027-02-22", "2027-03-05"), ("2027-04-26", "2027-05-07")]),
        2027: ("2027-08-30", "2028-07-07", [("2027-10-25", "2027-11-05"), ("2027-12-27", "2028-01-07"),
                                            ("2028-02-28", "2028-03-10"), ("2028-05-01", "2028-05-12")]),
    }
    for an, (debut, fin, conges) in publies.items():
        c = calendrier_fwb(an)
        assert (c["date_debut"], c["date_fin"], c["provisoire"]) == (debut, fin, False)
        assert [(f["date_debut"], f["date_fin"]) for f in c["fermetures"] if f["type"] == "conges"] == conges
    feries = {f["date_debut"]: f["motif"] for f in calendrier_fwb(2026)["fermetures"] if f["type"] == "ferie"}
    assert set(feries) == {"2026-11-02", "2026-11-11", "2027-03-29", "2027-05-17"}  # 27/9 = dimanche, Ascension en congé
    assert calendrier_fwb(2030)["provisoire"]
    # le calendrier produit est accepté tel quel par le modèle
    c = calendrier_fwb(2026)
    JeuDeDonnees.model_validate({"calendrier": {"date_debut": c["date_debut"], "date_fin": c["date_fin"], "fermetures": c["fermetures"],
                                                "creneaux": [{"id": "m", "heure_debut": "08:30", "heure_fin": "12:10", "periodes": 4}]}})
