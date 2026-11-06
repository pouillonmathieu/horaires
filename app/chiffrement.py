"""Chiffrement authentifié AES-256-GCM des données au repos.

Chaque enregistrement est chiffré avec un nonce aléatoire et lié à son contexte
(table + identifiant) via les données associées (AAD) : un bloc chiffré déplacé
ou recopié dans un autre enregistrement est détecté et refusé au déchiffrement.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = b"\x01"


class Chiffreur:
    def __init__(self, cle: bytes):
        if len(cle) != 32:
            raise ValueError("clé AES-256 attendue")
        self._aead = AESGCM(cle)

    def chiffrer(self, clair: bytes, contexte: str) -> bytes:
        nonce = os.urandom(12)
        return VERSION + nonce + self._aead.encrypt(nonce, clair, contexte.encode())

    def dechiffrer(self, blob: bytes, contexte: str) -> bytes:
        if blob[:1] != VERSION:
            raise ValueError("format de chiffrement inconnu")
        return self._aead.decrypt(blob[1:13], blob[13:], contexte.encode())
