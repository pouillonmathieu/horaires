"""Moteur de planification par programmation par contraintes (Google OR-Tools CP-SAT).

Principe
--------
Le calendrier est découpé en « créneaux datés » (ex. lundi 11/01 soir). Pour chaque
couple (groupe, module) le solveur choisit :
  * l'enseignant (un seul par module et par groupe, pour la continuité pédagogique) ;
  * les créneaux datés où le cours a lieu, et le local de chaque séance.

Contraintes dures
  - un groupe, un local, un enseignant : au plus une séance par créneau ;
  - jours de cours, créneaux autorisés, dates de début/fin du groupe ;
  - jours fériés / vacances, jours de stage ;
  - disponibilités et indisponibilités des enseignants et des locaux ;
  - capacité du local ≥ effectif du groupe, équipements fixes requis ;
  - matériel mobile en quantité limitée ;
  - modules prérequis entièrement terminés avant le début du module suivant ;
  - plafond de périodes par semaine (enseignant) et de séances par semaine (module).

Objectif (par ordre de priorité)
  1. planifier toutes les périodes requises (les périodes manquantes sont fortement pénalisées
     au lieu de rendre le problème insoluble : on obtient toujours un horaire, avec la liste
     précise de ce qui n'a pas pu être placé et pourquoi) ;
  2. terminer les modules au plus tôt ;
  3. (paramétrable) concentrer chaque module dans le temps.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from ortools.sat.python import cp_model

from ..modeles import JOURS, Creneau, Groupe, JeuDeDonnees, Module

POIDS_MANQUE = 100_000  # par période non planifiée


@dataclass(frozen=True)
class CreneauDate:
    k: int
    jour: date
    creneau: Creneau
    semaine: int  # index de semaine ISO depuis le début

    @property
    def periodes(self) -> int:
        return self.creneau.periodes


@dataclass
class Parametres:
    duree_max_s: float = 30.0
    poids_fin: int = 10  # terminer tôt
    poids_compacite: int = 1  # resserrer chaque module
    nb_threads: int = 8


@dataclass
class Resultat:
    statut: str
    duree_s: float
    seances: list[dict] = field(default_factory=list)
    synthese: list[dict] = field(default_factory=list)
    alertes: list[str] = field(default_factory=list)
    objectif: float | None = None

    def en_dict(self) -> dict:
        return {
            "statut": self.statut,
            "duree_s": round(self.duree_s, 2),
            "objectif": self.objectif,
            "seances": self.seances,
            "synthese": self.synthese,
            "alertes": self.alertes,
        }


def creneaux_dates(ds: JeuDeDonnees) -> list[CreneauDate]:
    cal = ds.calendrier
    creneaux = sorted(cal.creneaux, key=lambda c: c.heure_debut)
    lundi0 = cal.date_debut - timedelta(days=cal.date_debut.weekday())
    out: list[CreneauDate] = []
    d = cal.date_debut
    while d <= cal.date_fin:
        if JOURS[d.weekday()] in cal.jours_cours and not any(f.contient(d) for f in cal.fermetures):
            for c in creneaux:
                out.append(CreneauDate(len(out), d, c, (d - lundi0).days // 7))
        d += timedelta(days=1)
    return out


def groupe_disponible(g: Groupe, s: CreneauDate, jours_cal: list[str]) -> bool:
    if g.date_debut and s.jour < g.date_debut:
        return False
    if g.date_fin and s.jour > g.date_fin:
        return False
    if JOURS[s.jour.weekday()] not in (g.jours_cours or jours_cal):
        return False
    if g.creneaux and s.creneau.id not in g.creneaux:
        return False
    return not any(st.bloque(s.jour) for st in g.stages)


def locaux_eligibles(ds: JeuDeDonnees, g: Groupe, m: Module):
    return [l for l in ds.locaux if l.capacite >= g.effectif and set(m.equipements) <= set(l.equipements)]


def local_disponible(local, s: CreneauDate) -> bool:
    return not any(i.bloque(s.jour, s.creneau.id) for i in local.indisponibilites)


def planifier(ds: JeuDeDonnees, params: Parametres | None = None) -> Resultat:
    params = params or Parametres()
    t0 = time.monotonic()
    slots = creneaux_dates(ds)
    if not slots:
        return Resultat("AUCUN_CRENEAU", 0, alertes=["Le calendrier ne contient aucun créneau de cours."])

    n = len(slots)
    max_p = max(c.periodes for c in ds.calendrier.creneaux)
    ens_par_id = {e.id: e for e in ds.enseignants}
    res_par_id = {r.id: r for r in ds.ressources}
    jours_cal = ds.calendrier.jours_cours

    mdl = cp_model.CpModel()

    # --- variables par (groupe, module) ---------------------------------------------
    occ: dict[tuple, dict[int, cp_model.IntVar]] = {}  # gm -> k -> bool
    x: dict[tuple, dict[tuple[int, str], cp_model.IntVar]] = {}  # gm -> (k, local) -> bool
    y: dict[tuple, dict[str, cp_model.IntVar | int]] = {}  # gm -> enseignant -> bool/const
    debut, fin, manque = {}, {}, {}

    usage_groupe = defaultdict(list)  # (g,k) -> [bool]
    usage_local = defaultdict(list)  # (local,k) -> [bool]
    usage_ens = defaultdict(list)  # (ens,k) -> [(bool, periodes)]
    usage_res = defaultdict(list)  # (res,k) -> [bool]

    for g in ds.groupes:
        for mid in g.modules:
            m = ds.module(mid)
            gm = (g.id, m.id)
            locaux = locaux_eligibles(ds, g, m)
            enseignants = [ens_par_id[e] for e in m.enseignants]

            occ[gm], x[gm], y[gm] = {}, {}, {}
            for s in slots:
                if not groupe_disponible(g, s, jours_cal):
                    continue
                if not any(e.disponible(s.jour, s.creneau.id) for e in enseignants):
                    continue
                locs = [l for l in locaux if local_disponible(l, s)]
                if not locs:
                    continue
                o = mdl.NewBoolVar(f"occ_{g.id}_{m.id}_{s.k}")
                occ[gm][s.k] = o
                lits = []
                for l in locs:
                    v = mdl.NewBoolVar(f"x_{g.id}_{m.id}_{s.k}_{l.id}")
                    x[gm][(s.k, l.id)] = v
                    usage_local[(l.id, s.k)].append(v)
                    lits.append(v)
                mdl.Add(sum(lits) == o)
                usage_groupe[(g.id, s.k)].append(o)
                for r in m.ressources:
                    usage_res[(r, s.k)].append(o)

            # choix de l'enseignant
            if len(enseignants) == 1:
                y[gm][enseignants[0].id] = 1
            elif enseignants:
                for e in enseignants:
                    y[gm][e.id] = mdl.NewBoolVar(f"y_{g.id}_{m.id}_{e.id}")
                mdl.AddExactlyOne(y[gm].values())

            for e in enseignants:
                ye = y[gm][e.id]
                for k, o in occ[gm].items():
                    s = slots[k]
                    if isinstance(ye, int):
                        usage_ens[(e.id, k)].append((o, s.periodes))
                    elif not e.disponible(s.jour, s.creneau.id):
                        mdl.AddBoolOr([o.Not(), ye.Not()])
                    else:
                        w = mdl.NewBoolVar(f"w_{g.id}_{m.id}_{k}_{e.id}")
                        mdl.Add(w >= o + ye - 1)
                        usage_ens[(e.id, k)].append((w, s.periodes))

            # volume de périodes
            livre = sum(o * slots[k].periodes for k, o in occ[gm].items())
            manque[gm] = mdl.NewIntVar(0, m.periodes, f"manque_{g.id}_{m.id}")
            mdl.Add(livre + manque[gm] >= m.periodes)
            mdl.Add(livre <= m.periodes + max_p - 1)

            # bornes temporelles du module
            debut[gm] = mdl.NewIntVar(0, n - 1, f"debut_{g.id}_{m.id}")
            fin[gm] = mdl.NewIntVar(0, n - 1, f"fin_{g.id}_{m.id}")
            mdl.Add(debut[gm] <= fin[gm])
            for k, o in occ[gm].items():
                mdl.Add(debut[gm] <= k).OnlyEnforceIf(o)
                mdl.Add(fin[gm] >= k).OnlyEnforceIf(o)

            # rythme hebdomadaire max
            if m.max_seances_semaine:
                par_sem = defaultdict(list)
                for k, o in occ[gm].items():
                    par_sem[slots[k].semaine].append(o)
                for lits in par_sem.values():
                    if len(lits) > m.max_seances_semaine:
                        mdl.Add(sum(lits) <= m.max_seances_semaine)

    # --- conflits de ressources -------------------------------------------------------
    for lits in usage_groupe.values():
        if len(lits) > 1:
            mdl.AddAtMostOne(lits)
    for lits in usage_local.values():
        if len(lits) > 1:
            mdl.AddAtMostOne(lits)
    for lits in usage_ens.values():
        if len(lits) > 1:
            mdl.AddAtMostOne([v for v, _ in lits])
    for (rid, _k), lits in usage_res.items():
        if len(lits) > res_par_id[rid].quantite:
            mdl.Add(sum(lits) <= res_par_id[rid].quantite)

    # plafond hebdomadaire des enseignants
    for e in ds.enseignants:
        if e.max_periodes_semaine is None:
            continue
        par_sem = defaultdict(list)
        for s in slots:
            for v, p in usage_ens.get((e.id, s.k), []):
                par_sem[s.semaine].append(v * p)
        for termes in par_sem.values():
            mdl.Add(sum(termes) <= e.max_periodes_semaine)

    # --- prérequis : le module préalable est terminé avant le début du suivant --------
    prerequis_externes = []
    for g in ds.groupes:
        for mid in g.modules:
            for pre in ds.module(mid).prerequis:
                if pre in g.modules:
                    mdl.Add(fin[(g.id, pre)] < debut[(g.id, mid)])
                else:
                    prerequis_externes.append((g.id, mid, pre))

    # --- objectif ---------------------------------------------------------------------
    mdl.Minimize(
        POIDS_MANQUE * sum(manque.values())
        + params.poids_fin * sum(fin.values())
        + params.poids_compacite * sum(fin[gm] - debut[gm] for gm in fin)
    )

    solveur = cp_model.CpSolver()
    solveur.parameters.max_time_in_seconds = float(params.duree_max_s)
    solveur.parameters.num_workers = params.nb_threads
    statut = solveur.Solve(mdl)
    nom_statut = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "REALISABLE",
        cp_model.INFEASIBLE: "IMPOSSIBLE",
        cp_model.MODEL_INVALID: "MODELE_INVALIDE",
    }.get(statut, "INCONNU")

    res = Resultat(nom_statut, time.monotonic() - t0)
    for g, mid, pre in prerequis_externes:
        res.alertes.append(
            f"Groupe {g} : le prérequis {pre} de {mid} ne fait pas partie du parcours du groupe ; il est supposé acquis."
        )
    if statut not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        res.alertes.append("Aucune solution trouvée dans le temps imparti. Augmentez la durée ou simplifiez les données.")
        return res
    res.objectif = solveur.ObjectiveValue()

    # --- lecture de la solution -------------------------------------------------------
    groupes = {g.id: g for g in ds.groupes}
    for gm in occ:
        gid, mid = gm
        m = ds.module(mid)
        ens = next((e for e, v in y[gm].items() if (v if isinstance(v, int) else solveur.Value(v))), None)
        livre = 0
        for (k, lid), v in x[gm].items():
            if solveur.Value(v):
                s = slots[k]
                livre += s.periodes
                res.seances.append(
                    {
                        "date": s.jour.isoformat(),
                        "jour": JOURS[s.jour.weekday()],
                        "creneau": s.creneau.id,
                        "heure_debut": s.creneau.heure_debut,
                        "heure_fin": s.creneau.heure_fin,
                        "periodes": s.periodes,
                        "groupe": gid,
                        "groupe_nom": groupes[gid].nom,
                        "module": mid,
                        "module_nom": m.nom,
                        "local": lid,
                        "enseignant": ens,
                        "_k": k,
                    }
                )
        dates = sorted(se["date"] for se in res.seances if se["groupe"] == gid and se["module"] == mid)
        res.synthese.append(
            {
                "groupe": gid,
                "module": mid,
                "module_nom": m.nom,
                "enseignant": ens,
                "periodes_requises": m.periodes,
                "periodes_planifiees": livre,
                "periodes_manquantes": max(0, m.periodes - livre),
                "premiere_seance": dates[0] if dates else None,
                "derniere_seance": dates[-1] if dates else None,
                "nb_seances": len(dates),
            }
        )
    res.seances.sort(key=lambda s: (s.pop("_k"), s["groupe"]))
    return res
