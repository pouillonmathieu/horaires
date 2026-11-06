"""Authentification, sessions, contrôle d'accès par rôle, protection CSRF et anti-force brute."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request

from . import config

_ph = PasswordHasher()  # Argon2id, paramètres recommandés OWASP
_HASH_LEURRE = _ph.hash(secrets.token_urlsafe(16))  # temps constant si l'identifiant n'existe pas

ROLES = {"lecteur": 0, "planificateur": 1, "admin": 2}
NOM_COOKIE = "__Host-session" if config.COOKIE_SECURE else "session"


def hacher_mot_de_passe(mdp: str) -> str:
    return _ph.hash(mdp)


def verifier_mot_de_passe(hash_mdp: str | None, mdp: str) -> bool:
    try:
        return _ph.verify(hash_mdp or _HASH_LEURRE, mdp) and hash_mdp is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def politique_mot_de_passe(mdp: str, identifiant: str = "") -> str | None:
    if len(mdp) < config.MOT_DE_PASSE_LONGUEUR_MIN:
        return f"Le mot de passe doit contenir au moins {config.MOT_DE_PASSE_LONGUEUR_MIN} caractères."
    if identifiant and identifiant.lower() in mdp.lower():
        return "Le mot de passe ne doit pas contenir l'identifiant."
    if len(set(mdp)) < 6:
        return "Le mot de passe est trop répétitif."
    return None


def empreinte_jeton(jeton: str) -> str:
    return hashlib.sha256(jeton.encode()).hexdigest()


class AntiForceBrute:
    """Blocage temporaire après N échecs, par identifiant et par adresse IP."""

    def __init__(self):
        self._echecs: dict[str, list[float]] = defaultdict(list)
        self._v = threading.Lock()

    def _cles(self, identifiant: str, ip: str):
        return [f"u:{identifiant.lower()}", f"ip:{ip}"]

    def bloque(self, identifiant: str, ip: str) -> bool:
        limite = time.time() - config.BLOCAGE_MIN * 60
        with self._v:
            for k in self._cles(identifiant, ip):
                self._echecs[k] = [t for t in self._echecs[k] if t > limite]
                seuil = config.TENTATIVES_MAX * (4 if k.startswith("ip:") else 1)
                if len(self._echecs[k]) >= seuil:
                    return True
        return False

    def echec(self, identifiant: str, ip: str):
        with self._v:
            for k in self._cles(identifiant, ip):
                self._echecs[k].append(time.time())

    def succes(self, identifiant: str):
        with self._v:
            self._echecs.pop(f"u:{identifiant.lower()}", None)


@dataclass
class Utilisateur:
    id: str
    identifiant: str
    role: str
    csrf: str
    jeton_hash: str

    def peut(self, role_min: str) -> bool:
        return ROLES[self.role] >= ROLES[role_min]


def ip_client(request: Request) -> str:
    return request.client.host if request.client else "?"


def utilisateur_courant(request: Request) -> Utilisateur:
    store = request.app.state.stockage
    jeton = request.cookies.get(NOM_COOKIE)
    if not jeton:
        raise HTTPException(401, "Authentification requise")
    jh = empreinte_jeton(jeton)
    s = store.session(jh)
    t = time.time()
    if not s or t - s["vu_le"] > config.SESSION_INACTIVITE_MIN * 60 or t - s["cree_le"] > config.SESSION_MAX_HEURES * 3600:
        if s:
            store.supprimer_session(jh)
        raise HTTPException(401, "Session expirée")
    u = store.utilisateur(s["utilisateur_id"])
    if not u or not u["actif"]:
        store.supprimer_session(jh)
        raise HTTPException(401, "Compte désactivé")
    # CSRF : toute requête modifiante doit renvoyer le jeton lié à la session dans un en-tête.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), s["csrf"]):
            raise HTTPException(403, "Jeton CSRF invalide")
    store.toucher_session(jh, t)
    return Utilisateur(u["id"], u["identifiant"], u["role"], s["csrf"], jh)


def exige(role_min: str):
    def dep(u: Utilisateur = Depends(utilisateur_courant)) -> Utilisateur:
        if not u.peut(role_min):
            raise HTTPException(403, "Droits insuffisants")
        return u

    return dep
