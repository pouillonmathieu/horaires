"""Modèle de données métier : ce que l'on importe et ce que le solveur consomme.

Toutes les entrées sont validées strictement (champs inconnus refusés, identifiants
contrôlés, références croisées vérifiées) avant d'être stockées ou planifiées.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]

Id = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
Texte = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Heure = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

MAX_ELEMENTS = 5000


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


class Creneau(Strict):
    id: Id
    libelle: Texte = ""
    heure_debut: Heure
    heure_fin: Heure
    periodes: int = Field(ge=1, le=20, description="nombre de périodes de cours dans ce créneau")

    @model_validator(mode="after")
    def _ordre(self):
        if self.heure_fin <= self.heure_debut:
            raise ValueError(f"créneau {self.id} : l'heure de fin doit suivre l'heure de début")
        return self


class Calendrier(Strict):
    date_debut: date
    date_fin: date
    jours_cours: list[str] = Field(default_factory=lambda: ["lun", "mar", "mer", "jeu", "ven"])
    creneaux: list[Creneau] = Field(min_length=1, max_length=20)
    fermetures: list[Periode] = Field(default_factory=list, description="jours fériés, congés, vacances")

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


class Local(Strict):
    id: Id
    nom: Texte = ""
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
    indisponibilites: list[Indisponibilite] = Field(default_factory=list)

    @field_validator("disponibilites")
    @classmethod
    def _dispos(cls, v: list[str]) -> list[str]:
        out = []
        for motif in v:
            m = re.fullmatch(r"\s*([a-zA-Z]{3})[a-zA-Z]*\s*:\s*([A-Za-z0-9_.\-]{1,64}|\*)\s*", motif)
            if not m:
                raise ValueError(f"disponibilité « {motif} » invalide (format jour:creneau, ex. lun:soir ou mar:*)")
            out.append(f"{_jours_valides([m.group(1)])[0]}:{m.group(2)}")
        return out

    def disponible(self, d: date, creneau_id: str) -> bool:
        if self.disponibilites:
            j = JOURS[d.weekday()]
            if f"{j}:{creneau_id}" not in self.disponibilites and f"{j}:*" not in self.disponibilites:
                return False
        return not any(i.bloque(d, creneau_id) for i in self.indisponibilites)


class Module(Strict):
    """Unité d'enseignement / module de formation."""
    id: Id
    nom: Texte = ""
    periodes: int = Field(ge=1, le=5000)
    prerequis: list[Id] = Field(default_factory=list)
    equipements: list[Id] = Field(default_factory=list, description="équipement fixe requis dans le local")
    ressources: list[Id] = Field(default_factory=list, description="matériel mobile requis (1 unité par séance)")
    enseignants: list[Id] = Field(default_factory=list, description="enseignants habilités")
    max_seances_semaine: int | None = Field(default=None, ge=1, le=50)


class Groupe(Strict):
    id: Id
    nom: Texte = ""
    effectif: int = Field(ge=1, le=10000)
    modules: list[Id] = Field(min_length=1)
    jours_cours: list[str] = Field(default_factory=list, description="vide = jours du calendrier")
    creneaux: list[Id] = Field(default_factory=list, description="vide = tous les créneaux")
    date_debut: date | None = None
    date_fin: date | None = None
    stages: list[Stage] = Field(default_factory=list)

    @field_validator("jours_cours")
    @classmethod
    def _valide_jours(cls, v: list[str]) -> list[str]:
        return _jours_valides(v)


class JeuDeDonnees(Strict):
    nom: Texte = "Sans nom"
    calendrier: Calendrier
    locaux: list[Local] = Field(min_length=1, max_length=MAX_ELEMENTS)
    ressources: list[Ressource] = Field(default_factory=list, max_length=MAX_ELEMENTS)
    enseignants: list[Enseignant] = Field(min_length=1, max_length=MAX_ELEMENTS)
    modules: list[Module] = Field(min_length=1, max_length=MAX_ELEMENTS)
    groupes: list[Groupe] = Field(min_length=1, max_length=MAX_ELEMENTS)

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
        uniques("locaux", self.locaux)
        ressources = uniques("ressources", self.ressources)
        enseignants = uniques("enseignants", self.enseignants)
        modules = uniques("modules", self.modules)
        uniques("groupes", self.groupes)

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
        for m in self.modules:
            erreurs += [f"module {m.id} : prérequis inconnu « {p} »" for p in m.prerequis if p not in modules]
            erreurs += [f"module {m.id} : ressource inconnue « {r} »" for r in m.ressources if r not in ressources]
            erreurs += [f"module {m.id} : enseignant inconnu « {e} »" for e in m.enseignants if e not in enseignants]
            if m.id in m.prerequis:
                erreurs.append(f"module {m.id} : ne peut pas être son propre prérequis")
        for g in self.groupes:
            erreurs += [f"groupe {g.id} : module inconnu « {m} »" for m in g.modules if m not in modules]
            erreurs += [f"groupe {g.id} : créneau inconnu « {c} »" for c in g.creneaux if c not in creneaux]
            if len(set(g.modules)) != len(g.modules):
                erreurs.append(f"groupe {g.id} : module listé plusieurs fois")

        if erreurs:
            raise ValueError("\n".join(erreurs[:50]))
        return self

    # accès pratiques
    def module(self, mid: str) -> Module:
        return next(m for m in self.modules if m.id == mid)
