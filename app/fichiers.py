"""Import / export des données au format Excel (et JSON), export des horaires (Excel, iCalendar).

Format Excel : un onglet par type d'objet. Les listes se saisissent dans une cellule,
séparées par « ; » (ex. « lun;mar;jeu »). Voir le modèle téléchargeable dans l'application.
"""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
import zipfile
from datetime import date, datetime, time as dtime, timezone
from typing import Any, Callable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .modeles import JeuDeDonnees

MAX_DECOMPRESSE = 50 * 1024 * 1024  # protection contre les « zip bombs »

ONGLETS: dict[str, list[str]] = {
    "Parametres": ["cle", "valeur"],
    "Implantations": ["id", "nom", "adresse"],
    "Creneaux": ["id", "libelle", "heure_debut", "heure_fin", "periodes", "jours"],
    "Fermetures": ["date_debut", "date_fin", "motif", "type", "creneaux", "implantations"],
    "Locaux": ["id", "nom", "implantation", "capacite", "equipements"],
    "Ressources": ["id", "nom", "quantite"],
    "Enseignants": ["id", "nom", "disponibilites", "max_periodes_semaine", "implantations", "date_debut", "date_fin"],
    "Indisponibilites": ["type", "id", "date_debut", "date_fin", "creneaux", "motif"],
    "Modules": ["id", "nom", "prerequis"],
    "Cours": ["id", "module", "nom", "periodes", "equipements", "ressources", "max_seances_semaine", "rythme", "bloc",
              "implantation", "locaux", "periodes_distanciel"],
    "Attributions": ["cours", "enseignants", "periodes", "groupes"],
    "Groupes": ["id", "nom", "effectif", "modules", "implantation", "jours_cours", "creneaux", "date_debut", "date_fin",
                "local_prefere"],
    "Stages": ["groupe", "date_debut", "date_fin", "jours", "motif"],
    # Formats « une ligne par lien » (facultatifs), plus proches d'un export de base de données :
    "Parcours": ["groupe", "module"],
    "Prerequis": ["module", "prerequis"],
}

AIDE = {
    "Parametres": "nom, date_debut, date_fin (année scolaire), jours_cours (ex. lun;mar;mer;jeu;ven;sam), "
                  "cours_pendant_conges (oui/non), rythme_defaut (souple/fixe), bloc_defaut (plage, 2, 3, 4, 5, 6, 8, 9, "
                  "modulable), multi_sites_meme_jour (oui/non), delai_deplacement_min, enseignants_toutes_implantations "
                  "(oui/non), max_periodes_semaine_defaut",
    "Implantations": "Sites de l'établissement (laisser vide s'il n'y en a qu'un).",
    "Creneaux": "Plages horaires (matin, après-midi, soir…) et leur nombre de périodes. « jours » : seulement ces "
                "jours (ex. sam pour une plage du samedi) ; vide = tous les jours de cours.",
    "Fermetures": "type : ferie (toujours fermé), conges (congé scolaire, ouvert si cours_pendant_conges = oui) ou "
                  "special (jour spécial : formation continue, fête de l'école…). creneaux : seulement ces plages "
                  "(ex. aprem) ; implantations : seulement ces sites. Vide = toute la journée, tous les sites.",
    "Locaux": "Implantation, capacité en places assises, équipements fixes séparés par « ; » (ex. pc;projecteur).",
    "Ressources": "Matériel mobile partagé, en nombre d'unités (1 unité mobilisée par séance).",
    "Enseignants": "Disponibilités : jour:plage séparés par « ; » (ex. lun:soir;mer:*), vide = toujours. Implantations "
                   "où il enseigne (vide = selon le paramètre). date_debut / date_fin : engagement et fin de contrat.",
    "Indisponibilites": "type = enseignant ou local. Créneaux vides = journée entière.",
    "Modules": "Un module (unité d'enseignement) regroupe des cours. Prérequis : modules à terminer avant, séparés par « ; ».",
    "Cours": "Cours d'un module : périodes, besoins en local/matériel. Rythme : fixe ou souple. Bloc : taille des "
             "séances (plage, 2…9, modulable ; vide = défaut). Implantation : si différente de celle du groupe. Locaux : "
             "locaux imposés. periodes_distanciel : périodes données à distance (cours hybride, sans local).",
    "Attributions": "Qui donne le cours. Plusieurs lignes pour un cours = cours réparti (ex. 40 p. + 20 p.). "
                    "Plusieurs enseignants sur une ligne = co-enseignement (présents ensemble). "
                    "« groupes » : attribution propre à certains groupes (vide = tous).",
    "Groupes": "Modules du parcours séparés par « ; ». Implantation de la formation. Jours/plages vides = ceux du "
               "calendrier. Local préféré facultatif.",
    "Stages": "Périodes de stage d'un groupe (pas de cours). Jours vides = tous les jours de la période.",
    "Parcours": "Facultatif : une ligne par module suivi par un groupe (alternative à la colonne Groupes.modules).",
    "Prerequis": "Facultatif : une ligne par prérequis (alternative à la colonne Modules.prerequis).",
}

