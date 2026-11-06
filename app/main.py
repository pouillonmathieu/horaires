"""Application web : API + interface."""
from __future__ import annotations

import logging
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import config
from .chiffrement import Chiffreur
from .connecteurs import ConnecteurFichier
from .exemple import jeu_exemple
from .fichiers import ErreurImport, donnees_vers_excel, donnees_vers_json, horaire_vers_excel, horaire_vers_ics
from .modeles import JeuDeDonnees
from .securite import (
    NOM_COOKIE, ROLES, AntiForceBrute, Utilisateur, empreinte_jeton, exige, hacher_mot_de_passe, ip_client,
    politique_mot_de_passe, utilisateur_courant, verifier_mot_de_passe,
)
from .solveur.diagnostic import apres_planification, avant_planification
from .solveur.moteur import Parametres, planifier
from .stockage import Stockage

log = logging.getLogger("horaires")
STATIC = Path(__file__).parent / "static"


# ---------------------------------------------------------------------- corps de requêtes
class Connexion(BaseModel):
    identifiant: str = Field(max_length=64)
    mot_de_passe: str = Field(max_length=256)


class ChangementMdp(BaseModel):
    actuel: str = Field(max_length=256)
    nouveau: str = Field(max_length=256)


class DemandePlanif(BaseModel):
    duree_max_s: int = Field(30, ge=5, le=config.DUREE_MAX_SOLVEUR_S)
    poids_compacite: int = Field(1, ge=0, le=100)


class NouvelUtilisateur(BaseModel):
    identifiant: str = Field(pattern=r"^[A-Za-z0-9_.@\-]{3,64}$")
    mot_de_passe: str = Field(max_length=256)
    role: str


