"""Moteur de planification par programmation par contraintes (Google OR-Tools CP-SAT).

Modèle « hebdomadaire récurrent »
---------------------------------
En formation pour adultes, un cours a lieu à jour et heure fixes (« le mardi soir ») pendant
un certain nombre de semaines. Le moteur choisit, pour chaque *unité* (une part de cours,
c'est-à-dire une attribution, pour un groupe) :

  * un ou plusieurs créneaux hebdomadaires (ex. mardi soir + jeudi soir) ;
  * pour chacun, la semaine de début et la semaine de fin ;
  * pour chacun, le local (fixe sur toute la période).

Les séances réelles sont ensuite déduites date par date : jours fériés, vacances, stages du
groupe et absences ponctuelles des enseignants sont sautés, et le cours se prolonge d'autant.

Chaque (unité, créneau hebdomadaire) est un *intervalle optionnel* sur l'axe des semaines,
ce qui rend le modèle compact même pour de grands établissements (centaines de groupes,
milliers de cours).

Contraintes dures
  - groupe, local, enseignant : jamais deux cours au même créneau la même semaine ;
  - co-enseignement : tous les enseignants d'une attribution sont présents ;
  - jours/créneaux du groupe, dates du groupe, fermetures, stages ;
  - disponibilités hebdomadaires et indisponibilités datées des enseignants et des locaux ;
  - capacité et équipements des locaux ; matériel mobile en quantité limitée ;
  - prérequis : un module se termine avant le début du suivant (pour un même groupe) ;
  - plafonds hebdomadaires (enseignant, cours).

Objectif, en deux phases lexicographiques
  1. planifier le maximum de périodes (idéalement toutes) ;
  2. à volume égal : terminer tôt, resserrer les cours (paramétrable), utiliser le local attitré.
"""
from __future__ import annotations

import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cached_property

from ortools.sat.python import cp_model
from ortools.util.python import sorted_interval_list

from ..modeles import JOURS, Cours, Creneau, Enseignant, Groupe, JeuDeDonnees, Local, Module

Domain = sorted_interval_list.Domain


# ----------------------------------------------------------------------------- structures
@dataclass(frozen=True)
class CreneauHebdo:
    w: int
    jour_idx: int  # 0 = lundi
    creneau: Creneau

    @property
    def jour(self) -> str:
        return JOURS[self.jour_idx]

    @property
    def libelle(self) -> str:
        return f"{self.jour} {self.creneau.libelle or self.creneau.id}"


@dataclass
class Unite:
    """Une attribution d'un cours, pour un groupe : la brique que le moteur planifie."""
    idx: int
    groupe: Groupe
    module: Module
    cours: Cours
    part: int
    enseignants: tuple[str, ...]
    periodes: int

    @property
    def cle(self) -> str:
        return f"{self.groupe.id}/{self.cours.id}/{self.part}"


def construire_unites(ds: JeuDeDonnees) -> list[Unite]:
    modules = {m.id: m for m in ds.modules}
    out = []
    for g in ds.groupes:
        for mid in g.modules:
            m = modules[mid]
            for c in m.cours:
                for i, a in enumerate(c.attributions_pour(g.id)):
                    out.append(Unite(len(out), g, m, c, i, tuple(a.enseignants), a.periodes))
    return out


def dispo_hebdo(e: Enseignant, jour: str, creneau_id: str) -> bool:
    return not e.disponibilites or f"{jour}:{creneau_id}" in e.disponibilites or f"{jour}:*" in e.disponibilites


def locaux_eligibles(ds: JeuDeDonnees, g: Groupe, c: Cours) -> list[Local]:
    ok = [l for l in ds.locaux if l.capacite >= g.effectif and set(c.equipements) <= set(l.equipements)]
    # local attitré d'abord, puis le plus petit local suffisant (on garde les grandes salles libres)
    return sorted(ok, key=lambda l: (l.id != g.local_prefere, l.capacite, l.id))


