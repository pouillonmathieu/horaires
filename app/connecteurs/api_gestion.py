"""Gabarit de connecteur vers le logiciel de gestion de l'établissement (phase 2).

À compléter une fois connus le logiciel et son mode d'accès (API REST, vues SQL en
lecture seule, export planifié…). Principes de sécurité à respecter :
  * compte de service dédié, en lecture seule pour l'import ;
  * secret d'accès fourni par variable d'environnement / coffre-fort, jamais dans le code ;
  * TLS obligatoire avec vérification du certificat ;
  * aucune donnée nominative des apprenants : seuls les effectifs sont nécessaires.
"""
from __future__ import annotations

import os

from ..modeles import JeuDeDonnees
from .base import Connecteur


class ConnecteurApiGestion(Connecteur):
    nom = "api_gestion"

    def __init__(self, url_base: str | None = None):
        self.url_base = url_base or os.environ.get("GESTION_URL", "")
        self._jeton = os.environ.get("GESTION_JETON")  # jamais journalisé
        if not self.url_base.startswith("https://"):
            raise ValueError("Le connecteur exige une URL HTTPS")

    def lire_donnees(self) -> JeuDeDonnees:
        # 1. GET /formations, /unites, /enseignants, /locaux, /groupes, /calendrier
        # 2. Correspondance des champs vers JeuDeDonnees (table de correspondance à définir)
        # 3. JeuDeDonnees.model_validate(...) : même validation stricte que l'import de fichiers
        raise NotImplementedError("Correspondance à définir avec le logiciel de gestion")

    def publier_horaire(self, horaire: dict) -> None:
        raise NotImplementedError("Publication à définir avec le logiciel de gestion")
