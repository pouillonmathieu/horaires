"""Connecteurs vers les sources de données (fichiers aujourd'hui, logiciel de gestion demain)."""
from .base import Connecteur
from .fichier import ConnecteurFichier

CONNECTEURS: dict[str, type[Connecteur]] = {"fichier": ConnecteurFichier}
