"""Import / export des données au format Excel (et JSON), export des horaires (Excel, iCalendar).

Format Excel : un onglet par type d'objet. Les listes se saisissent dans une cellule,
séparées par « ; » (ex. « lun;mar;jeu »). Voir le modèle téléchargeable dans l'application.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, time as dtime, timezone
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .modeles import JeuDeDonnees

MAX_DECOMPRESSE = 50 * 1024 * 1024  # protection contre les « zip bombs »

ONGLETS: dict[str, list[str]] = {
    "Parametres": ["cle", "valeur"],
    "Creneaux": ["id", "libelle", "heure_debut", "heure_fin", "periodes"],
    "Fermetures": ["date_debut", "date_fin", "motif"],
    "Locaux": ["id", "nom", "capacite", "equipements"],
    "Ressources": ["id", "nom", "quantite"],
    "Enseignants": ["id", "nom", "disponibilites", "max_periodes_semaine"],
    "Indisponibilites": ["type", "id", "date_debut", "date_fin", "creneaux", "motif"],
    "Modules": ["id", "nom", "periodes", "prerequis", "equipements", "ressources", "enseignants", "max_seances_semaine"],
    "Groupes": ["id", "nom", "effectif", "modules", "jours_cours", "creneaux", "date_debut", "date_fin"],
    "Stages": ["groupe", "date_debut", "date_fin", "jours", "motif"],
}

AIDE = {
    "Parametres": "nom, date_debut, date_fin (AAAA-MM-JJ), jours_cours (ex. lun;mar;mer;jeu;ven)",
    "Creneaux": "Plages horaires d'une journée. « periodes » = nombre de périodes de cours du créneau.",
    "Fermetures": "Jours fériés, congés et vacances (une ligne par période ; un jour isolé : début = fin).",
    "Locaux": "Capacité en places assises ; équipements fixes séparés par « ; » (ex. pc;projecteur).",
    "Ressources": "Matériel mobile partagé, en nombre d'unités (1 unité mobilisée par séance).",
    "Enseignants": "Disponibilités : jour:creneau séparés par « ; » (ex. lun:soir;mer:*). Vide = toujours disponible.",
    "Indisponibilites": "type = enseignant ou local. Créneaux vides = journée entière.",
    "Modules": "Prérequis, équipements, ressources et enseignants habilités séparés par « ; ».",
    "Groupes": "Modules du parcours séparés par « ; ». Jours/créneaux vides = ceux du calendrier.",
    "Stages": "Périodes de stage d'un groupe (pas de cours). Jours vides = tous les jours de la période.",
}


class ErreurImport(ValueError):
    pass


# ---------------------------------------------------------------------------- lecture
def _liste(v: Any) -> list[str]:
    if v is None:
        return []
    return [p.strip() for p in str(v).replace(",", ";").split(";") if p.strip()]


def _date(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def _heure(v: Any) -> str:
    if isinstance(v, (dtime, datetime)):
        return v.strftime("%H:%M")
    s = str(v).strip().replace("h", ":")
    return s if len(s) == 5 else s.zfill(5)


def _entier(v: Any) -> int | None:
    if v in (None, ""):
        return None
    return int(float(v))


def _lignes(wb, onglet: str) -> list[dict]:
    if onglet not in wb.sheetnames:
        return []
    ws = wb[onglet]
    rows = ws.iter_rows(values_only=True)
    try:
        entetes = [str(h).strip().lower() if h is not None else "" for h in next(rows)]
    except StopIteration:
        return []
    out = []
    for i, row in enumerate(rows, start=2):
        if i > 20000:
            raise ErreurImport(f"onglet {onglet} : trop de lignes")
        if row is None or all(c in (None, "") for c in row):
            continue
        d = {h: row[j] if j < len(row) else None for j, h in enumerate(entetes) if h}
        if str(d.get(entetes[0]) or "").startswith("#"):
            continue  # ligne de commentaire
        d["_ligne"] = i
        out.append(d)
    return out


def _verifier_zip(contenu: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(contenu)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_DECOMPRESSE:
                raise ErreurImport("fichier Excel trop volumineux une fois décompressé")
    except zipfile.BadZipFile as e:
        raise ErreurImport("ce fichier n'est pas un classeur Excel (.xlsx) valide") from e


def excel_vers_donnees(contenu: bytes) -> JeuDeDonnees:
    _verifier_zip(contenu)
    wb = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    try:
        params = {str(r["cle"]).strip(): r.get("valeur") for r in _lignes(wb, "Parametres") if r.get("cle")}
        if not {"date_debut", "date_fin"} <= params.keys():
            raise ErreurImport("onglet Parametres : date_debut et date_fin sont obligatoires")

        indispos = {"enseignant": {}, "local": {}}
        for r in _lignes(wb, "Indisponibilites"):
            t = str(r.get("type") or "").strip().lower()
            if t not in indispos:
                raise ErreurImport(f"onglet Indisponibilites ligne {r['_ligne']} : type doit être « enseignant » ou « local »")
            indispos[t].setdefault(str(r["id"]).strip(), []).append({
                "date_debut": _date(r.get("date_debut")),
                "date_fin": _date(r.get("date_fin")) or _date(r.get("date_debut")),
                "creneaux": _liste(r.get("creneaux")),
                "motif": str(r.get("motif") or ""),
            })
        stages: dict[str, list] = {}
        for r in _lignes(wb, "Stages"):
            stages.setdefault(str(r["groupe"]).strip(), []).append({
                "date_debut": _date(r.get("date_debut")),
                "date_fin": _date(r.get("date_fin")) or _date(r.get("date_debut")),
                "jours": _liste(r.get("jours")),
                "motif": str(r.get("motif") or ""),
            })

        brut = {
            "nom": str(params.get("nom") or "Import Excel"),
            "calendrier": {
                "date_debut": _date(params["date_debut"]),
                "date_fin": _date(params["date_fin"]),
                **({"jours_cours": _liste(params["jours_cours"])} if params.get("jours_cours") else {}),
                "creneaux": [
                    {"id": str(r["id"]), "libelle": str(r.get("libelle") or ""), "heure_debut": _heure(r["heure_debut"]),
                     "heure_fin": _heure(r["heure_fin"]), "periodes": _entier(r["periodes"])}
                    for r in _lignes(wb, "Creneaux")
                ],
                "fermetures": [
                    {"date_debut": _date(r["date_debut"]), "date_fin": _date(r.get("date_fin")) or _date(r["date_debut"]),
                     "motif": str(r.get("motif") or "")}
                    for r in _lignes(wb, "Fermetures")
                ],
            },
            "locaux": [
                {"id": str(r["id"]), "nom": str(r.get("nom") or ""), "capacite": _entier(r["capacite"]),
                 "equipements": _liste(r.get("equipements")), "indisponibilites": indispos["local"].get(str(r["id"]), [])}
                for r in _lignes(wb, "Locaux")
            ],
            "ressources": [
                {"id": str(r["id"]), "nom": str(r.get("nom") or ""), "quantite": _entier(r["quantite"])}
                for r in _lignes(wb, "Ressources")
            ],
            "enseignants": [
                {"id": str(r["id"]), "nom": str(r.get("nom") or ""), "disponibilites": _liste(r.get("disponibilites")),
                 "max_periodes_semaine": _entier(r.get("max_periodes_semaine")),
                 "indisponibilites": indispos["enseignant"].get(str(r["id"]), [])}
                for r in _lignes(wb, "Enseignants")
            ],
            "modules": [
                {"id": str(r["id"]), "nom": str(r.get("nom") or ""), "periodes": _entier(r["periodes"]),
                 "prerequis": _liste(r.get("prerequis")), "equipements": _liste(r.get("equipements")),
                 "ressources": _liste(r.get("ressources")), "enseignants": _liste(r.get("enseignants")),
                 "max_seances_semaine": _entier(r.get("max_seances_semaine"))}
                for r in _lignes(wb, "Modules")
            ],
            "groupes": [
                {"id": str(r["id"]), "nom": str(r.get("nom") or ""), "effectif": _entier(r["effectif"]),
                 "modules": _liste(r.get("modules")), "jours_cours": _liste(r.get("jours_cours")),
                 "creneaux": _liste(r.get("creneaux")), "date_debut": _date(r.get("date_debut")),
                 "date_fin": _date(r.get("date_fin")), "stages": stages.get(str(r["id"]), [])}
                for r in _lignes(wb, "Groupes")
            ],
        }
    except KeyError as e:
        raise ErreurImport(f"colonne obligatoire manquante : {e.args[0]}") from e
    except (TypeError, ValueError) as e:
        if isinstance(e, ErreurImport):
            raise
        raise ErreurImport(f"valeur illisible : {e}") from e
    finally:
        wb.close()
    return JeuDeDonnees.model_validate(brut)


def json_vers_donnees(contenu: bytes) -> JeuDeDonnees:
    return JeuDeDonnees.model_validate_json(contenu)


# ---------------------------------------------------------------------------- écriture
_ENTETE = PatternFill("solid", fgColor="1F4E79")


def _feuille(wb: Workbook, nom: str, entetes: list[str], lignes: list[list], aide: str = ""):
    ws = wb.create_sheet(nom)
    ws.append(entetes)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = _ENTETE
    for l in lignes:
        ws.append(l)
    if aide:
        ws.append([])
        ws.append([f"# {aide}"])
        ws.cell(ws.max_row, 1).font = Font(italic=True, color="777777")
    for i, h in enumerate(entetes, start=1):
        largeur = max([len(str(h))] + [len(str(l[i - 1])) for l in lignes if i - 1 < len(l) and l[i - 1] is not None])
        ws.column_dimensions[get_column_letter(i)].width = min(60, largeur + 3)
    ws.freeze_panes = "A2"
    return ws


def _j(v: list) -> str:
    return ";".join(v)


def donnees_vers_excel(ds: JeuDeDonnees) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    cal = ds.calendrier
    _feuille(wb, "Parametres", ONGLETS["Parametres"], [
        ["nom", ds.nom], ["date_debut", cal.date_debut], ["date_fin", cal.date_fin], ["jours_cours", _j(cal.jours_cours)],
    ], AIDE["Parametres"])
    _feuille(wb, "Creneaux", ONGLETS["Creneaux"],
             [[c.id, c.libelle, c.heure_debut, c.heure_fin, c.periodes] for c in cal.creneaux], AIDE["Creneaux"])
    _feuille(wb, "Fermetures", ONGLETS["Fermetures"],
             [[f.date_debut, f.date_fin, f.motif] for f in cal.fermetures], AIDE["Fermetures"])
    _feuille(wb, "Locaux", ONGLETS["Locaux"],
             [[l.id, l.nom, l.capacite, _j(l.equipements)] for l in ds.locaux], AIDE["Locaux"])
    _feuille(wb, "Ressources", ONGLETS["Ressources"],
             [[r.id, r.nom, r.quantite] for r in ds.ressources], AIDE["Ressources"])
    _feuille(wb, "Enseignants", ONGLETS["Enseignants"],
             [[e.id, e.nom, _j(e.disponibilites), e.max_periodes_semaine] for e in ds.enseignants], AIDE["Enseignants"])
    indis = [["enseignant", e.id, i.date_debut, i.date_fin, _j(i.creneaux), i.motif] for e in ds.enseignants for i in e.indisponibilites]
    indis += [["local", l.id, i.date_debut, i.date_fin, _j(i.creneaux), i.motif] for l in ds.locaux for i in l.indisponibilites]
    _feuille(wb, "Indisponibilites", ONGLETS["Indisponibilites"], indis, AIDE["Indisponibilites"])
    _feuille(wb, "Modules", ONGLETS["Modules"], [
        [m.id, m.nom, m.periodes, _j(m.prerequis), _j(m.equipements), _j(m.ressources), _j(m.enseignants), m.max_seances_semaine]
        for m in ds.modules
    ], AIDE["Modules"])
    _feuille(wb, "Groupes", ONGLETS["Groupes"], [
        [g.id, g.nom, g.effectif, _j(g.modules), _j(g.jours_cours), _j(g.creneaux), g.date_debut, g.date_fin]
        for g in ds.groupes
    ], AIDE["Groupes"])
    _feuille(wb, "Stages", ONGLETS["Stages"],
             [[g.id, s.date_debut, s.date_fin, _j(s.jours), s.motif] for g in ds.groupes for s in g.stages], AIDE["Stages"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def horaire_vers_excel(resultat: dict, titre: str) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    cols = ["date", "jour", "heure_debut", "heure_fin", "periodes", "groupe", "groupe_nom", "module", "module_nom", "local", "enseignant"]
    _feuille(wb, "Seances", cols, [[s[c] for c in cols] for s in resultat["seances"]])
    cols_s = ["groupe", "module", "module_nom", "enseignant", "periodes_requises", "periodes_planifiees",
              "periodes_manquantes", "nb_seances", "premiere_seance", "derniere_seance"]
    ws = _feuille(wb, "Synthese", cols_s, [[s[c] for c in cols_s] for s in resultat["synthese"]])
    rouge = PatternFill("solid", fgColor="F8CBAD")
    for row in ws.iter_rows(min_row=2, max_row=1 + len(resultat["synthese"])):
        if row[6].value:
            for c in row:
                c.fill = rouge
    for gid in sorted({s["groupe"] for s in resultat["seances"]}):
        cols_g = ["date", "jour", "heure_debut", "heure_fin", "module_nom", "local", "enseignant"]
        _feuille(wb, f"Groupe {gid}"[:31], cols_g, [[s[c] for c in cols_g] for s in resultat["seances"] if s["groupe"] == gid])
    wb.properties.title = titre[:200]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ics_echap(t: str) -> str:
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Brussels
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


def horaire_vers_ics(seances: list[dict], horaire_id: str) -> bytes:
    maintenant = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lignes = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Horaires//Planification//FR", "CALSCALE:GREGORIAN", VTIMEZONE]
    for i, s in enumerate(seances):
        d = s["date"].replace("-", "")
        lignes += [
            "BEGIN:VEVENT",
            f"UID:{horaire_id}-{i}@horaires",
            f"DTSTAMP:{maintenant}",
            f"DTSTART;TZID=Europe/Brussels:{d}T{s['heure_debut'].replace(':', '')}00",
            f"DTEND;TZID=Europe/Brussels:{d}T{s['heure_fin'].replace(':', '')}00",
            f"SUMMARY:{_ics_echap(s['module_nom'] or s['module'])} ({_ics_echap(s['groupe'])})",
            f"LOCATION:{_ics_echap(s['local'])}",
            f"DESCRIPTION:{_ics_echap('Enseignant : ' + str(s['enseignant']))}",
            "END:VEVENT",
        ]
    lignes.append("END:VCALENDAR")
    return ("\r\n".join(lignes) + "\r\n").encode()


def donnees_vers_json(ds: JeuDeDonnees) -> bytes:
    return json.dumps(ds.model_dump(mode="json"), ensure_ascii=False, indent=2).encode()
