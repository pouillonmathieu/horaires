"""Crée (ou réinitialise) un compte administrateur. Usage : python -m scripts.creer_admin <identifiant>"""
import getpass
import sys

from app import config
from app.chiffrement import Chiffreur
from app.securite import hacher_mot_de_passe, politique_mot_de_passe
from app.stockage import Stockage


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage : python -m scripts.creer_admin <identifiant>")
    ident = sys.argv[1]
    mdp = getpass.getpass("Mot de passe : ")
    if getpass.getpass("Confirmation : ") != mdp:
        sys.exit("Les mots de passe ne correspondent pas.")
    if err := politique_mot_de_passe(mdp, ident):
        sys.exit(err)
    store = Stockage(config.DB_PATH, Chiffreur(config.cle_maitre()))
    u = store.utilisateur_par_identifiant(ident)
    if u:
        store.maj_mot_de_passe(u["id"], hacher_mot_de_passe(mdp))
        print(f"Mot de passe de {ident} réinitialisé.")
    else:
        store.creer_utilisateur(ident, hacher_mot_de_passe(mdp), "admin")
        print(f"Administrateur {ident} créé.")
    store.journaliser("console", "creation_admin", ident)


if __name__ == "__main__":
    main()
