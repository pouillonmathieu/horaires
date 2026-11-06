from collections import defaultdict

import pytest

from app.exemple import EXEMPLE, jeu_exemple
from app.fichiers import donnees_vers_excel, excel_vers_donnees, horaire_vers_excel, horaire_vers_ics
from app.modeles import JOURS, JeuDeDonnees
from app.solveur.diagnostic import avant_planification, cycles_prerequis
from app.solveur.moteur import Parametres, planifier


@pytest.fixture(scope="module")
def ds():
    return jeu_exemple()


@pytest.fixture(scope="module")
def resultat(ds):
    return planifier(ds, Parametres(duree_max_s=30)).en_dict()


def test_solution_complete(resultat):
    assert resultat["statut"] in ("OPTIMAL", "REALISABLE")
    assert all(l["periodes_manquantes"] == 0 for l in resultat["synthese"])


def test_aucun_conflit(resultat):
    for cle in ("groupe", "local", "enseignant"):
        occupe = defaultdict(int)
        for s in resultat["seances"]:
            occupe[(s[cle], s["date"], s["creneau"])] += 1
        assert max(occupe.values()) == 1, f"double réservation {cle}"


def test_contraintes_respectees(ds, resultat):
    groupes = {g.id: g for g in ds.groupes}
    locaux = {l.id: l for l in ds.locaux}
    ens = {e.id: e for e in ds.enseignants}
    fermetures = ds.calendrier.fermetures
    from datetime import date
    for s in resultat["seances"]:
        d = date.fromisoformat(s["date"])
        g, m, l = groupes[s["groupe"]], ds.module(s["module"]), locaux[s["local"]]
        assert not any(f.contient(d) for f in fermetures), "cours un jour de fermeture"
        assert not any(st.bloque(d) for st in g.stages), "cours pendant un stage"
        assert l.capacite >= g.effectif
        assert set(m.equipements) <= set(l.equipements)
        assert s["enseignant"] in m.enseignants
        assert ens[s["enseignant"]].disponible(d, s["creneau"])
        assert not any(i.bloque(d, s["creneau"]) for i in l.indisponibilites)
        if g.creneaux:
            assert s["creneau"] in g.creneaux
        if g.jours_cours:
            assert JOURS[d.weekday()] in g.jours_cours


def test_prerequis(ds, resultat):
    bornes = {}
    for s in resultat["seances"]:
        k = (s["groupe"], s["module"])
        cle = (s["date"], s["heure_debut"])
        lo, hi = bornes.get(k, (cle, cle))
        bornes[k] = (min(lo, cle), max(hi, cle))
    for g in ds.groupes:
        for mid in g.modules:
            for pre in ds.module(mid).prerequis:
                assert bornes[(g.id, pre)][1] < bornes[(g.id, mid)][0], f"{pre} doit précéder {mid}"


def test_ressource_mobile_et_plafonds(ds, resultat):
    mannequin = defaultdict(int)
    for s in resultat["seances"]:
        if "mannequin" in ds.module(s["module"]).ressources:
            mannequin[(s["date"], s["creneau"])] += 1
    assert max(mannequin.values()) <= 1
    lambert = defaultdict(int)
    from datetime import date
    for s in resultat["seances"]:
        if s["enseignant"] == "LAMBERT":
            lambert[date.fromisoformat(s["date"]).isocalendar()[:2]] += s["periodes"]
    assert max(lambert.values()) <= 16


def test_manque_signale_sans_planter():
    brut = {**EXEMPLE, "modules": [dict(m) for m in EXEMPLE["modules"]]}
    brut["modules"][0]["periodes"] = 2000  # impossible à placer entièrement
    ds = JeuDeDonnees.model_validate(brut)
    assert any(d["niveau"] == "erreur" for d in avant_planification(ds))
    r = planifier(ds, Parametres(duree_max_s=20)).en_dict()
    assert r["statut"] in ("OPTIMAL", "REALISABLE")
    assert sum(l["periodes_manquantes"] for l in r["synthese"]) > 0


def test_validation_references():
    brut = {**EXEMPLE, "groupes": [{**EXEMPLE["groupes"][0], "modules": ["INEXISTANT"]}]}
    with pytest.raises(ValueError, match="module inconnu"):
        JeuDeDonnees.model_validate(brut)


def test_cycle_prerequis():
    brut = {**EXEMPLE, "modules": [dict(m) for m in EXEMPLE["modules"]]}
    brut["modules"][0]["prerequis"] = ["AS-SOINS"]
    assert cycles_prerequis(JeuDeDonnees.model_validate(brut))


def test_aller_retour_excel(ds, resultat):
    assert excel_vers_donnees(donnees_vers_excel(ds)) == ds
    assert horaire_vers_excel(resultat, "t")[:2] == b"PK"
    ics = horaire_vers_ics(resultat["seances"][:3], "x").decode()
    assert ics.count("BEGIN:VEVENT") == 3
