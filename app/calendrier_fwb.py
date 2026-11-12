"""Calendrier scolaire officiel de la Fédération Wallonie-Bruxelles (enseignement obligatoire).

Depuis la réforme des rythmes scolaires (rentrée 2022), le calendrier suit des règles stables :

- rentrée : lundi de la semaine ISO 35 (fin août) ;
- vacances d'été : à partir du premier samedi qui suit le 1er juillet ;
- congé d'automne : 2 semaines, la seconde étant celle du 1er novembre ;
- vacances d'hiver : 2 semaines à partir de la semaine de Noël (la suivante si Noël tombe un week-end) ;
- vacances de printemps : 2 semaines à partir de la semaine du 1er mai ;
- congé de détente : 2 semaines en février-mars, fixées par le Gouvernement (table ci-dessous ;
  à défaut, 7 semaines après la reprise de janvier) ;
- jours fériés : Fête de la FWB (27/9), 2 et 11 novembre, lundi de Pâques, 1er mai, Ascension,
  lundi de Pentecôte, quand ils tombent un jour de semaine hors congé.

Ces règles reproduisent les calendriers publiés pour 2024-2025 à 2027-2028. Pour les autres années,
le résultat est marqué « provisoire » : à vérifier sur enseignement.be.
"""
from __future__ import annotations

from datetime import date, timedelta

# début du congé de détente, fixé chaque année par le Gouvernement (année de début de l'année scolaire)
DETENTE_PUBLIEE = {
    2024: date(2025, 2, 24),
    2025: date(2026, 2, 16),
    2026: date(2027, 2, 22),
    2027: date(2028, 2, 28),
}


def _lundi(d: date) -> date:
    return d - timedelta(days=d.weekday())


def paques(annee: int) -> date:
    """Dimanche de Pâques (calendrier grégorien, algorithme de Meeus/Jones/Butcher)."""
    a, b, c = annee % 19, annee // 100, annee % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mois = (h + l - 7 * m + 114) // 31
    jour = (h + l - 7 * m + 114) % 31 + 1
    return date(annee, mois, jour)


def _deux_semaines(lundi: date) -> tuple[date, date]:
    return lundi, lundi + timedelta(days=11)  # du lundi au vendredi de la semaine suivante


def calendrier_fwb(annee: int) -> dict:
    """Année scolaire qui commence en août `annee` : dates de début et de fin, congés et jours fériés."""
    debut = date.fromisocalendar(annee, 35, 1)
    premier_juillet = date(annee + 1, 7, 1)
    samedi_ete = premier_juillet + timedelta(days=(5 - premier_juillet.weekday()) % 7 or 7)
    fin = samedi_ete - timedelta(days=1)

    automne = _deux_semaines(_lundi(date(annee, 11, 1)) - timedelta(days=7))
    noel = date(annee, 12, 25)
    hiver = _deux_semaines(_lundi(noel) if noel.weekday() < 5 else _lundi(noel) + timedelta(days=7))
    reprise = hiver[1] + timedelta(days=3)
    detente = _deux_semaines(DETENTE_PUBLIEE.get(annee, reprise + timedelta(weeks=7)))
    printemps = _deux_semaines(_lundi(date(annee + 1, 5, 1)))
    conges = [
        (automne, "Congé d'automne (Toussaint)"),
        (hiver, "Vacances d'hiver (Noël)"),
        (detente, "Congé de détente (Carnaval)"),
        (printemps, "Vacances de printemps (Pâques)"),
    ]

    p = paques(annee + 1)
    feries = [
        (date(annee, 9, 27), "Fête de la Fédération Wallonie-Bruxelles"),
        (date(annee, 11, 2), "Commémoration des défunts"),
        (date(annee, 11, 11), "Armistice"),
        (p + timedelta(days=1), "Lundi de Pâques"),
        (date(annee + 1, 5, 1), "Fête du travail"),
        (p + timedelta(days=39), "Ascension"),
        (p + timedelta(days=50), "Lundi de Pentecôte"),
    ]
    en_conge = lambda d: any(a <= d <= b for (a, b), _ in conges)  # noqa: E731
    fermetures = [{"date_debut": a.isoformat(), "date_fin": b.isoformat(), "motif": m, "type": "conges"}
                  for (a, b), m in conges]
    fermetures += [{"date_debut": d.isoformat(), "date_fin": d.isoformat(), "motif": m, "type": "ferie"}
                   for d, m in feries if d.weekday() < 5 and not en_conge(d) and debut <= d <= fin]
    fermetures.sort(key=lambda f: f["date_debut"])
    publie = annee in DETENTE_PUBLIEE
    return {
        "annee": f"{annee}-{annee + 1}",
        "date_debut": debut.isoformat(),
        "date_fin": fin.isoformat(),
        "fermetures": fermetures,
        "provisoire": not publie,
        "note": ("Calendrier officiel publié par la Fédération Wallonie-Bruxelles." if publie else
                 "Calendrier calculé d'après les règles habituelles, pas encore publié : à vérifier sur enseignement.be."),
    }


def annee_scolaire_courante(aujourdhui: date | None = None) -> int:
    """Année de début de l'année scolaire en cours (ou de celle qui commence, à partir de juillet)."""
    d = aujourdhui or date.today()
    return d.year if d.month >= 7 else d.year - 1