class Horizon:
    """Découpage du calendrier en semaines × créneaux hebdomadaires, avec disponibilités datées."""

    def __init__(self, ds: JeuDeDonnees):
        self.ds = ds
        cal = ds.calendrier
        self.lundi0 = cal.date_debut - timedelta(days=cal.date_debut.weekday())
        self.T = (cal.date_fin - self.lundi0).days // 7 + 1
        creneaux = sorted(cal.creneaux, key=lambda c: c.heure_debut)
        jours = [JOURS.index(j) for j in JOURS if j in cal.jours_cours]
        self.creneaux_hebdo = [CreneauHebdo(i, j, c) for i, (j, c) in enumerate((j, c) for j in jours for c in creneaux)]
        self.enseignants = {e.id: e for e in ds.enseignants}
        self._cache_base: dict = {}
        self._cache_unite: dict = {}

    def date(self, t: int, ch: CreneauHebdo) -> date:
        return self.lundi0 + timedelta(days=7 * t + ch.jour_idx)

    def semaine_de(self, d: date) -> int:
        return (d - self.lundi0).days // 7

    @cached_property
    def _ouvert(self) -> list[list[bool]]:
        cal = self.ds.calendrier
        res = []
        for ch in self.creneaux_hebdo:
            ligne = []
            for t in range(self.T):
                d = self.date(t, ch)
                ligne.append(cal.date_debut <= d <= cal.date_fin and not any(f.contient(d) for f in cal.fermetures))
            res.append(ligne)
        return res

    def groupe_autorise(self, g: Groupe, ch: CreneauHebdo) -> bool:
        return ch.jour in (g.jours_cours or self.ds.calendrier.jours_cours) and (not g.creneaux or ch.creneau.id in g.creneaux)

    def base_groupe(self, g: Groupe, ch: CreneauHebdo) -> list[int]:
        cle = (g.id, ch.w)
        if cle not in self._cache_base:
            ligne = []
            for t in range(self.T):
                d = self.date(t, ch)
                ok = (self._ouvert[ch.w][t] and (not g.date_debut or d >= g.date_debut) and (not g.date_fin or d <= g.date_fin)
                      and not any(s.bloque(d) for s in g.stages))
                ligne.append(int(ok))
            self._cache_base[cle] = ligne
        return self._cache_base[cle]

    def dispo_unite(self, u: Unite, ch: CreneauHebdo, avec_enseignants: bool = True) -> list[int] | None:
        """Semaines où l'unité peut avoir séance à ce créneau hebdomadaire (None = créneau exclu)."""
        if not self.groupe_autorise(u.groupe, ch):
            return None
        if not avec_enseignants:
            return self.base_groupe(u.groupe, ch)
        ens = [self.enseignants[e] for e in u.enseignants]
        if not all(dispo_hebdo(e, ch.jour, ch.creneau.id) for e in ens):
            return None
        cle = (u.groupe.id, u.enseignants, ch.w)
        if cle not in self._cache_unite:
            base = self.base_groupe(u.groupe, ch)
            ligne = []
            for t in range(self.T):
                ok = base[t]
                if ok:
                    d = self.date(t, ch)
                    ok = not any(i.bloque(d, ch.creneau.id) for e in ens for i in e.indisponibilites)
                ligne.append(int(ok))
            self._cache_unite[cle] = ligne
        return self._cache_unite[cle]

    def local_bloque(self, l: Local, ch: CreneauHebdo) -> list[int]:
        return [t for t in range(self.T) if any(i.bloque(self.date(t, ch), ch.creneau.id) for i in l.indisponibilites)]


# ----------------------------------------------------------------------------- paramètres / résultat
@dataclass
class Parametres:
    duree_max_s: float = 120.0
    poids_fin: int = 10  # terminer tôt
    poids_compacite: int = 1  # resserrer chaque cours dans le temps
    nb_threads: int = 8


@dataclass
class Resultat:
    statut: str
    duree_s: float
    seances: list[dict] = field(default_factory=list)
    synthese: list[dict] = field(default_factory=list)
    alertes: list[str] = field(default_factory=list)
    statistiques: dict = field(default_factory=dict)

    def en_dict(self) -> dict:
        return {"statut": self.statut, "duree_s": round(self.duree_s, 2), "seances": self.seances,
                "synthese": self.synthese, "alertes": self.alertes, "statistiques": self.statistiques}