MAX_FICHIERS_ZIP = 50
MAX_LIGNES = 50_000


class ErreurImport(ValueError):
    pass


# ---------------------------------------------------------------------------- lecture
def _liste(v: Any) -> list[str]:
    if v is None:
        return []
    return [p.strip() for p in re.split(r"[;,|]", str(v)) if p.strip()]


def _date(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?:\s.*)?", s)
    if m:  # format belge JJ/MM/AAAA
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else s


def _heure(v: Any) -> str:
    if isinstance(v, (dtime, datetime)):
        return v.strftime("%H:%M")
    s = str(v).strip().lower().replace("h", ":").replace(".", ":")
    if s.endswith(":"):
        s += "00"
    h, _, m = s.partition(":")
    return f"{int(h):02d}:{int(m or 0):02d}" if h.isdigit() and (m or "0").isdigit() else s


def _entier(v: Any) -> int | None:
    if v in (None, ""):
        return None
    return int(float(str(v).replace(",", ".").strip()))


def _booleen(v: Any) -> bool | None:
    if v in (None, ""):
        return None
    if isinstance(v, bool):
        return v
    s = _cle(str(v))
    if s in ("oui", "o", "1", "true", "vrai", "yes", "y", "x"):
        return True
    if s in ("non", "n", "0", "false", "faux", "no"):
        return False
    raise ErreurImport(f"valeur « {v} » : attendu oui ou non")


