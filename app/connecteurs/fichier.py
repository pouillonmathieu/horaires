from __future__ import annotations

from ..fichiers import ErreurImport, excel_vers_donnees, json_vers_donnees
from ..modeles import JeuDeDonnees
from .base import Connecteur

SIGNATURE_XLSX = b"PK\x03\x04"


class ConnecteurFichier(Connecteur):
    """Import d'un fichier téléversé. Le type est déterminé par le contenu, pas par l'extension."""

    nom = "fichier"

    def __init__(self, contenu: bytes, nom_fichier: str = ""):
        self.contenu = contenu
        self.nom_fichier = nom_fichier

    def lire_donnees(self) -> JeuDeDonnees:
        if self.contenu.startswith(SIGNATURE_XLSX):
            return excel_vers_donnees(self.contenu)
        if self.contenu.lstrip()[:1] == b"{":
            return json_vers_donnees(self.contenu)
        raise ErreurImport("format non reconnu : fournissez un classeur Excel (.xlsx) ou un fichier JSON")
