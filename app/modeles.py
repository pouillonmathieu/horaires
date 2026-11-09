"""Modèle de données métier : ce que l'on importe et ce que le solveur consomme.

Toutes les entrées sont validées strictement (champs inconnus refusés, identifiants
contrôlés, références croisées vérifiées) avant d'être stockées ou planifiées.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]

# Identifiants : ceux du logiciel de gestion sont repris tels quels (espaces, « / », accents admis),
# sauf les séparateurs de listes (; , |), « : » (format jour:créneau) et les caractères de balisage.
Id = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[^;,|:<>\"'`\\\x00-\x1f\x7f]{1,64}$")]
Texte = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Heure = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

MAX_ELEMENTS = 5000
BLOCS_PROPOSES = ["plage", "2", "3", "4", "5", "6", "8", "9", "modulable"]


def _bloc_valide(v) -> str:
    """Taille des séances : « plage » (toute la plage horaire), « modulable » (le moteur choisit),
    ou un nombre de périodes (ex. 2, 4, 8 = journée complète)."""
    s = str(v).strip().lower()
    if s in ("plage", "modulable"):
        return s
    if s.isdigit() and 1 <= int(s) <= 12:
        return str(int(s))
    raise ValueError(f"bloc « {v} » invalide (attendu : plage, modulable ou un nombre de périodes de 1 à 12)")


def _jours_valides(v: list[str]) -> list[str]:
    out = []
    for j in v:
        j = j.strip().lower()[:3]
        if j not in JOURS:
            raise ValueError(f"jour inconnu « {j} » (attendu : {', '.join(JOURS)})")
        out.append(j)
    return out


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Periode(Strict):
    date_debut: date
    date_fin: date
    motif: Texte = ""

    @model_validator(mode="after")
    def _ordre(self):
        if self.date_fin < self.date_debut:
            raise ValueError(f"période {self.date_debut} → {self.date_fin} : la fin précède le début")
        return self

    def contient(self, d: date) -> bool:
        return self.date_debut <= d <= self.date_fin


class Indisponibilite(Periode):
    creneaux: list[Id] = Field(default_factory=list, description="vide = toute la journée")

    def bloque(self, d: date, creneau_id: str) -> bool:
        return self.contient(d) and (not self.creneaux or creneau_id in self.creneaux)


class Stage(Periode):
    jours: list[str] = Field(default_factory=list, description="vide = tous les jours de la période")

    @field_validator("jours")
    @classmethod
    def _valide_jours(cls, v: list[str]) -> list[str]:
        return _jours_valides(v)

    def bloque(self, d: date) -> bool:
        return self.contient(d) and (not self.jours or JOURS[d.weekday()] in self.jours)


TYPES_FERMETURE = ("ferie", "conges", "special")


class Fermeture(Periode):
    """Jour sans cours : férié (toujours fermé), congé scolaire (cours possibles si l'établissement le prévoit)
    ou jour spécial propre à l'établissement (formation continue, fête de l'école…).

    Une fermeture peut ne concerner qu'une partie de la journée (`creneaux`) ou que certaines implantations.
    """
    type: Literal["ferie", "conges", "special"] = "ferie"
    creneaux: list[Id] = Field(default_factory=list, description="vide = toute la journée")
    implantations: list[Id] = Field(default_factory=list, description="vide = toutes les implantations")

    @property
    def partielle(self) -> bool:
        return bool(self.creneaux or self.implantations)

    def bloque(self, d: date, creneau_id: str | None, implantation: str | None) -> bool:
        """Vrai si cette fermeture empêche un cours ce jour-là, sur ce créneau, dans cette implantation."""
        return (self.contient(d) and (not self.creneaux or creneau_id in self.creneaux)
                and (not self.implantations or implantation in self.implantations))


class Creneau(Strict):
    """Plage horaire d'une journée (ex. matin 08:30–12:00, 4 périodes)."""
    id: Id
    libelle: Texte = ""
    heure_debut: Heure
    heure_fin: Heure
    periodes: int = Field(ge=1, le=20, description="nombre de périodes de cours dans cette plage")
    jours: list[str] = Field(default_factory=list, description="vide = tous les jours de cours (ex. sam pour une plage du samedi)")

    @field_validator("jours")
    @classmethod
    def _valide_jours(cls, v: list[str]) -> list[str]:
        return _jours_valides(v)

    def ouverte(self, jour: str) -> bool:
        return not self.jours or jour in self.jours

    @model_validator(mode="after")
    def _ordre(self):
        if self.heure_fin <= self.heure_debut:
            raise ValueError(f"créneau {self.id} : l'heure de fin doit suivre l'heure de début")
        return self


Rythme = Literal["fixe", "souple"]


