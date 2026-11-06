import os
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.chiffrement import Chiffreur
from app.securite import hacher_mot_de_passe
from app.stockage import Stockage

MDP = "Un-mot-de-passe-solide-42"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.COOKIE_SECURE", False)
    import importlib

    import app.securite
    importlib.reload(app.securite)
    import app.main
    importlib.reload(app.main)
    store = Stockage(tmp_path / "t.db", Chiffreur(os.urandom(32)))
    store.creer_utilisateur("admin", hacher_mot_de_passe(MDP), "admin")
    store.creer_utilisateur("lecteur", hacher_mot_de_passe(MDP), "lecteur")
    with TestClient(app.main.creer_app(store), base_url="http://localhost", raise_server_exceptions=False) as c:
        yield c, store, tmp_path


def connecter(c, ident="admin"):
    r = c.post("/api/connexion", json={"identifiant": ident, "mot_de_passe": MDP})
    assert r.status_code == 200
    return r.json()["csrf"]


def test_acces_refuse_sans_session(env):
    c, *_ = env
    assert c.get("/api/jeux").status_code == 401
    assert c.get("/api/modele.xlsx").status_code == 401


def test_csrf_obligatoire(env):
    c, *_ = env
    csrf = connecter(c)
    assert c.post("/api/jeux/exemple").status_code == 403
    assert c.post("/api/jeux/exemple", headers={"X-CSRF-Token": "faux"}).status_code == 403
    assert c.post("/api/jeux/exemple", headers={"X-CSRF-Token": csrf}).status_code == 200


def test_roles(env):
    c, *_ = env
    csrf = connecter(c, "lecteur")
    h = {"X-CSRF-Token": csrf}
    assert c.post("/api/jeux/exemple", headers=h).status_code == 403
    assert c.get("/api/utilisateurs").status_code == 403
    assert c.get("/api/jeux").status_code == 200


def test_force_brute(env):
    c, *_ = env
    for _ in range(5):
        assert c.post("/api/connexion", json={"identifiant": "admin", "mot_de_passe": "faux"}).status_code == 401
    assert c.post("/api/connexion", json={"identifiant": "admin", "mot_de_passe": MDP}).status_code == 429


def test_en_tetes_securite(env):
    c, *_ = env
    r = c.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_chiffrement_au_repos(env):
    c, store, tmp = env
    h = {"X-CSRF-Token": connecter(c)}
    c.post("/api/jeux/exemple", headers=h)
    brut = (tmp / "t.db").read_bytes() + b"".join(p.read_bytes() for p in tmp.glob("t.db-*"))
    for mot in (b"Dupont", b"Comptabilit", b"AS-FOND", b"Aide-soignant"):
        assert mot not in brut, f"{mot!r} trouvé en clair dans la base"


def test_falsification_detectee(env):
    c, store, tmp = env
    h = {"X-CSRF-Token": connecter(c)}
    jid = c.post("/api/jeux/exemple", headers=h).json()["id"]
    db = sqlite3.connect(tmp / "t.db")
    blob = bytearray(db.execute("SELECT contenu FROM jeux WHERE id=?", (jid,)).fetchone()[0])
    blob[-1] ^= 1
    db.execute("UPDATE jeux SET contenu=? WHERE id=?", (bytes(blob), jid))
    db.commit()
    assert c.get(f"/api/jeux/{jid}").status_code == 500
    assert store.verifier_journal()
    db.execute("UPDATE journal SET action='autre' WHERE n=1")
    db.commit()
    assert not store.verifier_journal()


def test_import_invalide_lisible(env):
    c, *_ = env
    h = {"X-CSRF-Token": connecter(c)}
    r = c.post("/api/jeux", headers=h, files={"fichier": ("x.txt", b"bonjour", "text/plain")})
    assert r.status_code == 422 and "non reconnu" in r.json()["detail"]["message"]
    r = c.post("/api/jeux", headers=h, files={"fichier": ("x.json", b'{"nom": "x"}', "application/json")})
    assert r.status_code == 422 and r.json()["detail"]["erreurs"]