def creer_app(stockage: Stockage | None = None) -> FastAPI:
    @asynccontextmanager
    async def cycle(app: FastAPI):
        app.state.stockage = stockage or Stockage(config.DB_PATH, Chiffreur(config.cle_maitre()))
        app.state.stockage.marquer_horaires_interrompus()
        app.state.executeur = ThreadPoolExecutor(max_workers=2, thread_name_prefix="solveur")
        app.state.anti_fb = AntiForceBrute()
        yield
        app.state.executeur.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="Horaires", lifespan=cycle,
        docs_url="/api/docs" if config.MODE_DEV else None, redoc_url=None,
        openapi_url="/api/openapi.json" if config.MODE_DEV else None,
    )

    # ------------------------------------------------------------------ en-têtes de sécurité
    @app.middleware("http")
    async def entetes_securite(request: Request, call_next):
        lg = request.headers.get("content-length")
        if lg and lg.isdigit() and int(lg) > config.TAILLE_MAX_IMPORT + 64 * 1024:
            return JSONResponse({"detail": "Requête trop volumineuse"}, status_code=413)
        rep: Response = await call_next(request)
        rep.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        rep.headers["X-Content-Type-Options"] = "nosniff"
        rep.headers["X-Frame-Options"] = "DENY"
        rep.headers["Referrer-Policy"] = "no-referrer"
        rep.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        rep.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        rep.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            rep.headers["Cache-Control"] = "no-store"
        if config.COOKIE_SECURE:
            rep.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return rep

    @app.exception_handler(Exception)
    async def erreur_generique(request: Request, exc: Exception):
        log.exception("Erreur non gérée sur %s", request.url.path)
        return JSONResponse({"detail": "Erreur interne"}, status_code=500)

    @app.exception_handler(RequestValidationError)
    async def erreur_validation(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "Requête invalide"}, status_code=422)

    st = lambda r: r.app.state.stockage  # noqa: E731

    # ------------------------------------------------------------------ authentification
    @app.post("/api/connexion")
    def connexion(c: Connexion, request: Request):
        store, ip, afb = st(request), ip_client(request), request.app.state.anti_fb
        if afb.bloque(c.identifiant, ip):
            store.journaliser(c.identifiant, "connexion_bloquee", ip=ip)
            raise HTTPException(429, f"Trop de tentatives. Réessayez dans {config.BLOCAGE_MIN} minutes.")
        u = store.utilisateur_par_identifiant(c.identifiant)
        ok = verifier_mot_de_passe(u["hash"] if u else None, c.mot_de_passe)
        if not ok or not u["actif"]:
            afb.echec(c.identifiant, ip)
            store.journaliser(c.identifiant, "connexion_echec", ip=ip)
            raise HTTPException(401, "Identifiant ou mot de passe incorrect")
        afb.succes(c.identifiant)
        jeton, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        t = time.time()
        store.purger_sessions(t - config.SESSION_INACTIVITE_MIN * 60, t - config.SESSION_MAX_HEURES * 3600)
        store.creer_session(empreinte_jeton(jeton), u["id"], csrf, t)
        store.journaliser(u["identifiant"], "connexion", ip=ip)
        rep = JSONResponse({"identifiant": u["identifiant"], "role": u["role"], "csrf": csrf})
        rep.set_cookie(NOM_COOKIE, jeton, httponly=True, secure=config.COOKIE_SECURE, samesite="strict",
                       path="/", max_age=config.SESSION_MAX_HEURES * 3600)
        return rep

    @app.post("/api/deconnexion")
    def deconnexion(request: Request, u: Utilisateur = Depends(utilisateur_courant)):
        st(request).supprimer_session(u.jeton_hash)
        st(request).journaliser(u.identifiant, "deconnexion", ip=ip_client(request))
        rep = JSONResponse({"ok": True})
        rep.delete_cookie(NOM_COOKIE, path="/", secure=config.COOKIE_SECURE, httponly=True, samesite="strict")
        return rep

    @app.get("/api/moi")
    def moi(u: Utilisateur = Depends(utilisateur_courant)):
        return {"identifiant": u.identifiant, "role": u.role, "csrf": u.csrf}

    @app.post("/api/moi/mot-de-passe")
    def changer_mdp(c: ChangementMdp, request: Request, u: Utilisateur = Depends(utilisateur_courant)):
        store = st(request)
        if not verifier_mot_de_passe(store.utilisateur(u.id)["hash"], c.actuel):
            raise HTTPException(400, "Mot de passe actuel incorrect")
        if err := politique_mot_de_passe(c.nouveau, u.identifiant):
            raise HTTPException(400, err)
        store.maj_mot_de_passe(u.id, hacher_mot_de_passe(c.nouveau))
        store.journaliser(u.identifiant, "mot_de_passe_modifie", ip=ip_client(request))
        return {"ok": True}

    # ------------------------------------------------------------------ jeux de données
    @app.get("/api/modele.xlsx")
    def modele(u: Utilisateur = Depends(utilisateur_courant)):
        return _fichier(donnees_vers_excel(jeu_exemple()), "modele_horaires.xlsx", XLSX)

    @app.get("/api/jeux")
    def lister_jeux(request: Request, u: Utilisateur = Depends(utilisateur_courant)):
        return st(request).jeux()

    def _enregistrer_jeu(request: Request, u: Utilisateur, ds: JeuDeDonnees, source: str) -> dict:
        diag = avant_planification(ds)
        entete = {"nom": ds.nom, "source": source, "nb_groupes": len(ds.groupes), "nb_modules": len(ds.modules),
                  "nb_enseignants": len(ds.enseignants), "nb_locaux": len(ds.locaux),
                  "nb_erreurs": sum(d["niveau"] == "erreur" for d in diag)}
        jid = st(request).creer_jeu(u.id, entete, {"donnees": ds.model_dump(mode="json"), "diagnostic": diag})
        st(request).journaliser(u.identifiant, "import_donnees", jid, ip_client(request))
        return {"id": jid, **entete, "diagnostic": diag}

    @app.post("/api/jeux")
    async def importer(request: Request, fichier: UploadFile = File(...), nom: str = Form(""),
                       u: Utilisateur = Depends(exige("planificateur"))):
        contenu = await fichier.read(config.TAILLE_MAX_IMPORT + 1)
        if len(contenu) > config.TAILLE_MAX_IMPORT:
            raise HTTPException(413, "Fichier trop volumineux")
        try:
            ds = ConnecteurFichier(contenu, fichier.filename or "").lire_donnees()
        except ValidationError as e:
            raise HTTPException(422, {"message": "Données invalides", "erreurs": _erreurs_lisibles(e)})
        except ErreurImport as e:
            raise HTTPException(422, {"message": str(e), "erreurs": []})
        if nom.strip():
            ds.nom = nom.strip()[:200]
        return _enregistrer_jeu(request, u, ds, "fichier")

    @app.post("/api/jeux/exemple")
    def importer_exemple(request: Request, u: Utilisateur = Depends(exige("planificateur"))):
        return _enregistrer_jeu(request, u, jeu_exemple(), "exemple")

    def _jeu(request: Request, jid: str) -> dict:
        j = st(request).jeu(_uuid(jid))
        if not j:
            raise HTTPException(404, "Jeu de données introuvable")
        return j

    @app.get("/api/jeux/{jid}")
    def detail_jeu(jid: str, request: Request, u: Utilisateur = Depends(utilisateur_courant)):
        j = _jeu(request, jid)
        return {k: v for k, v in j.items() if k != "contenu"} | {
            "diagnostic": j["contenu"]["diagnostic"], "donnees": j["contenu"]["donnees"]}

    @app.get("/api/jeux/{jid}/donnees.xlsx")
    def exporter_jeu(jid: str, request: Request, u: Utilisateur = Depends(utilisateur_courant)):
        ds = JeuDeDonnees.model_validate(_jeu(request, jid)["contenu"]["donnees"])
        st(request).journaliser(u.identifiant, "export_donnees", jid, ip_client(request))
        return _fichier(donnees_vers_excel(ds), "donnees.xlsx", XLSX)

    @app.get("/api/jeux/{jid}/donnees.json")
    def exporter_jeu_json(jid: str, request: Request, u: Utilisateur = Depends(utilisateur_courant)):
        ds = JeuDeDonnees.model_validate(_jeu(request, jid)["contenu"]["donnees"])
        st(request).journaliser(u.identifiant, "export_donnees", jid, ip_client(request))
        return _fichier(donnees_vers_json(ds), "donnees.json", "application/json")

    @app.delete("/api/jeux/{jid}")
    def supprimer_jeu(jid: str, request: Request, u: Utilisateur = Depends(exige("planificateur"))):
        if not st(request).supprimer_jeu(_uuid(jid)):
            raise HTTPException(404, "Jeu de données introuvable")
        st(request).journaliser(u.identifiant, "suppression_donnees", jid, ip_client(request))
        return {"ok": True}

    # ------------------------------------------------------------------ planification
    @app.post("/api/jeux/{jid}/planifier")
    def lancer(jid: str, d: DemandePlanif, request: Request, u: Utilisateur = Depends(exige("planificateur"))):
        j = _jeu(request, jid)
        ds = JeuDeDonnees.model_validate(j["contenu"]["donnees"])
        store = st(request)
        hid = store.creer_horaire(j["id"], u.id)
        store.journaliser(u.identifiant, "planification", hid, ip_client(request))

        def tache():
            try:
                r = planifier(ds, Parametres(duree_max_s=d.duree_max_s, poids_compacite=d.poids_compacite)).en_dict()
                r["diagnostic"] = apres_planification(ds, r)
                r["parametres"] = d.model_dump()
                store.terminer_horaire(hid, r["statut"], r)
            except Exception:
                log.exception("Échec de la planification %s", hid)
                store.terminer_horaire(hid, "ERREUR", {"alertes": ["Erreur interne du moteur"], "seances": [], "synthese": []})

        request.app.state.executeur.submit(tache)
        return {"id": hid, "statut": "en_cours"}

    @app.get("/api/horaires")
    def lister_horaires(request: Request, jeu: str | None = None, u: Utilisateur = Depends(utilisateur_courant)):
        return st(request).horaires(_uuid(jeu) if jeu else None)

    def _horaire(request: Request, hid: str) -> dict:
        h = st(request).horaire(_uuid(hid))
        if not h:
            raise HTTPException(404, "Horaire introuvable")
        return h

    @app.get("/api/horaires/{hid}")
    def detail_horaire(hid: str, request: Request, u: Utilisateur = Depends(utilisateur_courant)):
        return _horaire(request, hid)

    @app.get("/api/horaires/{hid}/horaire.xlsx")
    def export_horaire(hid: str, request: Request, u: Utilisateur = Depends(utilisateur_courant)):
        h = _horaire(request, hid)
        if not h["contenu"]:
            raise HTTPException(409, "Horaire non disponible")
        st(request).journaliser(u.identifiant, "export_horaire", hid, ip_client(request))
        return _fichier(horaire_vers_excel(h["contenu"], f"Horaire {hid}"), "horaire.xlsx", XLSX)

    @app.get("/api/horaires/{hid}/horaire.ics")
    def export_ics(hid: str, request: Request, type: str = "", id: str = "", u: Utilisateur = Depends(utilisateur_courant)):
        h = _horaire(request, hid)
        if not h["contenu"]:
            raise HTTPException(409, "Horaire non disponible")
        seances = h["contenu"]["seances"]
        if type in ("groupe", "enseignant", "local") and id:
            seances = [s for s in seances if s[type] == id]
        st(request).journaliser(u.identifiant, "export_horaire_ics", hid, ip_client(request))
        nom = re.sub(r"[^A-Za-z0-9_.-]", "_", f"horaire_{id or 'complet'}")[:60]
        return _fichier(horaire_vers_ics(seances, hid), f"{nom}.ics", "text/calendar")

    # ------------------------------------------------------------------ administration
    @app.get("/api/utilisateurs")
    def lister_utilisateurs(request: Request, u: Utilisateur = Depends(exige("admin"))):
        return [{k: v for k, v in x.items() if k != "hash"} for x in st(request).utilisateurs()]

    @app.post("/api/utilisateurs")
    def creer_utilisateur(n: NouvelUtilisateur, request: Request, u: Utilisateur = Depends(exige("admin"))):
        if n.role not in ROLES:
            raise HTTPException(400, "Rôle inconnu")
        if err := politique_mot_de_passe(n.mot_de_passe, n.identifiant):
            raise HTTPException(400, err)
        if st(request).utilisateur_par_identifiant(n.identifiant):
            raise HTTPException(409, "Identifiant déjà utilisé")
        uid = st(request).creer_utilisateur(n.identifiant, hacher_mot_de_passe(n.mot_de_passe), n.role)
        st(request).journaliser(u.identifiant, f"creation_utilisateur:{n.role}", n.identifiant, ip_client(request))
        return {"id": uid}

    @app.post("/api/utilisateurs/{uid}/desactiver")
    def desactiver(uid: str, request: Request, u: Utilisateur = Depends(exige("admin"))):
        cible = st(request).utilisateur(_uuid(uid))
        if not cible:
            raise HTTPException(404, "Utilisateur introuvable")
        if cible["id"] == u.id:
            raise HTTPException(400, "Impossible de désactiver son propre compte")
        st(request).desactiver_utilisateur(cible["id"])
        st(request).journaliser(u.identifiant, "desactivation_utilisateur", cible["identifiant"], ip_client(request))
        return {"ok": True}

    @app.get("/api/journal")
    def journal(request: Request, u: Utilisateur = Depends(exige("admin"))):
        return {"integre": st(request).verifier_journal(), "lignes": st(request).journal()}

    # ------------------------------------------------------------------ interface
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _fichier(contenu: bytes, nom: str, mime: str) -> Response:
    return Response(contenu, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{nom}"'})


def _uuid(v: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", v or ""):
        raise HTTPException(404, "Introuvable")
    return v


def _erreurs_lisibles(e: ValidationError) -> list[str]:
    out = []
    for err in e.errors()[:50]:
        lieu = " › ".join(str(p) for p in err["loc"])
        msg = err["msg"].removeprefix("Value error, ")
        out.extend(f"{lieu} : {m}" if lieu else m for m in msg.split("\n"))
    return out


app = creer_app()