@dataclass
class _Var:
    u: Unite
    ch: CreneauHebdo
    dispo: list[int]
    present: cp_model.IntVar
    debut: cp_model.IntVar
    fin: cp_model.IntVar  # exclusive
    intervalle: cp_model.IntervalVar
    nb: cp_model.IntVar
    taille: cp_model.IntVar
    cs: cp_model.IntVar
    ce: cp_model.IntVar
    cumul: list[int]


@dataclass
class _Place:
    """Une (unité, créneau hebdomadaire) retenue par la phase A."""
    u: Unite
    ch: CreneauHebdo
    semaines: list[int]  # semaines où une séance a effectivement lieu
    local: str | None = None
    locaux_par_semaine: dict[int, str | None] = field(default_factory=dict)


# ----------------------------------------------------------------------------- moteur
def planifier(ds: JeuDeDonnees, params: Parametres | None = None) -> Resultat:
    params = params or Parametres()
    t0 = time.monotonic()
    hz = Horizon(ds)
    unites = construire_unites(ds)
    T = hz.T
    res_par_id = {r.id: r for r in ds.ressources}
    eligibles = {u.idx: frozenset(l.id for l in locaux_eligibles(ds, u.groupe, u.cours)) for u in unites}

    mdl = cp_model.CpModel()
    vars_uw: list[_Var] = []
    par_unite: dict[int, list[_Var]] = defaultdict(list)
    nogroupe = defaultdict(list)
    noens = defaultdict(list)
    cumul_ens = defaultdict(lambda: ([], []))
    cumul_res = defaultdict(list)

    for u in unites:
        if not eligibles[u.idx] or any(res_par_id[r].quantite == 0 for r in u.cours.ressources):
            continue
        for ch in hz.creneaux_hebdo:
            dispo = hz.dispo_unite(u, ch)
            if not dispo or not any(dispo):
                continue
            semaines = [t for t in range(T) if dispo[t]]
            pw = ch.creneau.periodes
            n_max = min(len(semaines), math.ceil(u.periodes / pw))
            nom = f"{u.idx}_{ch.w}"
            p = mdl.NewBoolVar(f"p_{nom}")
            s = mdl.NewIntVarFromDomain(Domain.FromValues(semaines), f"s_{nom}")
            e = mdl.NewIntVarFromDomain(Domain.FromValues([t + 1 for t in semaines]), f"e_{nom}")
            taille = mdl.NewIntVar(1, T, f"d_{nom}")
            iv = mdl.NewOptionalIntervalVar(s, taille, e, p, f"i_{nom}")
            cumul = [0]
            for a in dispo:
                cumul.append(cumul[-1] + a)
            cs = mdl.NewIntVar(0, cumul[-1], f"cs_{nom}")
            ce = mdl.NewIntVar(0, cumul[-1], f"ce_{nom}")
            mdl.AddElement(s, cumul, cs)
            mdl.AddElement(e, cumul, ce)
            nb = mdl.NewIntVar(0, n_max, f"n_{nom}")
            mdl.Add(nb == ce - cs).OnlyEnforceIf(p)
            mdl.Add(nb == 0).OnlyEnforceIf(p.Not())
            mdl.Add(s == semaines[0]).OnlyEnforceIf(p.Not())
            mdl.Add(e == semaines[0] + 1).OnlyEnforceIf(p.Not())

            v = _Var(u, ch, dispo, p, s, e, iv, nb, taille, cs, ce, cumul)
            vars_uw.append(v)
            par_unite[u.idx].append(v)
            nogroupe[(u.groupe.id, ch.w)].append(iv)
            for ens in u.enseignants:
                noens[(ens, ch.w)].append(iv)
                ivs, dem = cumul_ens[ens]
                ivs.append(iv)
                dem.append(pw)
            for r in u.cours.ressources:
                cumul_res[(r, ch.w)].append(iv)

    for groupe in (nogroupe, noens):
        for ivs in groupe.values():
            if len(ivs) > 1:
                mdl.AddNoOverlap(ivs)
    for (rid, _w), ivs in cumul_res.items():
        if len(ivs) > res_par_id[rid].quantite:
            mdl.AddCumulative(ivs, [1] * len(ivs), res_par_id[rid].quantite)
    for e in ds.enseignants:
        if e.max_periodes_semaine is not None and e.id in cumul_ens:
            ivs, dem = cumul_ens[e.id]
            mdl.AddCumulative(ivs, dem, e.max_periodes_semaine)

    # capacité en locaux (conditions de Hall) : pour chaque ensemble de salles admissibles E,
    # les cours qui ne peuvent aller que dans E ne dépassent jamais |E| salles en même temps.
    locaux_par_id = {l.id: l for l in ds.locaux}
    par_w = defaultdict(list)
    for v in vars_uw:
        par_w[v.ch.w].append(v)
    nb_hall = 0
    for w, vs in par_w.items():
        ch = hz.creneaux_hebdo[w]
        ensembles = {eligibles[v.u.idx] for v in vs}
        ensembles.add(frozenset().union(*ensembles))
        bloques = {r: hz.local_bloque(locaux_par_id[r], ch) for r in frozenset().union(*ensembles)}
        for E in ensembles:
            ivs = [v.intervalle for v in vs if eligibles[v.u.idx] <= E]
            fixes = [mdl.NewIntervalVar(t, 1, t + 1, "") for r in E for t in bloques[r]]
            if len(ivs) + len(fixes) > len(E):
                mdl.AddCumulative(ivs + fixes, [1] * (len(ivs) + len(fixes)), len(E))
                nb_hall += 1

    # volumes, bornes temporelles, rythme hebdomadaire.
    # Les bornes sont exprimées en « rang de créneau » = semaine × W + créneau hebdomadaire (ordre chronologique),
    # ce qui permet d'enchaîner deux modules dans la même semaine.
    W = len(hz.creneaux_hebdo)
    manque, debut_u, fin_u = {}, {}, {}
    for u in unites:
        vs = par_unite.get(u.idx, [])
        manque[u.idx] = m = mdl.NewIntVar(0, u.periodes, f"manque_{u.idx}")
        debut_u[u.idx] = du = mdl.NewIntVar(0, T * W, f"du_{u.idx}")
        fin_u[u.idx] = fu = mdl.NewIntVar(0, T * W, f"fu_{u.idx}")
        mdl.Add(du <= fu)
        if not vs:
            mdl.Add(m == u.periodes)
            continue
        livre = sum(v.ch.creneau.periodes * v.nb for v in vs)
        mdl.Add(livre + m >= u.periodes)
        mdl.Add(livre <= u.periodes + max(v.ch.creneau.periodes for v in vs) - 1)
        for v in vs:
            mdl.Add(du <= v.debut * W + v.ch.w).OnlyEnforceIf(v.present)
            mdl.Add(fu >= (v.fin - 1) * W + v.ch.w).OnlyEnforceIf(v.present)
        if u.cours.max_seances_semaine and len(vs) > u.cours.max_seances_semaine:
            mdl.Add(sum(v.present for v in vs) <= u.cours.max_seances_semaine)

    # prérequis entre modules, par groupe
    debut_m, fin_m = {}, {}
    for u in unites:
        k = (u.groupe.id, u.module.id)
        if k not in debut_m:
            debut_m[k] = mdl.NewIntVar(0, T * W, f"dm_{u.groupe.id}_{u.module.id}")
            fin_m[k] = mdl.NewIntVar(0, T * W, f"fm_{u.groupe.id}_{u.module.id}")
        mdl.Add(debut_m[k] <= debut_u[u.idx])
        mdl.Add(fin_m[k] >= fin_u[u.idx])
    alertes = []
    for g in ds.groupes:
        for mid in g.modules:
            for pre in ds.module(mid).prerequis:
                if pre in g.modules:
                    # même groupe : deux séances ne partagent jamais un rang, l'inégalité large suffit
                    mdl.Add(fin_m[(g.id, pre)] <= debut_m[(g.id, mid)])
                else:
                    alertes.append(f"Groupe {g.id} : le prérequis {pre} de {mid} ne fait pas partie du parcours ; supposé acquis.")

    stats = {"unites": len(unites), "variables_hebdo": len(vars_uw), "semaines": T,
             "creneaux_hebdo": len(hz.creneaux_hebdo), "contraintes_locaux": nb_hall}
    D = params.duree_max_s

    # ---- point de départ : heuristique constructive
    from .heuristique import construire
    candidats = defaultdict(list)
    for v in vars_uw:
        candidats[v.u.idx].append((v.ch, v.dispo))
    glouton, debut_glouton = construire(hz, ds, unites, eligibles, candidats)
    indice_glouton = _indice(vars_uw, unites, glouton, debut_glouton, manque, debut_u, fin_u, debut_m, fin_m, W)
    for var, val in indice_glouton.values():
        mdl.AddHint(var, val)
    manque_glouton = sum(indice_glouton.valeur(manque[u.idx]) for u in unites)
    stats["heuristique"] = {"periodes_manquantes": manque_glouton, "duree_s": round(time.monotonic() - t0, 1)}

    # ---- phase A1 : maximiser le volume planifié (recherche globale)
    solveur = cp_model.CpSolver()
    solveur.parameters.num_workers = params.nb_threads
    total_manque = sum(manque.values())
    mdl.Minimize(total_manque)
    solveur.parameters.max_time_in_seconds = max(5.0, D * 0.15)
    st1 = solveur.Solve(mdl)
    if st1 == cp_model.INFEASIBLE:
        return Resultat("IMPOSSIBLE", time.monotonic() - t0, alertes=alertes + [
            "Données contradictoires : vérifiez le contrôle des données (cycles de prérequis…)."], statistiques=stats)
    phase1_optimale = st1 == cp_model.OPTIMAL
    if st1 in (cp_model.OPTIMAL, cp_model.FEASIBLE) and solveur.ObjectiveValue() <= manque_glouton:
        meilleur = int(solveur.ObjectiveValue())
        sol = list(solveur.ResponseProto().solution)
    else:  # le solveur n'a pas fait mieux que l'heuristique : on part de celle-ci
        phase1_optimale = False
        sol = _completer(mdl, vars_uw, indice_glouton, params)
        if sol is None:
            return Resultat("INCONNU", time.monotonic() - t0, alertes=alertes + [
                "Aucune solution trouvée : augmentez la durée de calcul."], statistiques=stats)
        meilleur = manque_glouton
    stats["phase_volume"] = {"periodes_manquantes": meilleur, "optimal": phase1_optimale,
                             "duree_s": round(time.monotonic() - t0, 1)}

    # ---- phase A1 bis : réparation ciblée des cours incomplets (voisinages)
    if meilleur > 0 and not phase1_optimale:
        sol, meilleur, info = _reparer(mdl, vars_uw, par_unite, unites, manque, sol, t0 + D * 0.75, params)
        stats["reparation"] = {"periodes_manquantes": meilleur, **info}

    # ---- phase A2 : à volume égal, qualité de l'horaire
    mdl.ClearHints()
    indice = mdl.Proto().solution_hint
    indice.vars.extend(range(len(sol)))
    indice.values.extend(sol)
    mdl.Add(total_manque <= meilleur)
    mdl.Minimize(params.poids_fin * sum(fin_u.values()) + params.poids_compacite * sum(fin_u[i] - debut_u[i] for i in fin_u))
    solveur.parameters.max_time_in_seconds = max(3.0, D * 0.9 - (time.monotonic() - t0))
    st2 = solveur.Solve(mdl)
    if st2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        valeur = solveur.Value
        statut = "OPTIMAL" if st2 == cp_model.OPTIMAL and phase1_optimale else "REALISABLE"
    else:  # repli sur la meilleure solution connue
        valeur = lambda var: sol[var.Index()]  # noqa: E731
        statut = "REALISABLE"
    stats["phase_qualite"] = {"optimal": st2 == cp_model.OPTIMAL, "duree_s": round(solveur.WallTime(), 1)}

    places = []
    for v in vars_uw:
        if valeur(v.present):
            s, e = valeur(v.debut), valeur(v.fin)
            places.append(_Place(v.u, v.ch, [t for t in range(s, e) if v.dispo[t]]))

    # ---- phase B : attribution des locaux
    t_b = time.monotonic()
    sans_local = _attribuer_locaux(hz, ds, places, eligibles, params, max(5.0, params.duree_max_s - (t_b - t0)))
    stats["phase_locaux"] = {"duree_s": round(time.monotonic() - t_b, 1), "seances_sans_local": sans_local}
    if sans_local:
        alertes.append(f"{sans_local} séance(s) sans local disponible : voir « À attribuer » dans l'horaire.")
    changements = sum(1 for p in places if p.local is None)
    if changements:
        alertes.append(f"{changements} cours changent de local certaines semaines (aucun local libre sur toute leur durée).")

    resultat = Resultat(statut, time.monotonic() - t0, alertes=alertes, statistiques=stats)
    _lire_solution(hz, unites, places, resultat)
    return resultat


