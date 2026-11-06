"""Heuristique constructive : un premier horaire complet et cohérent, construit rapidement.

Elle place les cours un par un (modules dans l'ordre des prérequis, cours les plus contraints
d'abord), au plus tôt, en respectant toutes les contraintes dures. Le résultat sert de point de
départ (« indice ») au solveur CP-SAT, qui l'améliore ensuite. Il sert aussi de solution de repli.
"""
from __future__ import annotations

from collections import defaultdict
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING

from ..modeles import JeuDeDonnees

if TYPE_CHECKING:
    from .moteur import Horizon, Unite


def construire(hz: Horizon, ds: JeuDeDonnees, unites: list[Unite], eligibles: dict, candidats: dict) -> tuple[dict, dict]:
    """Retourne ({(u.idx, w): (debut, fin_exclusive, local)}, {(groupe, module): rang de début au plus tôt}).

    Rang = semaine × W + créneau hebdomadaire (ordre chronologique des créneaux).

    `candidats[u.idx]` : liste des (créneau hebdo, dispo) envisageables pour l'unité.
    """
    T = hz.T
    W = len(hz.creneaux_hebdo)
    locaux = {l.id: l for l in ds.locaux}
    ens = {e.id: e for e in ds.enseignants}
    res_q = {r.id: r.quantite for r in ds.ressources}

    occ_groupe = defaultdict(set)  # (g, w) -> semaines
    occ_ens = defaultdict(set)  # (e, w) -> semaines
    occ_local = defaultdict(set)  # (local, w) -> semaines
    charge_ens = defaultdict(int)  # (e, semaine) -> périodes
    occ_res = defaultdict(int)  # (r, w, semaine) -> nb
    for l in ds.locaux:
        for ch in hz.creneaux_hebdo:
            occ_local[(l.id, ch.w)].update(hz.local_bloque(l, ch))

    fin_module: dict[tuple[str, str], int] = {}  # rang de la dernière séance
    solution: dict = {}

    # rang de chaque module dans l'ordre des prérequis de son groupe
    rang = {}
    for g in ds.groupes:
        graphe = {m: [p for p in ds.module(m).prerequis if p in g.modules] for m in g.modules}
        try:
            ordre = list(TopologicalSorter(graphe).static_order())
        except CycleError:  # cycle : signalé par le diagnostic
            ordre = list(g.modules)
        niveaux = {}
        for m in ordre:
            niveaux[m] = 1 + max((niveaux[p] for p in graphe[m] if p in niveaux), default=-1)
        for m, n in niveaux.items():
            rang[(g.id, m)] = n

    def liberte(u: Unite) -> int:
        return sum(sum(d) for _, d in candidats.get(u.idx, []))

    ordre_unites = sorted(unites, key=lambda u: (rang[(u.groupe.id, u.module.id)], liberte(u) / max(u.periodes, 1)))

    def semaine_libre(u: Unite, ch, t: int, local: str | None) -> bool:
        if t in occ_groupe[(u.groupe.id, ch.w)]:
            return False
        pw = ch.creneau.periodes
        for e in u.enseignants:
            if t in occ_ens[(e, ch.w)]:
                return False
            m = ens[e].max_periodes_semaine
            if m is not None and charge_ens[(e, t)] + pw > m:
                return False
        for r in u.cours.ressources:
            if occ_res[(r, ch.w, t)] >= res_q[r]:
                return False
        return local is None or t not in occ_local[(local, ch.w)]

    debut_module: dict[tuple[str, str], int] = {}
    for u in ordre_unites:
        k = (u.groupe.id, u.module.id)
        rang_min = max((fin_module.get((u.groupe.id, p), -1) + 1 for p in u.module.prerequis if p in u.groupe.modules),
                       default=0)
        debut_module[k] = max(debut_module.get(k, 0), rang_min)
        reste = u.periodes
        max_creneaux = u.cours.max_seances_semaine or len(candidats.get(u.idx, []))
        options = list(candidats.get(u.idx, []))
        utilises, fins = 0, []
        while reste > 0 and options and utilises < max_creneaux:
            meilleur = None
            for ch, dispo in options:
                pw = ch.creneau.periodes
                s_min = max(0, -(-(rang_min - ch.w) // W))  # première semaine où le rang ≥ rang_min
                debut = next((t for t in range(s_min, T) if dispo[t] and semaine_libre(u, ch, t, None)), None)
                if debut is None:
                    continue
                for r in sorted(eligibles[u.idx], key=lambda r: (r != u.groupe.local_prefere, locaux[r].capacite)):
                    if not semaine_libre(u, ch, debut, r):
                        continue
                    t, n, dernier = debut, 0, debut
                    while t < T and n * pw < reste and semaine_libre(u, ch, t, r):
                        if dispo[t]:
                            n += 1
                            dernier = t
                        t += 1
                    # priorité : terminer le cours, puis finir au plus tôt, puis commencer au plus tôt
                    cle = (n * pw < reste, dernier, debut)
                    if n and (meilleur is None or cle < meilleur[0]):
                        meilleur = (cle, ch, debut, dernier + 1, r, n)
                    break
            if not meilleur:
                break
            _, ch, s, e, r, n = meilleur
            solution[(u.idx, ch.w)] = (s, e, r)
            fins.append((e - 1) * W + ch.w)
            for t in range(s, e):
                occ_groupe[(u.groupe.id, ch.w)].add(t)
                occ_local[(r, ch.w)].add(t)
                for en in u.enseignants:
                    occ_ens[(en, ch.w)].add(t)
                    charge_ens[(en, t)] += ch.creneau.periodes
                for rs in u.cours.ressources:
                    occ_res[(rs, ch.w, t)] += 1
            reste -= n * ch.creneau.periodes
            utilises += 1
            options = [(c, d) for c, d in options if c.w != ch.w]
        fin_module[k] = max([fin_module.get(k, rang_min - 1)] + fins)
    return solution, debut_module
