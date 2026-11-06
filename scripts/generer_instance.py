"""Génère un établissement fictif réaliste pour tester le moteur à grande échelle.

Usage :
    python -m scripts.generer_instance --groupes 100 --enseignants 200 --modules 500 --sortie grand.json
    python -m scripts.generer_instance ... --planifier 600     # lance aussi le calcul (600 s max)
"""
from __future__ import annotations

import argparse
import json
import random
import time
from datetime import date

FERMETURES = [
    ("2026-10-19", "2026-10-30", "Vacances d'automne"), ("2026-11-11", "2026-11-11", "Armistice"),
    ("2026-12-21", "2027-01-01", "Vacances d'hiver"), ("2027-02-15", "2027-02-26", "Vacances de détente"),
    ("2027-03-29", "2027-03-29", "Lundi de Pâques"), ("2027-04-26", "2027-05-07", "Vacances de printemps"),
    ("2027-05-17", "2027-05-17", "Lundi de Pentecôte"),
]

DOMAINES = {  # domaine : équipement requis pour une partie des cours
    "compta": None, "langues": None, "secretariat": "pc", "informatique": "pc", "soins": "lit_medicalise",
    "cuisine": "cuisine", "electricite": "atelier_elec", "management": None, "petite_enfance": None, "graphisme": "pc",
}
JOURS_SEM = ["lun", "mar", "mer", "jeu", "ven"]


