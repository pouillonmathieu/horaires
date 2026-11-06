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


def construire(hz: Horizon, ds: JeuDeDonnees, unites: list[Unite], eligibles: dict, candidats: dict,
               fixe: dict[int, bool], segments: int) -> tuple[dict, dict]:
    """Retourne ({(u.idx, w, segment): (debut, fin_exclusive, local)}, {(groupe, module): rang de début au plus tôt}).

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

    def semaine_libre(u: Unite, ch, t: int, local: str | None, deja: int = 0) -> bool:
        if t in occ_groupe[(u.groupe.id, ch.w)]:
            return False
        pw = ch.creneau.periodes
        for e in u.enseignants:
            if t in occ_ens[(e, ch.w)]:
                return False
            m = ens[e].max_periodes_semaine
            if m is not None and charge_ens[(e, t)] + deja + pw > m:
                return False
        for r in u.cours.ressources:
            if occ_res[(r, ch.w, t)] >= res_q[r]:
                return False
        return local is None or t not in occ_local[(local, ch.w)]

    def placer(u: Unite, ch, k: int, s: int, e: int, r: str):
        solution[(u.idx, ch.w, k)] = (s, e, r)
        for t in range(s, e):
            occ_groupe[(u.groupe.id, ch.w)].add(t)
            occ_local[(r, ch.w)].add(t)
            for en in u.enseignants:
                occ_ens[(en, ch.w)].add(t)
                charge_ens[(en, t)] += ch.creneau.periodes
            for rs in u.cours.ressources:
                occ_res[(rs, ch.w, t)] += 1

    def locaux_tries(u: Unite):
        return sorted(eligibles[u.idx], key=lambda r: (r != u.groupe.local_prefere, locaux[r].capacite))

    def premiere_semaine(ch, rang_min: int) -> int:
        return max(0, -(-(rang_min - ch.w) // W))  # première semaine où le rang ≥ rang_min

    def placer_souple(u: Unite, rang_min: int) -> list[int]:
        """Créneaux choisis un par un ; un même créneau peut reprendre après une pause (segments)."""
        reste, fins = u.periodes, []
        max_creneaux = u.cours.max_seances_semaine or len(candidats.get(u.idx, []))
        nb_seg, fin_w = defaultdict(int), {}
        options = list(candidats.get(u.idx, []))
        while reste > 0:
            utilises = sum(1 for w in nb_seg if nb_seg[w])
            meilleur = None
            for ch, dispo in options:
                if nb_seg[ch.w] >= segments or (not nb_seg[ch.w] and utilises >= max_creneaux):
                    continue
                pw = ch.creneau.periodes
                s_min = max(premiere_semaine(ch, rang_min), fin_w.get(ch.w, 0))
                debut = next((t for t in range(s_min, T) if dispo[t] and semaine_libre(u, ch, t, None)), None)
                if debut is None:
                    continue
                for r in locaux_tries(u):
                    if not semaine_libre(u, ch, debut, r):
                        continue
                    t, n, dernier = debut, 0, debut
                    while t < T and n * pw < reste and semaine_libre(u, ch, t, r):
                        if dispo[t]:
                            n += 1
                            dernier = t
                        t += 1
                    # priorité : terminer le cours, puis finir au plus tôt, puis éviter les reprises, puis commencer tôt
                    cle = (n * pw < reste, dernier, nb_seg[ch.w], debut)
                    if n and (meilleur is None or cle < meilleur[0]):
                        meilleur = (cle, ch, debut, dernier + 1, r, n)
                    break
            if not meilleur:
                break
            _, ch, s, e, r, n = meilleur
            placer(u, ch, nb_seg[ch.w], s, e, r)
            nb_seg[ch.w] += 1
            fin_w[ch.w] = e
            fins.append((e - 1) * W + ch.w)
            reste -= n * ch.creneau.periodes
        return fins

    def placer_fixe(u: Unite, rang_min: int) -> list[int]:
        """Un ou deux créneaux hebdomadaires qui démarrent ensemble et s'arrêtent ensemble."""
        options = sorted(candidats.get(u.idx, []), key=lambda o: o[0].w)
        max_creneaux = min(2, u.cours.max_seances_semaine or 2)
        ensembles = [[o] for o in options]
        if max_creneaux >= 2:
            ensembles += [[a, b] for i, a in enumerate(options) for b in options[i + 1:]]
        meilleur = None
        for ens_ in ensembles:
            s0 = max(premiere_semaine(ch, rang_min) for ch, _ in ens_)
            for s in range(s0, T):
                if all(d[s] and semaine_libre(u, ch, s, None) for ch, d in ens_):
                    break
            else:
                continue
            salles = []
            for ch, _ in ens_:
                r = next((r for r in locaux_tries(u) if semaine_libre(u, ch, s, r)), None)
                if r is None:
                    break
                salles.append(r)
            if len(salles) != len(ens_):
                continue
            reste, dernier, t, arret = u.periodes, {}, s, False
            while t < T and reste > 0 and not arret:
                deja = 0
                for (ch, d), r in zip(ens_, salles):
                    if reste <= 0:
                        break
                    if not semaine_libre(u, ch, t, r, deja):
                        arret = True
                        break
                    if d[t]:
                        dernier[ch.w] = t
                        reste -= ch.creneau.periodes
                        deja += ch.creneau.periodes
                t += 1
            if len(dernier) != len(ens_):
                continue
            fins_sem = [dernier[ch.w] + 1 for ch, _ in ens_]
            if min(fins_sem) < max(fins_sem) - 1:
                continue  # les créneaux ne finiraient pas ensemble
            rang_fin = max(dernier[ch.w] * W + ch.w for ch, _ in ens_)
            cle = (reste > 0, rang_fin, len(ens_), s)
            if meilleur is None or cle < meilleur[0]:
                meilleur = (cle, ens_, salles, s, fins_sem)
        if not meilleur:
            return []
        _, ens_, salles, s, fins_sem = meilleur
        for (ch, _), r, e in zip(ens_, salles, fins_sem):
            placer(u, ch, 0, s, e, r)
        return [(e - 1) * W + ch.w for (ch, _), e in zip(ens_, fins_sem)]

    debut_module: dict[tuple[str, str], int] = {}
    for u in ordre_unites:
        k = (u.groupe.id, u.module.id)
        rang_min = max((fin_module.get((u.groupe.id, p), -1) + 1 for p in u.module.prerequis if p in u.groupe.modules),
                       default=0)
        debut_module[k] = max(debut_module.get(k, 0), rang_min)
        fins = placer_fixe(u, rang_min) if fixe[u.idx] else placer_souple(u, rang_min)
        fin_module[k] = max([fin_module.get(k, rang_min - 1)] + fins)
    return solution, debut_module
