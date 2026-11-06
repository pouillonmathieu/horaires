"""Démarre une instance de TEST : crée (ou réinitialise) le compte « admin » avec un mot de passe
aléatoire affiché à l'écran, puis lance le serveur. Utilisé par GitHub Codespaces (.devcontainer).

Ne pas utiliser en production : la clé de chiffrement est générée dans le dossier de données.
"""
import os
import secrets
import sys

os.environ.setdefault("HORAIRES_MODE_DEV", "1")

from app import config  # noqa: E402
from app.chiffrement import Chiffreur  # noqa: E402
from app.securite import hacher_mot_de_passe  # noqa: E402
from app.stockage import Stockage  # noqa: E402


def main():
    store = Stockage(config.DB_PATH, Chiffreur(config.cle_maitre()))
    mdp = "Test-" + secrets.token_urlsafe(10)
    u = store.utilisateur_par_identifiant("admin")
    if u:
        store.maj_mot_de_passe(u["id"], hacher_mot_de_passe(mdp))
    else:
        store.creer_utilisateur("admin", hacher_mot_de_passe(mdp), "admin")
    store.journaliser("console", "demarrage_test", "admin")
    bandeau = "=" * 64
    print(f"\n{bandeau}\n  Horaires – instance de TEST (données fictives uniquement)\n"
          f"  Identifiant : admin\n  Mot de passe : {mdp}\n"
          f"  L'application s'ouvre dans un nouvel onglet (onglet « Ports » sinon).\n{bandeau}\n", flush=True)
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
                               "--proxy-headers", "--forwarded-allow-ips", "*"])


if __name__ == "__main__":
    main()