class _DictVars(dict):
    def __setitem__(self, var, val):
        super().__setitem__(var.Index(), (var, val))

    def update(self, d):
        for k, v in d.items():
            self[k] = v

    def valeur(self, var) -> int:
        return dict.__getitem__(self, var.Index())[1]


def _indice(vars_uw, unites, glouton, debut_glouton, manque, debut_u, fin_u, debut_m, fin_m, W) -> dict:
    """Valeurs de toutes les variables du modèle correspondant à la solution heuristique.

    Retourne {indice de variable: (variable, valeur)} (les variables CP-SAT ne sont pas des clés sûres).
    """
    h = _DictVars()
    livre = defaultdict(int)
    bornes = defaultdict(list)
    for v in vars_uw:
        sol = glouton.get((v.u.idx, v.ch.w))
        if sol:
            s, e, _ = sol
            nb = v.cumul[e] - v.cumul[s]
            bornes[v.u.idx].append((s * W + v.ch.w, (e - 1) * W + v.ch.w))
        else:
            s = next(t for t, a in enumerate(v.dispo) if a)
            e, nb = s + 1, 0
        h.update({v.present: int(bool(sol)), v.debut: s, v.fin: e, v.taille: e - s,
                  v.cs: v.cumul[s], v.ce: v.cumul[e], v.nb: nb})
        livre[v.u.idx] += nb * v.ch.creneau.periodes
    par_module = defaultdict(list)
    for u in unites:
        h[manque[u.idx]] = max(0, u.periodes - livre[u.idx])
        par_module[(u.groupe.id, u.module.id)].append(u)
    for k, us in par_module.items():
        tout = [b for u in us for b in bornes[u.idx]]
        dm = min((s for s, _ in tout), default=debut_glouton.get(k, 0))
        fm = max((e for _, e in tout), default=dm)
        h[debut_m[k]], h[fin_m[k]] = dm, fm
        for u in us:
            b = bornes[u.idx]
            h[debut_u[u.idx]] = min((s for s, _ in b), default=dm)
            h[fin_u[u.idx]] = max((e for _, e in b), default=dm)
    return h


