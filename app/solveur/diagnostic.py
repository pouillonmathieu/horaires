"""Diagnostic lisible des données, avant et après planification.

Le but est d'expliquer au planificateur, en français, *pourquoi* un module ne peut pas
être placé et *quoi changer* — plutôt qu'un simple « pas de solution ».
"""
from __future__ import annotations

from collections import defaultdict

from ..modeles import JeuDeDonnees
from .moteur import creneaux_dates, groupe_disponible, local_disponible, locaux_eligibles


def _msg(niveau: str, sujet: str, message: str, suggestion: str = "") -> dict:
    return {"niveau": niveau, "sujet": sujet, "message": message, "suggestion": suggestion}


def cycles_prerequis(ds: JeuDeDonnees) -> list[list[str]]:
    graphe = {m.id: m.prerequis for m in ds.modules}
    cycles, etat = [], {}

    def visite(u, chemin):
        etat[u] = 1
        for v in graphe.get(u, []):
            if etat.get(v) == 1:
                cycles.append(chemin[chemin.index(v):] + [v])
            elif v not in etat:
                visite(v, chemin + [v])
        etat[u] = 2

    for m in graphe:
        if m not in etat:
            visite(m, [m])
    return cycles


def capacite_module(ds: JeuDeDonnees, g, m, slots) -> tuple[int, dict]:
    """Périodes réellement disponibles pour (groupe, module) en ignorant la concurrence."""
    ens = [e for e in ds.enseignants if e.id in m.enseignants]
    locaux = locaux_eligibles(ds, g, m)
    raisons = defaultdict(int)
    total = 0
    for s in slots:
        if not groupe_disponible(g, s, ds.calendrier.jours_cours):
            raisons["groupe"] += 1
        elif not any(e.disponible(s.jour, s.creneau.id) for e in ens):
            raisons["enseignant"] += 1
        elif not any(local_disponible(l, s) for l in locaux):
            raisons["local"] += 1
        else:
            total += s.periodes
    return total, raisons


def avant_planification(ds: JeuDeDonnees) -> list[dict]:
    out: list[dict] = []
    slots = creneaux_dates(ds)
    if not slots:
        return [_msg("erreur", "calendrier", "Aucun créneau de cours : vérifiez dates, jours de cours et fermetures.")]

    for cyc in cycles_prerequis(ds):
        out.append(_msg("erreur", "prérequis", "Cycle de prérequis : " + " → ".join(cyc),
                        "Supprimez l'un des liens de prérequis du cycle."))

    for g in ds.groupes:
        dispo_groupe = sum(s.periodes for s in slots if groupe_disponible(g, s, ds.calendrier.jours_cours))
        requis_groupe = sum(ds.module(mid).periodes for mid in g.modules)
        if requis_groupe > dispo_groupe:
            out.append(_msg("erreur", f"groupe {g.id}",
                            f"{requis_groupe} périodes à suivre mais seulement {dispo_groupe} périodes de cours possibles "
                            "(après stages, congés et jours de cours).",
                            "Prolongez la période, ajoutez des jours/créneaux de cours ou réduisez le parcours."))
        elif requis_groupe > 0.9 * dispo_groupe:
            out.append(_msg("avertissement", f"groupe {g.id}",
                            f"Parcours très chargé : {requis_groupe} périodes pour {dispo_groupe} disponibles "
                            f"({100 * requis_groupe // max(dispo_groupe, 1)} %)."))

        for mid in g.modules:
            m = ds.module(mid)
            sujet = f"{g.id} / {m.id}"
            if not m.enseignants:
                out.append(_msg("erreur", sujet, "Aucun enseignant habilité.", "Renseignez la colonne « enseignants » du module."))
                continue
            if not locaux_eligibles(ds, g, m):
                besoin = f"capacité ≥ {g.effectif}" + (f", équipements {', '.join(m.equipements)}" if m.equipements else "")
                out.append(_msg("erreur", sujet, f"Aucun local compatible ({besoin}).",
                                "Ajoutez un local adapté, un équipement, ou scindez le groupe."))
                continue
            for r in m.ressources:
                if next(x for x in ds.ressources if x.id == r).quantite == 0:
                    out.append(_msg("erreur", sujet, f"Le matériel « {r} » est en quantité nulle."))
            dispo, raisons = capacite_module(ds, g, m, slots)
            if dispo < m.periodes:
                principale = max(raisons, key=raisons.get) if raisons else "groupe"
                conseil = {
                    "groupe": "Élargissez les jours/créneaux du groupe ou la période de formation.",
                    "enseignant": "Élargissez les disponibilités des enseignants habilités ou habilitez un autre enseignant.",
                    "local": "Libérez un local compatible ou levez ses indisponibilités.",
                }[principale]
                out.append(_msg("erreur", sujet,
                                f"Seulement {dispo} périodes possibles pour {m.periodes} requises "
                                f"(principal facteur limitant : {principale}).", conseil))

    # charge des enseignants : modules mono-enseignant
    charge = defaultdict(int)
    for g in ds.groupes:
        for mid in g.modules:
            m = ds.module(mid)
            if len(m.enseignants) == 1:
                charge[m.enseignants[0]] += m.periodes
    for e in ds.enseignants:
        if not charge[e.id]:
            continue
        dispo = sum(s.periodes for s in slots if e.disponible(s.jour, s.creneau.id))
        if charge[e.id] > dispo:
            out.append(_msg("erreur", f"enseignant {e.id}",
                            f"{charge[e.id]} périodes assignées d'office pour {dispo} périodes de disponibilité.",
                            "Habilitez un second enseignant sur certains modules ou élargissez ses disponibilités."))
        if e.max_periodes_semaine is not None:
            semaines = len({s.semaine for s in slots})
            if charge[e.id] > e.max_periodes_semaine * semaines:
                out.append(_msg("erreur", f"enseignant {e.id}",
                                f"Plafond hebdomadaire ({e.max_periodes_semaine} p.) insuffisant pour {charge[e.id]} périodes "
                                f"sur {semaines} semaines."))
    return out


def apres_planification(ds: JeuDeDonnees, resultat: dict) -> list[dict]:
    """Explique les modules incomplets après résolution."""
    out = []
    slots = creneaux_dates(ds)
    groupes = {g.id: g for g in ds.groupes}
    for ligne in resultat.get("synthese", []):
        if ligne["periodes_manquantes"] <= 0:
            continue
        g, m = groupes[ligne["groupe"]], ds.module(ligne["module"])
        dispo, _ = capacite_module(ds, g, m, slots)
        cause = (
            "les créneaux compatibles sont insuffisants" if dispo < m.periodes
            else "les créneaux compatibles sont déjà pris par d'autres cours (enseignant, local, groupe ou matériel) "
                 "ou bloqués par l'ordre des prérequis"
        )
        out.append(_msg(
            "erreur", f"{g.id} / {m.id}",
            f"{ligne['periodes_manquantes']} périodes sur {m.periodes} n'ont pas pu être planifiées : {cause}.",
            "Ajoutez un enseignant habilité, élargissez les disponibilités, prolongez la période "
            "ou assouplissez les prérequis.",
        ))
    return out