def generer(nb_groupes=100, nb_enseignants=200, nb_modules=500, graine=1) -> dict:
    rnd = random.Random(graine)
    domaines = list(DOMAINES)

    # --- locaux
    locaux = []
    for i in range(int(nb_groupes * 0.55)):
        locaux.append({"id": f"S{i + 1:03d}", "nom": f"Salle {i + 1}", "capacite": rnd.choice([16, 20, 24, 24, 30, 36]),
                       "equipements": ["projecteur"]})
    speciaux = {"pc": max(3, nb_groupes // 10), "lit_medicalise": max(1, nb_groupes // 40),
                "cuisine": max(1, nb_groupes // 50), "atelier_elec": max(1, nb_groupes // 50)}
    for eq, n in speciaux.items():
        for i in range(n):
            locaux.append({"id": f"{eq[:4].upper()}{i + 1}", "nom": f"{eq} {i + 1}", "capacite": rnd.choice([24, 24, 30]),
                           "equipements": [eq, "projecteur"]})

    # --- groupes : régime soir / jour / samedi
    groupes = []
    for i in range(nb_groupes):
        dom = rnd.choice(domaines)
        regime = rnd.choices(["soir", "jour", "samedi"], [60, 30, 10])[0]
        if regime == "soir":
            jours, creneaux = sorted(rnd.sample(JOURS_SEM, rnd.choice([3, 4, 4, 5])), key=JOURS_SEM.index), ["soir"]
        elif regime == "jour":
            jours, creneaux = sorted(rnd.sample(JOURS_SEM, rnd.choice([3, 4, 5])), key=JOURS_SEM.index), ["matin", "aprem"]
        else:
            jours, creneaux = ["sam"], ["matin", "aprem"]
        g = {"id": f"G{i + 1:03d}", "nom": f"{dom} {regime} {i + 1}", "effectif": rnd.randint(8, 24),
             "jours_cours": jours, "creneaux": creneaux, "modules": [], "stages": [], "_dom": dom}
        if regime == "jour" and rnd.random() < 0.5:
            g["stages"].append({"date_debut": "2027-03-08", "date_fin": "2027-03-19", "motif": "Stage"})
        groupes.append(g)

    # --- enseignants : spécialistes d'un ou deux domaines
    enseignants = []
    for i in range(nb_enseignants):
        doms = rnd.sample(domaines, rnd.choice([1, 1, 2]))
        regime = rnd.choices(["soir", "jour", "mixte"], [50, 30, 20])[0]
        dispos = []
        jours = rnd.sample(JOURS_SEM, rnd.choice([2, 3, 4, 5]))
        for j in jours:
            if regime in ("soir", "mixte"):
                dispos.append(f"{j}:soir")
            if regime in ("jour", "mixte"):
                dispos += [f"{j}:matin", f"{j}:aprem"]
        if rnd.random() < 0.25:
            dispos += ["sam:matin", "sam:aprem"]
        e = {"id": f"E{i + 1:03d}", "nom": f"Enseignant {i + 1}", "disponibilites": dispos, "_doms": doms, "_charge": 0}
        if rnd.random() < 0.15:
            e["max_periodes_semaine"] = rnd.choice([8, 12, 16])
        if rnd.random() < 0.1:
            e["indisponibilites"] = [{"date_debut": "2027-01-18", "date_fin": "2027-01-22", "motif": "Formation"}]
        enseignants.append(e)

    def compatibles(g, n_slots_min=1):
        slots = {f"{j}:{c}" for j in g["jours_cours"] for c in g["creneaux"]}
        res = []
        for e in enseignants:
            if g["_dom"] not in e["_doms"]:
                continue
            communs = slots & set(e["disponibilites"])
            if len(communs) >= n_slots_min:
                res.append(e)
        return res

    # --- modules (instances annuelles) répartis sur les groupes
    modules = []
    capacite_hebdo = {g["id"]: len(g["jours_cours"]) * len(g["creneaux"]) * 4 for g in groupes}
    nb_cours = 0
    for i in range(nb_modules):
        g = groupes[i % nb_groupes]
        budget = capacite_hebdo[g["id"]] * 30 * 0.65 - sum(m["_periodes"] for m in modules if m["_groupe"] == g["id"])
        n_cours = rnd.choices([1, 2, 3, 4], [30, 35, 25, 10])[0]
        cours = []
        for k in range(n_cours):
            periodes = rnd.choice([20, 20, 30, 40, 40, 60, 80])
            if periodes > budget:
                continue
            cand = compatibles(g) or compatibles({**g, "_dom": rnd.choice(domaines)})
            cand = sorted(cand, key=lambda e: e["_charge"] + rnd.random() * 20)
            if not cand:
                continue
            eq = DOMAINES[g["_dom"]] if rnd.random() < 0.5 else None
            type_attr = rnd.choices(["simple", "reparti", "co"], [80, 12, 8])[0]
            if type_attr != "simple" and len(cand) >= 2:
                e1, e2 = cand[0], cand[1]
                if type_attr == "reparti":
                    p1 = periodes // 2 if periodes >= 40 else periodes - 10
                    attrs = [{"enseignants": [e1["id"]], "periodes": p1}, {"enseignants": [e2["id"]], "periodes": periodes - p1}]
                    e1["_charge"] += p1
                    e2["_charge"] += periodes - p1
                else:
                    communs = set(e1["disponibilites"]) & set(e2["disponibilites"]) & {
                        f"{j}:{c}" for j in g["jours_cours"] for c in g["creneaux"]}
                    if len(communs) < 2:
                        attrs = [{"enseignants": [e1["id"]], "periodes": periodes}]
                        e1["_charge"] += periodes
                    else:
                        attrs = [{"enseignants": [e1["id"], e2["id"]], "periodes": periodes}]
                        e1["_charge"] += periodes
                        e2["_charge"] += periodes
            else:
                attrs = [{"enseignants": [cand[0]["id"]], "periodes": periodes}]
                cand[0]["_charge"] += periodes
            nb_cours += 1
            budget -= periodes
            cours.append({"id": f"C{nb_cours:05d}", "nom": f"Cours {nb_cours}", "periodes": periodes,
                          "equipements": [eq] if eq else [], "attributions": attrs,
                          **({"max_seances_semaine": 2} if rnd.random() < 0.3 else {})})
        if not cours:
            continue
        mods_g = [m for m in modules if m["_groupe"] == g["id"]]
        prerequis = [mods_g[-1]["id"]] if mods_g and rnd.random() < 0.4 else []
        modules.append({"id": f"M{i + 1:04d}", "nom": f"Module {i + 1}", "prerequis": prerequis, "cours": cours,
                        "_groupe": g["id"], "_periodes": sum(c["periodes"] for c in cours)})
        g["modules"].append(modules[-1]["id"])

    for m in modules:
        del m["_groupe"], m["_periodes"]
    for g in groupes:
        del g["_dom"]
    for e in enseignants:
        del e["_doms"], e["_charge"]
    groupes = [g for g in groupes if g["modules"]]
    return {
        "nom": f"Établissement fictif ({len(groupes)} groupes, {len(enseignants)} enseignants, {len(modules)} modules)",
        "calendrier": {
            "date_debut": "2026-09-14", "date_fin": "2027-06-25", "jours_cours": JOURS_SEM + ["sam"],
            "creneaux": [
                {"id": "matin", "libelle": "Matin", "heure_debut": "08:30", "heure_fin": "12:00", "periodes": 4},
                {"id": "aprem", "libelle": "Après-midi", "heure_debut": "13:00", "heure_fin": "16:20", "periodes": 4},
                {"id": "soir", "libelle": "Soir", "heure_debut": "18:00", "heure_fin": "21:20", "periodes": 4},
            ],
            "fermetures": [{"date_debut": a, "date_fin": b, "motif": m} for a, b, m in FERMETURES],
        },
        "locaux": locaux, "ressources": [], "enseignants": enseignants, "modules": modules, "groupes": groupes,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groupes", type=int, default=100)
    ap.add_argument("--enseignants", type=int, default=200)
    ap.add_argument("--modules", type=int, default=500)
    ap.add_argument("--graine", type=int, default=1)
    ap.add_argument("--sortie", default="")
    ap.add_argument("--planifier", type=float, default=0, help="durée max de calcul en secondes (0 = ne pas planifier)")
    a = ap.parse_args()
    brut = generer(a.groupes, a.enseignants, a.modules, a.graine)
    from app.modeles import JeuDeDonnees
    ds = JeuDeDonnees.model_validate(brut)
    nb_cours = sum(len(m.cours) for m in ds.modules)
    print(f"{ds.nom} — {nb_cours} cours, {sum(m.periodes for m in ds.modules)} périodes, {len(ds.locaux)} locaux")
    if a.sortie:
        with open(a.sortie, "w") as f:
            json.dump(ds.model_dump(mode="json"), f, ensure_ascii=False)
    if a.planifier:
        from app.solveur.diagnostic import avant_planification
        from app.solveur.moteur import Parametres, planifier
        diag = avant_planification(ds)
        print(f"Contrôle : {sum(d['niveau'] == 'erreur' for d in diag)} erreurs, "
              f"{sum(d['niveau'] == 'avertissement' for d in diag)} avertissements")
        t = time.monotonic()
        r = planifier(ds, Parametres(duree_max_s=a.planifier)).en_dict()
        manque = sum(l["periodes_manquantes"] for l in r["synthese"])
        requis = sum(l["periodes_requises"] for l in r["synthese"])
        print(f"Statut {r['statut']} en {time.monotonic() - t:.0f} s — {len(r['seances'])} séances — "
              f"{requis - manque}/{requis} périodes planifiées ({100 * (requis - manque) / requis:.2f} %)")
        print("Statistiques :", r["statistiques"])


if __name__ == "__main__":
    main()