class Calendrier(Strict):
    date_debut: date
    date_fin: date
    rythme_defaut: Rythme = Field(
        default="souple",
        description="fixe = mêmes jours/heures du début à la fin du cours ; souple = changements, pauses et reprises permis",
    )
    jours_cours: list[str] = Field(default_factory=lambda: ["lun", "mar", "mer", "jeu", "ven"])
    cours_pendant_conges: bool = Field(default=False, description="des cours ont lieu pendant les congés scolaires")
    creneaux: list[Creneau] = Field(min_length=1, max_length=30)
    fermetures: list[Fermeture] = Field(default_factory=list, max_length=500,
                                        description="jours fériés, congés, vacances, jours spéciaux")

    def fermetures_actives(self) -> list[Fermeture]:
        """Fermetures qui empêchent réellement les cours (les congés ne comptent pas si des cours y sont prévus)."""
        return [f for f in self.fermetures if f.type != "conges" or not self.cours_pendant_conges]

    def ferme(self, d: date) -> bool:
        """Établissement fermé toute la journée, sur toutes les implantations."""
        return any(f.contient(d) and not f.partielle for f in self.fermetures_actives())

    def ferme_pour(self, d: date, creneau_id: str | None, implantation: str | None) -> bool:
        """Pas de cours ce jour-là sur ce créneau dans cette implantation (fermetures partielles comprises)."""
        return any(f.bloque(d, creneau_id, implantation) for f in self.fermetures_actives())

    @field_validator("jours_cours")
    @classmethod
    def _valide_jours(cls, v: list[str]) -> list[str]:
        return _jours_valides(v)

    @model_validator(mode="after")
    def _ordre(self):
        if self.date_fin < self.date_debut:
            raise ValueError("calendrier : la date de fin précède la date de début")
        if (self.date_fin - self.date_debut).days > 3 * 366:
            raise ValueError("calendrier : période limitée à 3 ans")
        return self


class Implantation(Strict):
    """Site d'enseignement de l'établissement."""
    id: Id
    nom: Texte = ""
    adresse: Texte = ""


class Etablissement(Strict):
    """Paramètres propres à l'établissement (fixés à la première connexion, modifiables ensuite)."""
    implantations: list[Implantation] = Field(default_factory=list, max_length=200)
    multi_sites_meme_jour: bool = Field(default=True, description="un enseignant peut enseigner sur deux implantations le même jour")
    delai_deplacement_min: int = Field(default=0, ge=0, le=600, description="temps minimal entre deux cours sur des implantations différentes")
    enseignants_toutes_implantations: bool = Field(default=True, description="sinon, les implantations de chaque enseignant sont à préciser")
    max_periodes_semaine_defaut: int | None = Field(default=None, ge=1, le=200)
    bloc_defaut: str = Field(default="plage", description="taille des séances par défaut")

    @field_validator("bloc_defaut", mode="before")
    @classmethod
    def _bloc(cls, v):
        return _bloc_valide(v)


class Local(Strict):
    id: Id
    nom: Texte = ""
    implantation: Id | None = None
    capacite: int = Field(ge=1, le=10000)
    equipements: list[Id] = Field(default_factory=list)
    indisponibilites: list[Indisponibilite] = Field(default_factory=list)


class Ressource(Strict):
    """Matériel mobile partagé (ex. valise de PC portables, mannequin de soins)."""
    id: Id
    nom: Texte = ""
    quantite: int = Field(ge=0, le=10000)


class Enseignant(Strict):
    id: Id
    nom: Texte = ""
    disponibilites: list[str] = Field(
        default_factory=list,
        description="motifs « jour:creneau » (ex. « lun:soir », « mar:* ») ; vide = toujours disponible",
    )
    max_periodes_semaine: int | None = Field(default=None, ge=0, le=200)
    implantations: list[Id] = Field(default_factory=list, description="vide = selon le paramètre de l'établissement")
    date_debut: date | None = Field(default=None, description="date d'engagement")
    date_fin: date | None = Field(default=None, description="fin de contrat, départ à la pension…")
    indisponibilites: list[Indisponibilite] = Field(default_factory=list)

    @field_validator("disponibilites")
    @classmethod
    def _dispos(cls, v: list[str]) -> list[str]:
        out = []
        for motif in v:
            m = re.fullmatch(r"\s*([a-zA-Z]{3})[a-zA-Z]*\s*:\s*([^;,|:<>\"'`\\\x00-\x1f]{1,64}?)\s*", motif)
            if not m:
                raise ValueError(f"disponibilité « {motif} » invalide (format jour:creneau, ex. lun:soir ou mar:*)")
            out.append(f"{_jours_valides([m.group(1)])[0]}:{m.group(2)}")
        return out

    def sous_contrat(self, d: date) -> bool:
        return (not self.date_debut or d >= self.date_debut) and (not self.date_fin or d <= self.date_fin)

    def disponible(self, d: date, creneau_id: str) -> bool:
        if not self.sous_contrat(d):
            return False
        if self.disponibilites:
            j = JOURS[d.weekday()]
            if f"{j}:{creneau_id}" not in self.disponibilites and f"{j}:*" not in self.disponibilites:
                return False
        return not any(i.bloque(d, creneau_id) for i in self.indisponibilites)


