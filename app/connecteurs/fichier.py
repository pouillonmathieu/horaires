from __future__ import annotations

from ..fichiers import ErreurImport, csv_vers_donnees, excel_vers_donnees, json_vers_donnees
from ..modeles import JeuDeDonnees
from .base import Connecteur

SIGNATURE_ZIP = b"PK\x03\x04"


class ConnecteurFichier(Connecteur):
    """Import de fichiers téléversés : un classeur Excel, un JSON, ou des CSV (séparés ou dans un ZIP).

    Le type est déterminé par le contenu, pas seulement par l'extension.
    """

    nom = "fichier"

    def __init__(self, fichiers: list[tuple[str, bytes]]):
        self.fichiers = fichiers

    def lire_donnees(self) -> JeuDeDonnees:
        if not self.fichiers:
            raise ErreurImport("aucun fichier reçu")
        if len(self.fichiers) == 1:
            nom, contenu = self.fichiers[0]
            if contenu.startswith(SIGNATURE_ZIP):
                if nom.lower().endswith(".zip") or b"[Content_Types].xml" not in contenu[:4096] and not nom.lower().endswith(".xlsx"):
                    return csv_vers_donnees(self.fichiers)
                return excel_vers_donnees(contenu)
            if contenu.lstrip(b"\xef\xbb\xbf \r\n\t")[:1] == b"{":
                return json_vers_donnees(contenu)
        if all(not c.startswith(SIGNATURE_ZIP) or n.lower().endswith(".zip") for n, c in self.fichiers):
            return csv_vers_donnees(self.fichiers)
        raise ErreurImport("format non reconnu : fournissez un classeur Excel (.xlsx), un fichier JSON, "
                           "ou les fichiers CSV (séparément ou dans un .zip)")