def test_parcours_complet(env):
    c, *_ = env
    h = {"X-CSRF-Token": connecter(c)}
    modele = c.get("/api/modele.xlsx").content
    jid = c.post("/api/jeux", headers=h, files={"fichier": ("m.xlsx", modele, "application/octet-stream")}).json()["id"]
    hid = c.post(f"/api/jeux/{jid}/planifier", headers=h, json={"duree_max_s": 15}).json()["id"]
    for _ in range(120):
        x = c.get(f"/api/horaires/{hid}").json()
        if x["statut"] != "en_cours":
            break
        time.sleep(0.5)
    assert x["statut"] in ("OPTIMAL", "REALISABLE")
    assert c.get(f"/api/horaires/{hid}/horaire.xlsx").status_code == 200
    assert b"BEGIN:VEVENT" in c.get(f"/api/horaires/{hid}/horaire.ics?type=enseignant&id=MERTENS").content
    assert b"BEGIN:VEVENT" in c.get(f"/api/horaires/{hid}/horaire.ics?type=groupe&id=AS1").content
    actions = [l["action"] for l in c.get("/api/journal").json()["lignes"]]
    assert {"connexion", "import_donnees", "planification", "export_horaire"} <= set(actions)


def test_import_csv_multiple(env):
    c, *_ = env
    h = {"X-CSRF-Token": connecter(c)}
    import io
    import zipfile
    z = c.get("/api/modele-csv.zip").content
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        fichiers = [("fichier", (n, zf.read(n), "text/csv")) for n in zf.namelist()]
    r = c.post("/api/jeux", headers=h, files=fichiers)
    assert r.status_code == 200, r.text
    assert r.json()["nb_cours"] == 14
    r = c.post("/api/jeux", headers=h, files={"fichier": ("export.zip", z, "application/zip")})
    assert r.status_code == 200


def test_accueil_modification_et_seance(env):
    c, *_ = env
    h = {"X-CSRF-Token": connecter(c)}
    vierge = {"nom": "Mon école", "etablissement": {"implantations": [{"id": "A"}]},
              "calendrier": {"date_debut": "2026-09-01", "date_fin": "2027-06-30",
                             "creneaux": [{"id": "soir", "heure_debut": "18:00", "heure_fin": "21:20", "periodes": 4}]}}
    r = c.post("/api/jeux/vierge", headers=h, json=vierge)
    assert r.status_code == 200 and r.json()["version"] == 1
    jid = c.post("/api/jeux/exemple", headers=h).json()["id"]
    j = c.get(f"/api/jeux/{jid}").json()
    d = j["donnees"]
    d["enseignants"][0]["date_fin"] = "2027-06-01"
    r = c.put(f"/api/jeux/{jid}/donnees", headers=h, json={"version": j["version"], "donnees": d})
    assert r.status_code == 200 and r.json()["version"] == j["version"] + 1
    # une seconde modification sur l'ancienne version est refusée (modification concurrente)
    assert c.put(f"/api/jeux/{jid}/donnees", headers=h, json={"version": j["version"], "donnees": d}).status_code == 409
    d["groupes"][0]["modules"] = ["INCONNU"]
    r = c.put(f"/api/jeux/{jid}/donnees", headers=h, json={"version": j["version"] + 1, "donnees": d})
    assert r.status_code == 422 and r.json()["detail"]["erreurs"]
    hid = c.post(f"/api/jeux/{jid}/planifier", headers=h, json={"duree_max_s": 10}).json()["id"]
    for _ in range(120):
        x = c.get(f"/api/horaires/{hid}").json()
        if x["statut"] != "en_cours":
            break
        time.sleep(0.5)
    s = x["contenu"]["seances"][0]
    assert x["contenu"]["donnees"]["nom"]  # instantané des données dans l'horaire
    r = c.patch(f"/api/horaires/{hid}/seances/{s['id']}", headers=h, json={"distanciel": True})
    assert r.status_code == 200 and r.json()["local"] == "Distanciel"
    r = c.patch(f"/api/horaires/{hid}/seances/{s['id']}", headers=h, json={"distanciel": False})
    assert r.json()["local"] == s["local"]  # le local libéré est retrouvé