class Attribution(Strict):
    """Part d'un cours confiée à un ou plusieurs enseignants.

    * un enseignant  → cas courant ;
    * plusieurs attributions d'un enseignant chacune → cours **réparti** (ex. 40 p. + 20 p.) ;
    * une attribution à plusieurs enseignants → **co-enseignement** (présents ensemble à chaque séance).
    """
    enseignants: list[Id] = Field(min_length=1, max_length=10)
    periodes: int = Field(ge=1, le=5000)
    groupes: list[Id] = Field(default_factory=list, description="vide = tous les groupes qui suivent le module")


class Cours(Strict):
    """Cours (activité d'enseignement) d'un module."""
    id: Id
    nom: Texte = ""
    periodes: int = Field(ge=1, le=5000)
    equipements: list[Id] = Field(default_factory=list, description="équipement fixe requis dans le local")
    ressources: list[Id] = Field(default_factory=list, description="matériel mobile requis (1 unité par séance)")
    max_seances_semaine: int | None = Field(default=None, ge=1, le=50)
    rythme: Rythme | None = Field(default=None, description="vide = rythme par défaut du calendrier")
    bloc: str | None = Field(default=None, description="taille des séances ; vide = valeur par défaut de l'établissement")
    implantation: Id | None = Field(default=None, description="vide = implantation du groupe")
    locaux: list[Id] = Field(default_factory=list, description="locaux autorisés ; vide = tout local compatible")
    periodes_distanciel: int = Field(default=0, ge=0, le=5000, description="périodes données à distance (sans local)")
    attributions: list[Attribution] = Field(min_length=1, max_length=50)

    @field_validator("bloc", mode="before")
    @classmethod
    def _bloc(cls, v):
        return None if v in (None, "") else _bloc_valide(v)

    def attributions_pour(self, groupe_id: str) -> list[Attribution]:
        propres = [a for a in self.attributions if groupe_id in a.groupes]
        return propres or [a for a in self.attributions if not a.groupes]


class Module(Strict):
    """Module / unité d'enseignement : regroupe un ou plusieurs cours."""
    id: Id
    nom: Texte = ""
    prerequis: list[Id] = Field(default_factory=list, description="modules à terminer avant de commencer celui-ci")
    cours: list[Cours] = Field(min_length=1, max_length=50)

    @property
    def periodes(self) -> int:
        return sum(c.periodes for c in self.cours)


class Groupe(Strict):
    id: Id
    nom: Texte = ""
    effectif: int = Field(ge=1, le=10000)
    modules: list[Id] = Field(default_factory=list)
    implantation: Id | None = Field(default=None, description="implantation où la formation est organisée")
    jours_cours: list[str] = Field(default_factory=list, description="vide = jours du calendrier")
    creneaux: list[Id] = Field(default_factory=list, description="vide = tous les créneaux")
    date_debut: date | None = None
    date_fin: date | None = None
    local_prefere: Id | None = Field(default=None, description="local attitré, utilisé en priorité")
    stages: list[Stage] = Field(default_factory=list)

    @field_validator("jours_cours")
    @classmethod
    def _valide_jours(cls, v: list[str]) -> list[str]:
        return _jours_valides(v)