def _figer(proto, vars_uw, valeurs, garder=frozenset()):
    """Fixe le domaine des variables de décision (présence, début, fin) hors du voisinage `garder`."""
    for v in vars_uw:
        if v.u.idx in garder:
            continue
        for var in (v.present, v.debut, v.fin):
            i = var.Index()
            d = proto.variables[i].domain
            d.clear()
            d.extend([valeurs[i], valeurs[i]])


def _sous_modele(mdl: cp_model.CpModel, sol: list[int] | None) -> cp_model.CpModel:
    sub = mdl.clone()
    if sol is not None:
        h = sub.Proto().solution_hint
        h.vars.clear()
        h.values.clear()
        h.vars.extend(range(len(sol)))
        h.values.extend(sol)
    return sub


def _completer(mdl, vars_uw, indice: "_DictVars", params: Parametres) -> list[int] | None:
    """Solution complète (toutes variables) correspondant à l'horaire heuristique."""
    sub = _sous_modele(mdl, None)
    valeurs = {i: val for i, (_var, val) in indice.items()}
    _figer(sub.Proto(), vars_uw, valeurs)
    sv = cp_model.CpSolver()
    sv.parameters.num_workers = params.nb_threads
    sv.parameters.max_time_in_seconds = 60
    if sv.Solve(sub) in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return list(sv.ResponseProto().solution)
    return None


