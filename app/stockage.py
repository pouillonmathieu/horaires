"""Persistance SQLite. Tout contenu métier (noms, données, horaires) est chiffré AES-256-GCM.

Seules des métadonnées techniques (identifiants aléatoires, dates, statut) sont en clair.
Le journal d'audit est chaîné par empreintes SHA-256 : toute modification ou suppression
d'une ligne a posteriori est détectable (`verifier_journal`).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .chiffrement import Chiffreur

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA secure_delete=ON;
CREATE TABLE IF NOT EXISTS utilisateurs (
    id TEXT PRIMARY KEY, identifiant TEXT UNIQUE NOT NULL, hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','planificateur','lecteur')),
    actif INTEGER NOT NULL DEFAULT 1, cree_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    jeton_hash TEXT PRIMARY KEY, utilisateur_id TEXT NOT NULL REFERENCES utilisateurs(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL, cree_le REAL NOT NULL, vu_le REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jeux (
    id TEXT PRIMARY KEY, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL,
    entete BLOB NOT NULL, contenu BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS horaires (
    id TEXT PRIMARY KEY, jeu_id TEXT NOT NULL REFERENCES jeux(id) ON DELETE CASCADE,
    cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, statut TEXT NOT NULL, contenu BLOB
);
CREATE TABLE IF NOT EXISTS journal (
    n INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, utilisateur TEXT, action TEXT NOT NULL,
    cible TEXT, ip TEXT, precedent TEXT NOT NULL, empreinte TEXT NOT NULL
);
"""


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Stockage:
    def __init__(self, chemin: Path, chiffreur: Chiffreur):
        chemin.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(chemin, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._verrou = threading.RLock()
        self._c = chiffreur
        with self._verrou:
            self._db.executescript(SCHEMA)
        try:
            chemin.chmod(0o600)
        except OSError:
            pass

    def _x(self, sql: str, params=()):
        with self._verrou:
            return self._db.execute(sql, params)

    def _json_chiffre(self, obj, contexte: str) -> bytes:
        return self._c.chiffrer(json.dumps(obj, ensure_ascii=False, default=str).encode(), contexte)

    def _json_clair(self, blob: bytes, contexte: str):
        return json.loads(self._c.dechiffrer(blob, contexte))

    # ------------------------------------------------------------------ utilisateurs
    def creer_utilisateur(self, identifiant: str, hash_mdp: str, role: str) -> str:
        uid = str(uuid.uuid4())
        self._x("INSERT INTO utilisateurs VALUES (?,?,?,?,1,?)", (uid, identifiant, hash_mdp, role, maintenant()))
        return uid

    def utilisateur_par_identifiant(self, identifiant: str):
        return self._x("SELECT * FROM utilisateurs WHERE identifiant=?", (identifiant,)).fetchone()

    def utilisateur(self, uid: str):
        return self._x("SELECT * FROM utilisateurs WHERE id=?", (uid,)).fetchone()

    def utilisateurs(self):
        return [dict(r) | {"hash": None} for r in self._x("SELECT * FROM utilisateurs ORDER BY identifiant")]

    def maj_mot_de_passe(self, uid: str, hash_mdp: str):
        self._x("UPDATE utilisateurs SET hash=? WHERE id=?", (hash_mdp, uid))

    def desactiver_utilisateur(self, uid: str):
        self._x("UPDATE utilisateurs SET actif=0 WHERE id=?", (uid,))
        self._x("DELETE FROM sessions WHERE utilisateur_id=?", (uid,))

    def nb_admins(self) -> int:
        return self._x("SELECT COUNT(*) FROM utilisateurs WHERE role='admin' AND actif=1").fetchone()[0]

    # ------------------------------------------------------------------ sessions
    def creer_session(self, jeton_hash: str, uid: str, csrf: str, t: float):
        self._x("INSERT INTO sessions VALUES (?,?,?,?,?)", (jeton_hash, uid, csrf, t, t))

    def session(self, jeton_hash: str):
        return self._x("SELECT * FROM sessions WHERE jeton_hash=?", (jeton_hash,)).fetchone()

    def toucher_session(self, jeton_hash: str, t: float):
        self._x("UPDATE sessions SET vu_le=? WHERE jeton_hash=?", (t, jeton_hash))

    def supprimer_session(self, jeton_hash: str):
        self._x("DELETE FROM sessions WHERE jeton_hash=?", (jeton_hash,))

    def purger_sessions(self, avant_vu: float, avant_cree: float):
        self._x("DELETE FROM sessions WHERE vu_le<? OR cree_le<?", (avant_vu, avant_cree))

    # ------------------------------------------------------------------ jeux de données
    def creer_jeu(self, uid: str, entete: dict, contenu: dict) -> str:
        jid = str(uuid.uuid4())
        self._x("INSERT INTO jeux VALUES (?,?,?,?,?)", (
            jid, uid, maintenant(), self._json_chiffre(entete, f"jeux/entete/{jid}"), self._json_chiffre(contenu, f"jeux/{jid}"),
        ))
        return jid

    def jeux(self) -> list[dict]:
        out = []
        for r in self._x("SELECT id, cree_par, cree_le, entete FROM jeux ORDER BY cree_le DESC"):
            out.append({"id": r["id"], "cree_le": r["cree_le"], **self._json_clair(r["entete"], f"jeux/entete/{r['id']}")})
        return out

    def jeu(self, jid: str) -> dict | None:
        r = self._x("SELECT * FROM jeux WHERE id=?", (jid,)).fetchone()
        if not r:
            return None
        return {"id": jid, "cree_le": r["cree_le"], **self._json_clair(r["entete"], f"jeux/entete/{jid}"),
                "contenu": self._json_clair(r["contenu"], f"jeux/{jid}")}

    def supprimer_jeu(self, jid: str) -> bool:
        return self._x("DELETE FROM jeux WHERE id=?", (jid,)).rowcount > 0

    # ------------------------------------------------------------------ horaires
    def creer_horaire(self, jeu_id: str, uid: str) -> str:
        hid = str(uuid.uuid4())
        self._x("INSERT INTO horaires VALUES (?,?,?,?,?,NULL)", (hid, jeu_id, uid, maintenant(), "en_cours"))
        return hid

    def terminer_horaire(self, hid: str, statut: str, contenu: dict):
        self._x("UPDATE horaires SET statut=?, contenu=? WHERE id=?", (statut, self._json_chiffre(contenu, f"horaires/{hid}"), hid))

    def horaires(self, jeu_id: str | None = None) -> list[dict]:
        sql, p = "SELECT id, jeu_id, cree_le, statut FROM horaires", ()
        if jeu_id:
            sql, p = sql + " WHERE jeu_id=?", (jeu_id,)
        return [dict(r) for r in self._x(sql + " ORDER BY cree_le DESC", p)]

    def horaire(self, hid: str) -> dict | None:
        r = self._x("SELECT * FROM horaires WHERE id=?", (hid,)).fetchone()
        if not r:
            return None
        d = {k: r[k] for k in ("id", "jeu_id", "cree_le", "statut")}
        d["contenu"] = self._json_clair(r["contenu"], f"horaires/{hid}") if r["contenu"] else None
        return d

    def marquer_horaires_interrompus(self):
        self._x("UPDATE horaires SET statut='interrompu' WHERE statut='en_cours'")

    # ------------------------------------------------------------------ journal d'audit
    def journaliser(self, utilisateur: str | None, action: str, cible: str = "", ip: str = ""):
        with self._verrou:
            der = self._db.execute("SELECT empreinte FROM journal ORDER BY n DESC LIMIT 1").fetchone()
            prec = der[0] if der else "0" * 64
            ts = maintenant()
            emp = hashlib.sha256("|".join([prec, ts, utilisateur or "", action, cible, ip]).encode()).hexdigest()
            self._db.execute("INSERT INTO journal (ts, utilisateur, action, cible, ip, precedent, empreinte) VALUES (?,?,?,?,?,?,?)",
                             (ts, utilisateur, action, cible, ip, prec, emp))

    def journal(self, limite: int = 500) -> list[dict]:
        return [dict(r) for r in self._x("SELECT n, ts, utilisateur, action, cible, ip FROM journal ORDER BY n DESC LIMIT ?", (limite,))]

    def verifier_journal(self) -> bool:
        prec = "0" * 64
        for r in self._x("SELECT * FROM journal ORDER BY n"):
            attendu = hashlib.sha256("|".join([prec, r["ts"], r["utilisateur"] or "", r["action"], r["cible"] or "", r["ip"] or ""]).encode()).hexdigest()
            if r["precedent"] != prec or r["empreinte"] != attendu:
                return False
            prec = r["empreinte"]
        return True