class JeuDeDonnees(Strict):
    nom: Texte = "Sans nom"
    etablissement: Etablissement = Field(default_factory=Etablissement)
    calendrier: Calendrier
    locaux: list[Local] = Field(default_factory=list, max_length=MAX_ELEMENTS)
    ressources: list[Ressource] = Field(default_factory=list, max_length=MAX_ELEMENTS)
    enseignants: list[Enseignant] = Field(default_factory=list, max_length=MAX_ELEMENTS)
    modules: list[Module] = Field(default_factory=list, max_length=MAX_ELEMENTS)
    groupes: list[Groupe] = Field(default_factory=list, max_length=MAX_ELEMENTS)

    @model_validator(mode="after")
    def _references(self):
        erreurs: list[str] = []

        def uniques(nom, items):
            vus = set()
            for it in items:
                if it.id in vus:
                    erreurs.append(f"{nom} : identifiant en double « {it.id} »")
                vus.add(it.id)
            return vus

        creneaux = uniques("créneaux", self.calendrier.creneaux)
        locaux = uniques("locaux", self.locaux)
        ressources = uniques("ressources", self.ressources)
        enseignants = uniques("enseignants", self.enseignants)
        modules = uniques("modules", self.modules)
        uniques("cours", [c for m in self.modules for c in m.cours])
        groupes = uniques("groupes", self.groupes)

        implantations = uniques("implantations", self.etablissement.implantations)
        for j in self.calendrier.jours_cours:
            cr = sorted((c for c in self.calendrier.creneaux if c.ouverte(j)), key=lambda c: c.heure_debut)
            for a, b in zip(cr, cr[1:]):
                if b.heure_debut < a.heure_fin:
                    erreurs.append(f"plages {a.id} et {b.id} : les horaires se chevauchent le {j}")

        def implantation_connue(quoi: str, i: str | None):
            if i and i not in implantations:
                erreurs.append(f"{quoi} : implantation inconnue « {i} »")

        for l in self.locaux:
            implantation_connue(f"local {l.id}", l.implantation)
        for g in self.groupes:
            implantation_connue(f"groupe {g.id}", g.implantation)
        for e in self.enseignants:
            for i in e.implantations:
                implantation_connue(f"enseignant {e.id}", i)
            if e.date_debut and e.date_fin and e.date_fin < e.date_debut:
                erreurs.append(f"enseignant {e.id} : la fin de contrat précède l'engagement")

        for f in self.calendrier.fermetures:
            quoi = f"fermeture du {f.date_debut}" + (f" ({f.motif})" if f.motif else "")
            erreurs += [f"{quoi} : créneau inconnu « {c} »" for c in f.creneaux if c not in creneaux]
            for i in f.implantations:
                implantation_connue(quoi, i)

        for e in self.enseignants:
            for d in e.disponibilites:
                c = d.split(":")[1]
                if c != "*" and c not in creneaux:
                    erreurs.append(f"enseignant {e.id} : créneau inconnu « {c} » dans les disponibilités")
            for i in e.indisponibilites:
                erreurs += [f"enseignant {e.id} : créneau inconnu « {c} »" for c in i.creneaux if c not in creneaux]
        for l in self.locaux:
            for i in l.indisponibilites:
                erreurs += [f"local {l.id} : créneau inconnu « {c} »" for c in i.creneaux if c not in creneaux]

        suivi_par: dict[str, list[str]] = {}
        for g in self.groupes:
            for m in g.modules:
                suivi_par.setdefault(m, []).append(g.id)

        for m in self.modules:
            erreurs += [f"module {m.id} : prérequis inconnu « {p} »" for p in m.prerequis if p not in modules]
            if m.id in m.prerequis:
                erreurs.append(f"module {m.id} : ne peut pas être son propre prérequis")
            for c in m.cours:
                erreurs += [f"cours {c.id} : ressource inconnue « {r} »" for r in c.ressources if r not in ressources]
                erreurs += [f"cours {c.id} : local inconnu « {l} »" for l in c.locaux if l not in locaux]
                implantation_connue(f"cours {c.id}", c.implantation)
                if c.periodes_distanciel > c.periodes:
                    erreurs.append(f"cours {c.id} : plus de périodes en distanciel ({c.periodes_distanciel}) que de périodes de cours")
                for a in c.attributions:
                    erreurs += [f"cours {c.id} : enseignant inconnu « {e} »" for e in a.enseignants if e not in enseignants]
                    erreurs += [f"cours {c.id} : groupe inconnu « {g} » dans une attribution" for g in a.groupes if g not in groupes]
                    if len(set(a.enseignants)) != len(a.enseignants):
                        erreurs.append(f"cours {c.id} : enseignant répété dans une même attribution")
                for gid in suivi_par.get(m.id, []):
                    attr = c.attributions_pour(gid)
                    total = sum(a.periodes for a in attr)
                    if not attr:
                        erreurs.append(f"cours {c.id} / groupe {gid} : aucune attribution d'enseignant")
                    elif total != c.periodes:
                        erreurs.append(f"cours {c.id} / groupe {gid} : attributions = {total} périodes "
                                       f"pour {c.periodes} périodes de cours")
        for g in self.groupes:
            erreurs += [f"groupe {g.id} : module inconnu « {m} »" for m in g.modules if m not in modules]
            erreurs += [f"groupe {g.id} : créneau inconnu « {c} »" for c in g.creneaux if c not in creneaux]
            if g.local_prefere and g.local_prefere not in locaux:
                erreurs.append(f"groupe {g.id} : local préféré inconnu « {g.local_prefere} »")
            if len(set(g.modules)) != len(g.modules):
                erreurs.append(f"groupe {g.id} : module listé plusieurs fois")

        if erreurs:
            raise ValueError("\n".join(erreurs[:50]) + (f"\n… et {len(erreurs) - 50} autres erreurs" if len(erreurs) > 50 else ""))
        return self

    def module(self, mid: str) -> Module:
        return next(m for m in self.modules if m.id == mid)