def _reparer(mdl, vars_uw, par_unite, unites, manque, sol, echeance: float, params: Parametres):
    """Recherche à voisinage large ciblée : on libère un cours incomplet, son groupe et ses enseignants,
    on fige tout le reste, et on ré-optimise ce sous-problème. Répété tant qu'il reste du temps."""
    rnd = random.Random(42)
    idx_manque = {u.idx: manque[u.idx].Index() for u in unites}
    par_groupe, par_ens = defaultdict(set), defaultdict(set)
    for u in unites:
        par_groupe[u.groupe.id].add(u.idx)
        for e in u.enseignants:
            par_ens[e].add(u.idx)
    essais = defaultdict(int)
    meilleur = sum(sol[i] for i in idx_manque.values())
    iterations = ameliorations = 0
    while meilleur > 0 and time.monotonic() < echeance - 1:
        deficits = [u for u in unites if sol[idx_manque[u.idx]] > 0 and u.idx in par_unite and essais[u.idx] < 4]
        if not deficits:
            break
        u = min(deficits, key=lambda u: (essais[u.idx], -sol[idx_manque[u.idx]], rnd.random()))
        essais[u.idx] += 1
        voisins = set(par_groupe[u.groupe.id])
        for e in u.enseignants:
            voisins |= par_ens[e]
        for _ in range(essais[u.idx]):  # voisinage élargi à chaque nouvel essai
            x = unites[rnd.choice(sorted(voisins))]
            voisins |= par_groupe[x.groupe.id]
            for e in x.enseignants:
                voisins |= par_ens[e]
        sub = _sous_modele(mdl, sol)
        _figer(sub.Proto(), vars_uw, sol, voisins)
        sv = cp_model.CpSolver()
        sv.parameters.num_workers = params.nb_threads
        sv.parameters.max_time_in_seconds = max(1.0, min(6.0, echeance - time.monotonic()))
        st = sv.Solve(sub)
        iterations += 1
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) and sv.ObjectiveValue() <= meilleur:
            if sv.ObjectiveValue() < meilleur:
                ameliorations += 1
                essais.clear()  # la situation a changé : on peut retenter les autres
            meilleur = int(sv.ObjectiveValue())
            sol = list(sv.ResponseProto().solution)
    return sol, meilleur, {"iterations": iterations, "ameliorations": ameliorations}


