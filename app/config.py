"""Configuration par variables d'environnement (aucun secret dans le code)."""
from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path


def _bool(nom: str, defaut: bool) -> bool:
    return os.environ.get(nom, "1" if defaut else "0").lower() in ("1", "true", "oui", "yes")


DATA_DIR = Path(os.environ.get("HORAIRES_DATA_DIR", "./donnees")).resolve()
DB_PATH = DATA_DIR / "horaires.db"

# Cookies « Secure » : à laisser activé en production (HTTPS obligatoire).
COOKIE_SECURE = _bool("HORAIRES_COOKIE_SECURE", True)
SESSION_INACTIVITE_MIN = int(os.environ.get("HORAIRES_SESSION_INACTIVITE_MIN", "30"))
SESSION_MAX_HEURES = int(os.environ.get("HORAIRES_SESSION_MAX_HEURES", "8"))
TAILLE_MAX_IMPORT = int(os.environ.get("HORAIRES_TAILLE_MAX_IMPORT", str(20 * 1024 * 1024)))
DUREE_MAX_SOLVEUR_S = int(os.environ.get("HORAIRES_DUREE_MAX_SOLVEUR_S", "3600"))
MODE_DEV = _bool("HORAIRES_MODE_DEV", False)
# Mode aperçu (TEST UNIQUEMENT) : autorise l'affichage dans le volet d'aperçu d'une autre application
# (iframe) ; le cookie de session passe alors en SameSite=None + Secure. Jamais en production.
MODE_APERCU = _bool("HORAIRES_APERCU", False)
if MODE_APERCU:
    COOKIE_SECURE = True
MOT_DE_PASSE_LONGUEUR_MIN = 12
TENTATIVES_MAX = 5
BLOCAGE_MIN = 15


def cle_maitre() -> bytes:
    """Clé AES-256 de chiffrement des données au repos.

    Production : fournie par HORAIRES_CLE_MAITRE (base64, 32 octets), idéalement injectée
    par un coffre-fort de secrets (Vault, Azure Key Vault, secret Docker/Kubernetes).
    Mode développement uniquement : générée dans DATA_DIR/cle_maitre (droits 0600).
    """
    brut = os.environ.get("HORAIRES_CLE_MAITRE")
    if brut:
        cle = base64.b64decode(brut)
        if len(cle) != 32:
            raise RuntimeError("HORAIRES_CLE_MAITRE doit faire 32 octets encodés en base64")
        return cle
    if not MODE_DEV:
        raise RuntimeError(
            "HORAIRES_CLE_MAITRE absente. Générez-en une : "
            "python -c \"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\""
        )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fichier = DATA_DIR / "cle_maitre"
    if not fichier.exists():
        fd = os.open(fichier, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64encode(secrets.token_bytes(32)))
    return base64.b64decode(fichier.read_bytes())
