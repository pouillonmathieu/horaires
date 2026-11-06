"""Interface commune à tous les connecteurs.

Phase 1 : import de fichiers (Excel / JSON).
Phase 2 : un connecteur par logiciel de gestion (API REST, export base de données, SFTP…),
qui traduit le modèle du logiciel vers `JeuDeDonnees` et, en retour, publie l'horaire validé.
Le reste de l'application (moteur, interface, sécurité) ne change pas.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..modeles import JeuDeDonnees


class Connecteur(ABC):
    nom: str = "abstrait"

    @abstractmethod
    def lire_donnees(self) -> JeuDeDonnees:
        """Récupère et convertit les données de la source."""

    def publier_horaire(self, horaire: dict) -> None:
        """Renvoie l'horaire validé vers la source (optionnel selon le connecteur)."""
        raise NotImplementedError(f"Le connecteur {self.nom} ne permet pas la publication")