def _texte(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _cle(nom: str) -> str:
    """Normalise un nom d'onglet, de fichier ou de colonne : minuscules, sans accents ni espaces."""
    nom = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode().lower().strip()
    return re.sub(r"[^a-z0-9]+", "_", nom).strip("_")


ONGLETS_NORMALISES = {_cle(o): o for o in ONGLETS}


def _verifier_zip(contenu: bytes, quoi: str = "classeur Excel (.xlsx)") -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile as e:
        raise ErreurImport(f"ce fichier n'est pas un {quoi} valide") from e
    if sum(i.file_size for i in z.infolist()) > MAX_DECOMPRESSE:
        raise ErreurImport("fichier trop volumineux une fois décompressé")
    return z


def _lignes_excel(wb, onglet: str) -> list[dict]:
    nom = next((n for n in wb.sheetnames if _cle(n) == _cle(onglet)), None)
    if nom is None:
        return []
    rows = wb[nom].iter_rows(values_only=True)
    try:
        entetes = [_cle(str(h)) if h is not None else "" for h in next(rows)]
    except StopIteration:
        return []
    out = []
    for i, row in enumerate(rows, start=2):
        if i > MAX_LIGNES:
            raise ErreurImport(f"onglet {onglet} : trop de lignes")
        if row is None or all(c in (None, "") for c in row):
            continue
        d = {h: row[j] if j < len(row) else None for j, h in enumerate(entetes) if h}
        if str(d.get(entetes[0]) or "").startswith("#"):
            continue  # ligne de commentaire
        d["_ligne"] = i
        out.append(d)
    return out


def _decoder_csv(contenu: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return contenu.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ErreurImport("encodage du fichier CSV non reconnu (attendu : UTF-8 ou Windows-1252)")


def _lignes_csv(contenu: bytes, nom: str) -> list[dict]:
    texte = _decoder_csv(contenu)
    premiere = texte.split("\n", 1)[0]
    sep = max([";", ",", "\t"], key=premiere.count)
    lecteur = csv.reader(io.StringIO(texte), delimiter=sep)
    try:
        entetes = [_cle(h) for h in next(lecteur)]
    except StopIteration:
        return []
    out = []
    for i, row in enumerate(lecteur, start=2):
        if i > MAX_LIGNES:
            raise ErreurImport(f"{nom} : trop de lignes")
        if not any(c.strip() for c in row) or row[0].strip().startswith("#"):
            continue
        d = {h: (row[j].strip() or None) if j < len(row) else None for j, h in enumerate(entetes) if h}
        d["_ligne"] = i
        out.append(d)
    return out


def _avec_complement(lire: Callable[[str], list[dict]], complement: JeuDeDonnees | None) -> Callable[[str], list[dict]]:
    """Complète les tables (ou les colonnes) absentes de l'import avec celles d'un jeu existant.

    Permet de ne réinjecter que l'export du logiciel de gestion : créneaux, locaux, disponibilités,
    congés… restent ceux déjà saisis. Une table présente mais sans une colonne (ex. enseignants.csv
    sans « disponibilites ») est complétée colonne par colonne, en rapprochant les identifiants.
    """
    if complement is None:
        return lire
    tables = donnees_vers_tables(complement)

    def lire_complete(onglet: str) -> list[dict]:
        lignes = lire(onglet)
        ref = [dict(zip(ONGLETS[onglet], l)) for l in tables.get(onglet, [])]
        if not lignes:
            return [{**r, "_ligne": f"{i + 1} (reprise du jeu existant)"} for i, r in enumerate(ref)]
        cle = "cle" if onglet == "Parametres" else ("id" if "id" in ONGLETS[onglet] else None)
        if cle and ref:
            par_cle = {_texte(r[cle]): r for r in ref}
            presentes = set().union(*(l.keys() for l in lignes))
            manquantes = [c for c in ONGLETS[onglet] if c not in presentes]
            for l in lignes:
                r = par_cle.get(_texte(l.get(cle)))
                if r:
                    for c in manquantes:
                        l[c] = r.get(c)
            if onglet == "Parametres":
                connues = {_cle(_texte(l.get("cle"))) for l in lignes}
                lignes += [{**r, "_ligne": "(reprise)"} for r in ref if _cle(_texte(r["cle"])) not in connues]
        return lignes

    return lire_complete


def tables_vers_donnees(lire: Callable[[str], list[dict]], nom_defaut: str,
                        complement: JeuDeDonnees | None = None) -> JeuDeDonnees:
    """Construit le jeu de données à partir de tables (onglets Excel ou fichiers CSV)."""
    lire = _avec_complement(lire, complement)

    def obligatoire(r: dict, col: str, table: str):
        v = r.get(col)
        if v in (None, ""):
            raise ErreurImport(f"{table}, ligne {r['_ligne']} : la colonne « {col} » est vide ou absente")
        return v

    try:
        params = {_cle(str(r["cle"])): r.get("valeur") for r in lire("Parametres") if r.get("cle")}
        if not {"date_debut", "date_fin"} <= params.keys():
            raise ErreurImport("Parametres : date_debut et date_fin sont obligatoires")

        indispos = {"enseignant": {}, "local": {}}
        for r in lire("Indisponibilites"):
            t = _cle(_texte(r.get("type")))
            if t not in indispos:
                raise ErreurImport(f"Indisponibilites, ligne {r['_ligne']} : type doit être « enseignant » ou « local »")
            indispos[t].setdefault(_texte(obligatoire(r, "id", "Indisponibilites")), []).append({
                "date_debut": _date(r.get("date_debut")),
                "date_fin": _date(r.get("date_fin")) or _date(r.get("date_debut")),
                "creneaux": _liste(r.get("creneaux")), "motif": _texte(r.get("motif")),
            })
        stages: dict[str, list] = {}
        for r in lire("Stages"):
            stages.setdefault(_texte(obligatoire(r, "groupe", "Stages")), []).append({
                "date_debut": _date(r.get("date_debut")),
                "date_fin": _date(r.get("date_fin")) or _date(r.get("date_debut")),
                "jours": _liste(r.get("jours")), "motif": _texte(r.get("motif")),
            })
        attributions: dict[str, list] = {}
        for r in lire("Attributions"):
            attributions.setdefault(_texte(obligatoire(r, "cours", "Attributions")), []).append({
                "enseignants": _liste(obligatoire(r, "enseignants", "Attributions")),
                "periodes": _entier(obligatoire(r, "periodes", "Attributions")),
                "groupes": _liste(r.get("groupes")),
            })
        cours_par_module: dict[str, list] = {}
        for r in lire("Cours"):
            cid = _texte(obligatoire(r, "id", "Cours"))
            cours_par_module.setdefault(_texte(obligatoire(r, "module", "Cours")), []).append({
                "id": cid, "nom": _texte(r.get("nom")), "periodes": _entier(obligatoire(r, "periodes", "Cours")),
                "equipements": _liste(r.get("equipements")), "ressources": _liste(r.get("ressources")),
                "max_seances_semaine": _entier(r.get("max_seances_semaine")),
                "rythme": _cle(_texte(r.get("rythme"))) or None,
                "bloc": _texte(r.get("bloc")) or None,
                "implantation": _texte(r.get("implantation")) or None,
                "locaux": _liste(r.get("locaux")),
                "periodes_distanciel": _entier(r.get("periodes_distanciel")) or 0,
                "attributions": attributions.pop(cid, []),
            })
        if attributions:
            raise ErreurImport(f"Attributions : cours inconnu(s) : {', '.join(sorted(attributions)[:10])}")

        prerequis_lignes: dict[str, list[str]] = {}
        for r in lire("Prerequis"):
            prerequis_lignes.setdefault(_texte(obligatoire(r, "module", "Prerequis")), []).append(
                _texte(obligatoire(r, "prerequis", "Prerequis")))
        modules = []
        for r in lire("Modules"):
            mid = _texte(obligatoire(r, "id", "Modules"))
            pre = _liste(r.get("prerequis"))
            pre += [p for p in prerequis_lignes.pop(mid, []) if p not in pre]
            modules.append({"id": mid, "nom": _texte(r.get("nom")), "prerequis": pre,
                            "cours": cours_par_module.pop(mid, [])})
        if prerequis_lignes:
            raise ErreurImport(f"Prerequis : module(s) inconnu(s) : {', '.join(sorted(prerequis_lignes)[:10])}")
        parcours: dict[str, list[str]] = {}
        for r in lire("Parcours"):
            parcours.setdefault(_texte(obligatoire(r, "groupe", "Parcours")), []).append(
                _texte(obligatoire(r, "module", "Parcours")))
        if cours_par_module:
            raise ErreurImport(f"Cours : module(s) inconnu(s) : {', '.join(sorted(cours_par_module)[:10])}")

        etab = {"implantations": [
            {"id": _texte(obligatoire(r, "id", "Implantations")), "nom": _texte(r.get("nom")), "adresse": _texte(r.get("adresse"))}
            for r in lire("Implantations")]}
        for cle in ("multi_sites_meme_jour", "enseignants_toutes_implantations"):
            if _booleen(params.get(cle)) is not None:
                etab[cle] = _booleen(params[cle])
        for cle in ("delai_deplacement_min", "max_periodes_semaine_defaut"):
            if _entier(params.get(cle)) is not None:
                etab[cle] = _entier(params[cle])
        if params.get("bloc_defaut") not in (None, ""):
            etab["bloc_defaut"] = _texte(params["bloc_defaut"])
        brut = {
            "nom": _texte(params.get("nom")) or nom_defaut,
            "etablissement": etab,
            "calendrier": {
                "date_debut": _date(params["date_debut"]),
                "date_fin": _date(params["date_fin"]),
                **({"jours_cours": _liste(params["jours_cours"])} if params.get("jours_cours") else {}),
                **({"rythme_defaut": _cle(_texte(params["rythme_defaut"]))} if params.get("rythme_defaut") else {}),
                **({"cours_pendant_conges": _booleen(params["cours_pendant_conges"])}
                   if _booleen(params.get("cours_pendant_conges")) is not None else {}),
                "creneaux": [
                    {"id": _texte(obligatoire(r, "id", "Creneaux")), "libelle": _texte(r.get("libelle")),
                     "heure_debut": _heure(obligatoire(r, "heure_debut", "Creneaux")),
                     "heure_fin": _heure(obligatoire(r, "heure_fin", "Creneaux")),
                     "periodes": _entier(obligatoire(r, "periodes", "Creneaux")), "jours": _liste(r.get("jours"))}
                    for r in lire("Creneaux")
                ],
                "fermetures": [
                    {"date_debut": _date(obligatoire(r, "date_debut", "Fermetures")),
                     "date_fin": _date(r.get("date_fin")) or _date(r["date_debut"]), "motif": _texte(r.get("motif")),
                     "type": _type_fermeture(r.get("type")), "creneaux": _liste(r.get("creneaux")),
                     "implantations": _liste(r.get("implantations"))}
                    for r in lire("Fermetures")
                ],
            },
            "locaux": [
                {"id": _texte(obligatoire(r, "id", "Locaux")), "nom": _texte(r.get("nom")),
                 "implantation": _texte(r.get("implantation")) or None,
                 "capacite": _entier(obligatoire(r, "capacite", "Locaux")), "equipements": _liste(r.get("equipements")),
                 "indisponibilites": indispos["local"].get(_texte(r["id"]), [])}
                for r in lire("Locaux")
            ],
            "ressources": [
                {"id": _texte(obligatoire(r, "id", "Ressources")), "nom": _texte(r.get("nom")),
                 "quantite": _entier(obligatoire(r, "quantite", "Ressources"))}
                for r in lire("Ressources")
            ],
            "enseignants": [
                {"id": _texte(obligatoire(r, "id", "Enseignants")), "nom": _texte(r.get("nom")),
                 "disponibilites": _liste(r.get("disponibilites")),
                 "max_periodes_semaine": _entier(r.get("max_periodes_semaine")),
                 "implantations": _liste(r.get("implantations")),
                 "date_debut": _date(r.get("date_debut")), "date_fin": _date(r.get("date_fin")),
                 "indisponibilites": indispos["enseignant"].get(_texte(r["id"]), [])}
                for r in lire("Enseignants")
            ],
            "modules": modules,
            "groupes": [
                {"id": _texte(obligatoire(r, "id", "Groupes")), "nom": _texte(r.get("nom")),
                 "effectif": _entier(obligatoire(r, "effectif", "Groupes")),
                 "implantation": _texte(r.get("implantation")) or None,
                 "modules": list(dict.fromkeys(_liste(r.get("modules")) + parcours.pop(_texte(r["id"]), []))),
                 "jours_cours": _liste(r.get("jours_cours")),
                 "creneaux": _liste(r.get("creneaux")), "date_debut": _date(r.get("date_debut")),
                 "date_fin": _date(r.get("date_fin")), "local_prefere": _texte(r.get("local_prefere")) or None,
                 "stages": stages.get(_texte(r["id"]), [])}
                for r in lire("Groupes")
            ],
        }
        if parcours:
            raise ErreurImport(f"Parcours : groupe(s) inconnu(s) : {', '.join(sorted(parcours)[:10])}")
    except (TypeError, ValueError) as e:
        if isinstance(e, ErreurImport):
            raise
        raise ErreurImport(f"valeur illisible : {e}") from e
    return JeuDeDonnees.model_validate(brut)


def _type_fermeture(v: Any) -> str:
    s = _cle(_texte(v))
    if s in ("", "ferie", "jour_ferie", "f"):
        return "ferie"
    if s in ("conges", "conge", "conges_scolaires", "vacances", "c"):
        return "conges"
    if s in ("special", "speciale", "jour_special", "journee_speciale", "evenement", "formation", "formation_continue",
             "journee_pedagogique", "s"):
        return "special"
    raise ErreurImport(f"type de fermeture « {v} » : attendu ferie, conges ou special")


def excel_vers_donnees(contenu: bytes, complement: JeuDeDonnees | None = None) -> JeuDeDonnees:
    _verifier_zip(contenu).close()
    wb = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    try:
        return tables_vers_donnees(lambda onglet: _lignes_excel(wb, onglet), "Import Excel", complement)
    finally:
        wb.close()


def csv_vers_donnees(fichiers: list[tuple[str, bytes]], complement: JeuDeDonnees | None = None) -> JeuDeDonnees:
    """Un fichier CSV par table, nommé comme l'onglet Excel correspondant (ex. cours.csv, attributions.csv).

    `fichiers` peut contenir des CSV et/ou des archives ZIP de CSV.
    """
    tables: dict[str, bytes] = {}
    inconnus = []

    def ajouter(nom: str, contenu: bytes):
        base = _cle(nom.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0])
        if base not in ONGLETS_NORMALISES:
            inconnus.append(nom)
            return
        tables[ONGLETS_NORMALISES[base]] = contenu

    for nom, contenu in fichiers:
        if contenu.startswith(b"PK\x03\x04"):
            with _verifier_zip(contenu, "fichier ZIP") as z:
                membres = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith(".csv")]
                if len(membres) > MAX_FICHIERS_ZIP:
                    raise ErreurImport("archive ZIP : trop de fichiers")
                for i in membres:
                    ajouter(i.filename, z.read(i))
        else:
            ajouter(nom, contenu)
    if inconnus:
        raise ErreurImport(f"fichier(s) CSV non reconnu(s) : {', '.join(inconnus[:10])}. "
                           f"Noms attendus : {', '.join(o.lower() + '.csv' for o in ONGLETS)}")
    manquants = [o for o in ("Parametres", "Creneaux", "Locaux", "Enseignants", "Modules", "Cours", "Attributions", "Groupes")
                 if o not in tables]
    if manquants and complement is None:
        raise ErreurImport(f"fichier(s) CSV manquant(s) : {', '.join(m.lower() + '.csv' for m in manquants)}. "
                           "Vous pouvez aussi compléter l'import avec un jeu de données existant.")
    cache = {o: _lignes_csv(c, f"{o.lower()}.csv") for o, c in tables.items()}
    return tables_vers_donnees(lambda onglet: cache.get(onglet, []), "Import CSV", complement)


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