def _attribuer_locaux(hz: Horizon, ds: JeuDeDonnees, places: list[_Place], eligibles, params: Parametres,
                      budget_s: float) -> int:
    """Attribue un local fixe à chaque (cours, créneau hebdomadaire), créneau par créneau."""
    locaux = {l.id: l for l in ds.locaux}
    par_w = defaultdict(list)
    for p in places:
        par_w[p.ch.w].append(p)
    sans_local = 0
    debut = time.monotonic()
    for i, (w, ps) in enumerate(sorted(par_w.items())):
        ch = hz.creneaux_hebdo[w]
        bloques = {r: set(hz.local_bloque(l, ch)) for r, l in locaux.items() if l.indisponibilites}
        mdl = cp_model.CpModel()
        x = {}
        occupation = defaultdict(list)  # (local, semaine) -> [bool]
        cout = []
        non_aff = []
        for k, p in enumerate(ps):
            lits = []
            for r in eligibles[p.u.idx]:
                if bloques.get(r) and bloques[r] & set(p.semaines):
                    continue
                b = mdl.NewBoolVar("")
                x[(k, r)] = b
                lits.append(b)
                for t in p.semaines:
                    occupation[(r, t)].append(b)
                l = locaux[r]
                penal = (l.capacite - p.u.groupe.effectif) // 4
                if p.u.groupe.local_prefere and r != p.u.groupe.local_prefere:
                    penal += 10
                if set(l.equipements) - set(p.u.cours.equipements) - {"projecteur"}:
                    penal += 20  # salle spécialisée occupée sans nécessité
                if penal:
                    cout.append(penal * b)
            n = mdl.NewBoolVar("")
            non_aff.append(n)
            mdl.AddExactlyOne(lits + [n])
        for lits in occupation.values():
            if len(lits) > 1:
                mdl.AddAtMostOne(lits)
        mdl.Minimize(100_000 * sum(non_aff) + sum(cout))
        sv = cp_model.CpSolver()
        sv.parameters.num_workers = params.nb_threads
        reste = budget_s - (time.monotonic() - debut)
        sv.parameters.max_time_in_seconds = max(1.0, reste / max(1, len(par_w) - i))
        st = sv.Solve(mdl)
        ok = st in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        utilises = defaultdict(set)  # semaine -> locaux pris
        for k, p in enumerate(ps):
            r = next((r for r in eligibles[p.u.idx] if (k, r) in x and ok and sv.Value(x[(k, r)])), None)
            p.local = r
            if r:
                for t in p.semaines:
                    utilises[t].add(r)
        # repli : local variable selon la semaine pour les cours restés sans local fixe
        for p in ps:
            if p.local:
                continue
            for t in p.semaines:
                choix = next((r for r in sorted(eligibles[p.u.idx], key=lambda r: locaux[r].capacite)
                              if r not in utilises[t] and t not in bloques.get(r, ())), None)
                p.locaux_par_semaine[t] = choix
                if choix:
                    utilises[t].add(choix)
                else:
                    sans_local += 1
    return sans_local


