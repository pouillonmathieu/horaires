"""Heuristique constructive : un premier horaire complet et cohérent, construit rapidement.

Elle place les cours un par un (modules dans l'ordre des prérequis, cours les plus contraints
d'abord), au plus tôt, en respectant toutes les contraintes dures. Le résultat sert de point de
départ (« indice ») au solveur CP-SAT, qui l'améliore ensuite. Il sert aussi de solution de repli.
"""
from __future__ import annotations

from collections import defaultdict
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING

from ..modeles import JOURS, JeuDeDonnees

if TYPE_CHECKING:
    from .moteur import Horizon, Unite


def construire(hz: Horizon, ds: JeuDeDonnees, unites: list[Unite], eligibles: dict, candidats: dict,
               fixe: dict[int, bool], segments: int) -> tuple[dict, dict]:
    """Retourne ({(u.idx, w, segment): (debut, fin_exclusive, local)}, {(groupe, module): rang de début au plus tôt}).

    Rang = semaine × W + créneau hebdomadaire (ordre chronologique des créneaux).

    `candidats[u.idx]` : liste des (créneau hebdo, dispo) envisageables pour l'unité.
    """
    T = hz.T
    W = max(1, len(hz.creneaux_hebdo))
    locaux = {l.id: l for l in ds.locaux}
    ens = {e.id: e for e in ds.enseignants}
    res_q = {r.id: r.quantite for r in ds.ressources}

    from .moteur import creneaux_incompatibles

    occ_groupe = defaultdict(set)  # (g, jour, période) -> semaines
    occ_ens = defaultdict(set)  # (e, jour, période) -> semaines
    occ_local = defaultdict(set)  # (local, jour, période) -> semaines
    charge_ens = defaultdict(int)  # (e, semaine) -> périodes
    occ_res = defaultdict(int)  # (r, jour, période, semaine) -> nb
    sites_ens = defaultdict(list)  # (e, semaine) -> [(implantation, créneau)]
    plafonds = {e.id: (e.max_periodes_semaine if e.max_periodes_semaine is not None
                       else ds.etablissement.max_periodes_semaine_defaut) for e in ds.enseignants}
    deplacements = len(ds.etablissement.implantations) > 1
    for l in ds.locaux:
        for j in hz.jours:
            for pj in hz.journee[j]:
                if l.ouvert(JOURS[j], pj.plage.id):
                    occ_local[(l.id, j, pj.rang)].update(hz.local_bloque(l, hz.creneau(j, pj.rang, 1)))
                else:  # local jamais disponible à cette plage ce jour-là
                    occ_local[(l.id, j, pj.rang)].update(range(hz.T))

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
        j = ch.jour_idx
        if any(t in occ_groupe[(u.groupe.id, j, r)] for r in ch.rangs):
            return False
        pw = ch.periodes
        for e in u.enseignants:
            if any(t in occ_ens[(e, j, r)] for r in ch.rangs):
                return False
            m = plafonds[e]
            if m is not None and charge_ens[(e, t)] + deja + pw > m:
                return False
            if deplacements and not u.distanciel and u.implantation and any(
                    site != u.implantation and creneaux_incompatibles(ds, ch, autre) for site, autre in sites_ens[(e, t)]):
                return False
        for rs in u.cours.ressources:
            if any(occ_res[(rs, j, r, t)] >= res_q[rs] for r in ch.rangs):
                return False
        return local is None or all(t not in occ_local[(local, j, r)] for r in ch.rangs)

    def placer(u: Unite, ch, k: int, s: int, e: int, r: str | None):
        solution[(u.idx, ch.w, k)] = (s, e, r)
        j = ch.jour_idx
        for t in range(s, e):
            for rang in ch.rangs:
                occ_groupe[(u.groupe.id, j, rang)].add(t)
                if r:
                    occ_local[(r, j, rang)].add(t)
                for en in u.enseignants:
                    occ_ens[(en, j, rang)].add(t)
                for rs in u.cours.ressources:
                    occ_res[(rs, j, rang, t)] += 1
            for en in u.enseignants:
                charge_ens[(en, t)] += ch.periodes
                if u.implantation and not u.distanciel:
                    sites_ens[(en, t)].append((u.implantation, ch))

    def locaux_tries(u: Unite):
        if u.distanciel:
            return [None]  # séance à distance : pas de local
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
                pw = ch.periodes
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
            reste -= n * ch.periodes
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
                r = next((r for r in locaux_tries(u) if semaine_libre(u, ch, s, r)), False)
                if r is False:
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
                        reste -= ch.periodes
                        deja += ch.periodes
                t += 1
            if len(dernier) != len(ens_):
                continue
            fins_sem = [dernier[ch.w] + 1 for ch, _ in ens_]
            if min(fins_sem) < max(fins_sem) - 1:
                continue  # les créneaux ne finiraient pas ensemble
            if any(set(a.rangs) & set(b.rangs) for (a, _), (b, _) in zip(ens_, ens_[1:])):
                continue  # deux blocs qui se chevauchent le même jour
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
