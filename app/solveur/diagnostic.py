"""Diagnostic lisible des données, avant et après planification.

Le but est d'expliquer au planificateur, en français, *pourquoi* un cours ne peut pas
être placé et *quoi changer*, plutôt qu'un simple « pas de solution ».
"""
from __future__ import annotations

from collections import defaultdict

from ..modeles import JeuDeDonnees
from .moteur import Horizon, Unite, construire_unites, dispo_hebdo, locaux_eligibles


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


def capacite_unite(hz: Horizon, u: Unite, avec_enseignants: bool = True) -> int:
    """Périodes que l'unité pourrait recevoir si elle était seule (sans concurrence)."""
    par_semaine = defaultdict(list)
    for ch in hz.creneaux_hebdo:
        dispo = hz.dispo_unite(u, ch, avec_enseignants)
        if dispo:
            for t, ok in enumerate(dispo):
                if ok:
                    par_semaine[t].append(ch.creneau.periodes)
    k = u.cours.max_seances_semaine
    return sum(sum(sorted(v, reverse=True)[:k] if k else v) for v in par_semaine.values())


def avant_planification(ds: JeuDeDonnees) -> list[dict]:
    out: list[dict] = []
    hz = Horizon(ds)
    if not any(any(hz.base_groupe(g, ch)) for g in ds.groupes for ch in hz.creneaux_hebdo if hz.groupe_autorise(g, ch)):
        return [_msg("erreur", "calendrier", "Aucun créneau de cours : vérifiez dates, jours de cours et fermetures.")]

    for cyc in cycles_prerequis(ds):
        out.append(_msg("erreur", "prérequis", "Cycle de prérequis : " + " → ".join(cyc),
                        "Supprimez l'un des liens de prérequis du cycle."))

    unites = construire_unites(ds)
    par_groupe = defaultdict(list)
    for u in unites:
        par_groupe[u.groupe.id].append(u)

    for g in ds.groupes:
        dispo = sum(ch.creneau.periodes * sum(hz.base_groupe(g, ch)) for ch in hz.creneaux_hebdo if hz.groupe_autorise(g, ch))
        requis = sum(ds.module(m).periodes for m in g.modules)
        if requis > dispo:
            out.append(_msg("erreur", f"groupe {g.id}",
                            f"{requis} périodes à suivre mais seulement {dispo} périodes de cours possibles "
                            "(après stages, congés et jours de cours).",
                            "Prolongez la période, ajoutez des jours/créneaux de cours ou réduisez le parcours."))
        elif requis > 0.9 * dispo:
            out.append(_msg("avertissement", f"groupe {g.id}",
                            f"Parcours très chargé : {requis} périodes pour {dispo} disponibles "
                            f"({100 * requis // max(dispo, 1)} %). Des cours risquent de ne pas trouver de place."))

    deja = set()
    for u in unites:
        sujet = f"{u.groupe.id} / {u.cours.id}" + (f" ({' + '.join(u.enseignants)})" if len(u.cours.attributions_pour(u.groupe.id)) > 1 else "")
        cle_local = (u.groupe.id, u.cours.id)
        if not locaux_eligibles(ds, u.groupe, u.cours):
            if cle_local not in deja:
                deja.add(cle_local)
                besoin = f"capacité ≥ {u.groupe.effectif}" + (f", équipements {', '.join(u.cours.equipements)}" if u.cours.equipements else "")
                out.append(_msg("erreur", sujet, f"Aucun local compatible ({besoin}).",
                                "Ajoutez un local adapté, un équipement, ou scindez le groupe."))
            continue
        for r in u.cours.ressources:
            if next(x for x in ds.ressources if x.id == r).quantite == 0:
                out.append(_msg("erreur", sujet, f"Le matériel « {r} » est en quantité nulle."))
        cap = capacite_unite(hz, u)
        if cap < u.periodes:
            sans_ens = capacite_unite(hz, u, avec_enseignants=False)
            if sans_ens >= u.periodes:
                cause = "disponibilités communes de l'enseignant" if len(u.enseignants) == 1 else \
                        "disponibilités communes des enseignants (co-enseignement)"
                conseil = "Élargissez les disponibilités ou confiez une partie du cours à un autre enseignant."
            else:
                cause = "jours/créneaux et période du groupe"
                conseil = "Élargissez les jours/créneaux du groupe, prolongez la période ou augmentez le rythme hebdomadaire autorisé."
            out.append(_msg("erreur", sujet, f"Seulement {cap} périodes possibles pour {u.periodes} requises "
                                             f"(facteur limitant : {cause}).", conseil))

    # charge des enseignants
    charge = defaultdict(int)
    for u in unites:
        for e in u.enseignants:
            charge[e] += u.periodes
    for e in ds.enseignants:
        if not charge[e.id]:
            continue
        dispo = 0
        for ch in hz.creneaux_hebdo:
            if dispo_hebdo(e, ch.jour, ch.creneau.id):
                dispo += ch.creneau.periodes * sum(
                    1 for t in range(hz.T) if hz._ouvert[ch.w][t]
                    and not any(i.bloque(hz.date(t, ch), ch.creneau.id) for i in e.indisponibilites))
        if charge[e.id] > dispo:
            out.append(_msg("erreur", f"enseignant {e.id}",
                            f"{charge[e.id]} périodes attribuées pour {dispo} périodes de disponibilité sur la période.",
                            "Répartissez certains cours sur un autre enseignant ou élargissez ses disponibilités."))
        elif charge[e.id] > 0.85 * dispo:
            out.append(_msg("avertissement", f"enseignant {e.id}",
                            f"Enseignant très chargé : {charge[e.id]} périodes attribuées pour {dispo} disponibles."))
        if e.max_periodes_semaine is not None and charge[e.id] > e.max_periodes_semaine * hz.T:
            out.append(_msg("erreur", f"enseignant {e.id}",
                            f"Plafond hebdomadaire ({e.max_periodes_semaine} p.) insuffisant pour {charge[e.id]} périodes "
                            f"sur {hz.T} semaines."))
    return out


def apres_planification(ds: JeuDeDonnees, resultat: dict) -> list[dict]:
    """Explique les cours incomplets après résolution."""
    out = []
    hz = Horizon(ds)
    unites = {(u.groupe.id, u.cours.id, u.enseignants): u for u in construire_unites(ds)}
    for ligne in resultat.get("synthese", []):
        if ligne["periodes_manquantes"] <= 0:
            continue
        u = unites[(ligne["groupe"], ligne["cours"], tuple(ligne["enseignants"]))]
        cap = capacite_unite(hz, u)
        cause = (
            "les créneaux compatibles sont insuffisants" if cap < u.periodes
            else "les créneaux compatibles sont pris par d'autres cours (enseignant, local, groupe ou matériel) "
                 "ou bloqués par l'ordre des prérequis"
        )
        out.append(_msg(
            "erreur", f"{u.groupe.id} / {u.cours.id} ({ligne['enseignant']})",
            f"{ligne['periodes_manquantes']} périodes sur {u.periodes} n'ont pas pu être planifiées : {cause}.",
            "Ajoutez un enseignant, élargissez les disponibilités, prolongez la période ou assouplissez les prérequis.",
        ))
    return out