def _lire_solution(hz: Horizon, unites: list[Unite], places: list[_Place], res: Resultat) -> None:
    noms_ens = {e.id: e.nom or e.id for e in hz.ds.enseignants}
    par_unite = defaultdict(list)
    for p in places:
        par_unite[p.u.idx].append(p)
    for u in unites:
        livre, dates, hebdo, locaux = 0, [], [], set()
        for p in par_unite.get(u.idx, []):
            hebdo.append(p.ch.libelle)
            for t in p.semaines:
                d = hz.date(t, p.ch)
                local = p.local or p.locaux_par_semaine.get(t)
                locaux.add(local or "À attribuer")
                livre += p.ch.creneau.periodes
                dates.append(d.isoformat())
                res.seances.append({
                    "date": d.isoformat(), "jour": p.ch.jour, "creneau": p.ch.creneau.id,
                    "heure_debut": p.ch.creneau.heure_debut, "heure_fin": p.ch.creneau.heure_fin,
                    "periodes": p.ch.creneau.periodes,
                    "groupe": u.groupe.id, "groupe_nom": u.groupe.nom,
                    "module": u.module.id, "module_nom": u.module.nom,
                    "cours": u.cours.id, "cours_nom": u.cours.nom,
                    "enseignants": list(u.enseignants), "enseignant": " + ".join(u.enseignants),
                    "local": local or "À attribuer",
                })
        dates.sort()
        res.synthese.append({
            "groupe": u.groupe.id, "module": u.module.id, "module_nom": u.module.nom,
            "cours": u.cours.id, "cours_nom": u.cours.nom,
            "enseignants": list(u.enseignants), "enseignant": " + ".join(u.enseignants),
            "enseignant_noms": " + ".join(noms_ens[e] for e in u.enseignants),
            "periodes_requises": u.periodes, "periodes_planifiees": livre,
            "periodes_manquantes": max(0, u.periodes - livre),
            "creneaux_hebdo": hebdo, "locaux": sorted(locaux),
            "premiere_seance": dates[0] if dates else None, "derniere_seance": dates[-1] if dates else None,
            "nb_seances": len(dates),
        })
    res.seances.sort(key=lambda s: (s["date"], s["heure_debut"], s["groupe"]))