def _oui(v: bool) -> str:
    return "oui" if v else "non"


def donnees_vers_tables(ds: JeuDeDonnees) -> dict[str, list[list]]:
    cal = ds.calendrier
    et = ds.etablissement
    indis = [["enseignant", e.id, i.date_debut, i.date_fin, _j(i.creneaux), i.motif] for e in ds.enseignants for i in e.indisponibilites]
    indis += [["local", l.id, i.date_debut, i.date_fin, _j(i.creneaux), i.motif] for l in ds.locaux for i in l.indisponibilites]
    return {
        "Parametres": [["nom", ds.nom], ["date_debut", cal.date_debut], ["date_fin", cal.date_fin],
                       ["jours_cours", _j(cal.jours_cours)], ["cours_pendant_conges", _oui(cal.cours_pendant_conges)],
                       ["rythme_defaut", cal.rythme_defaut], ["bloc_defaut", et.bloc_defaut],
                       ["multi_sites_meme_jour", _oui(et.multi_sites_meme_jour)],
                       ["delai_deplacement_min", et.delai_deplacement_min],
                       ["enseignants_toutes_implantations", _oui(et.enseignants_toutes_implantations)],
                       ["max_periodes_semaine_defaut", et.max_periodes_semaine_defaut]],
        "Implantations": [[i.id, i.nom, i.adresse] for i in et.implantations],
        "Creneaux": [[c.id, c.libelle, c.heure_debut, c.heure_fin, c.periodes, _j(c.jours)] for c in cal.creneaux],
        "Fermetures": [[f.date_debut, f.date_fin, f.motif, f.type, _j(f.creneaux), _j(f.implantations)]
                       for f in cal.fermetures],
        "Locaux": [[l.id, l.nom, l.implantation, l.capacite, _j(l.equipements)] for l in ds.locaux],
        "Ressources": [[r.id, r.nom, r.quantite] for r in ds.ressources],
        "Enseignants": [[e.id, e.nom, _j(e.disponibilites), e.max_periodes_semaine, _j(e.implantations), e.date_debut,
                         e.date_fin] for e in ds.enseignants],
        "Indisponibilites": indis,
        "Modules": [[m.id, m.nom, _j(m.prerequis)] for m in ds.modules],
        "Cours": [[c.id, m.id, c.nom, c.periodes, _j(c.equipements), _j(c.ressources), c.max_seances_semaine, c.rythme,
                   c.bloc, c.implantation, _j(c.locaux), c.periodes_distanciel or None]
                  for m in ds.modules for c in m.cours],
        "Attributions": [[c.id, _j(a.enseignants), a.periodes, _j(a.groupes)]
                         for m in ds.modules for c in m.cours for a in c.attributions],
        "Groupes": [[g.id, g.nom, g.effectif, _j(g.modules), g.implantation, _j(g.jours_cours), _j(g.creneaux), g.date_debut,
                     g.date_fin, g.local_prefere] for g in ds.groupes],
        "Stages": [[g.id, s.date_debut, s.date_fin, _j(s.jours), s.motif] for g in ds.groupes for s in g.stages],
    }


def donnees_vers_excel(ds: JeuDeDonnees) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for onglet, lignes in donnees_vers_tables(ds).items():
        _feuille(wb, onglet, ONGLETS[onglet], lignes, AIDE[onglet])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def donnees_vers_csv_zip(ds: JeuDeDonnees) -> bytes:
    """Un CSV par table (UTF-8 avec BOM, séparateur « ; » : s'ouvre directement dans Excel)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for onglet, lignes in donnees_vers_tables(ds).items():
            texte = io.StringIO()
            w = csv.writer(texte, delimiter=";", lineterminator="\r\n")
            w.writerow(ONGLETS[onglet])
            w.writerows([["" if v is None else v for v in l] for l in lignes])
            z.writestr(f"{onglet.lower()}.csv", "\ufeff" + texte.getvalue())
    return buf.getvalue()


def horaire_vers_excel(resultat: dict, titre: str) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    cols = ["date", "jour", "heure_debut", "heure_fin", "periodes", "groupe", "groupe_nom", "module", "module_nom",
            "cours", "cours_nom", "implantation", "local", "enseignant", "distanciel"]
    _feuille(wb, "Seances", cols, [[s[c] for c in cols] for s in resultat["seances"]])
    cols_s = ["groupe", "module", "module_nom", "cours", "cours_nom", "enseignant", "distanciel", "periodes_requises",
              "periodes_planifiees", "periodes_manquantes", "creneaux_hebdo", "locaux", "nb_seances",
              "premiere_seance", "derniere_seance"]
    lignes = [[_j(s[c]) if isinstance(s[c], list) else s[c] for c in cols_s] for s in resultat["synthese"]]
    ws = _feuille(wb, "Synthese", cols_s, lignes)
    rouge = PatternFill("solid", fgColor="F8CBAD")
    i_manque = cols_s.index("periodes_manquantes")
    for row in ws.iter_rows(min_row=2, max_row=1 + len(lignes)):
        if row[i_manque].value:
            for c in row:
                c.fill = rouge
    for gid in sorted({s["groupe"] for s in resultat["seances"]}):
        cols_g = ["date", "jour", "heure_debut", "heure_fin", "module_nom", "cours_nom", "local", "enseignant"]
        _feuille(wb, re.sub(r"[\\/?*\[\]:]", "-", f"Groupe {gid}")[:31], cols_g,
                 [[s[c] for c in cols_g] for s in resultat["seances"] if s["groupe"] == gid])
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
            f"SUMMARY:{_ics_echap(s.get('cours_nom') or s['module_nom'] or s['module'])} ({_ics_echap(s['groupe'])})",
            f"LOCATION:{_ics_echap(s['local'])}",
            f"DESCRIPTION:{_ics_echap('Enseignant : ' + str(s['enseignant']))}",
            "END:VEVENT",
        ]
    lignes.append("END:VCALENDAR")
    return ("\r\n".join(lignes) + "\r\n").encode()


def donnees_vers_json(ds: JeuDeDonnees) -> bytes:
    return json.dumps(ds.model_dump(mode="json"), ensure_ascii=False, indent=2).encode()
