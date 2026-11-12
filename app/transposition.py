"""Transposer des données (cours, enseignants, groupes, locaux) dans les paramètres d'un établissement.

Sert à garder les paramètres saisis dans l'assistant (année, plages, congés, implantations…) quand on
importe des données ou qu'on charge l'exemple : l'établissement et le calendrier de `cible` remplacent
ceux des données, et les références aux plages horaires sont traduites par recouvrement horaire
(ex. « matin 08:30–12:00 » → « Matin 1 » + « Matin 2 »).
"""
from __future__ import annotations

from datetime import date, timedelta

from .modeles import JeuDeDonnees


def _minutes(h: str) -> int:
    return int(h[:2]) * 60 + int(h[3:5])


def correspondance_plages(source: list[dict], cible: JeuDeDonnees) -> dict[str, list[str]]:
    """Chaque plage source devient les plages cibles qui la recouvrent (sinon la plus proche).
    Une plage qui existe telle quelle dans la cible (même identifiant) est gardée."""
    cibles = [(c.id, _minutes(c.heure_debut), _minutes(c.heure_fin)) for c in cible.calendrier.creneaux]
    ids = {c[0] for c in cibles}
    out: dict[str, list[str]] = {}
    for p in source:
        if p["id"] in ids:
            out[p["id"]] = [p["id"]]
            continue
        d0, d1 = _minutes(str(p["heure_debut"])), _minutes(str(p["heure_fin"]))
        recouvre = [i for i, a, b in cibles if a < d1 and b > d0]
        out[p["id"]] = recouvre or [min(cibles, key=lambda c: abs(c[1] - d0))[0]]
    return out


def transposer(brut: dict, cible: JeuDeDonnees, sites: dict[str, str | None] | None = None,
               decalage: timedelta = timedelta(0)) -> JeuDeDonnees:
    """`brut` : données au format JeuDeDonnees (dict), modifiées sur place puis validées.

    sites : traduction des implantations (None = identifiants gardés tels quels).
    decalage : décalage des dates (un nombre entier de semaines, pour garder les jours de la semaine).
    """
    plages_src = brut.get("calendrier", {}).get("creneaux", [])
    corr = correspondance_plages(plages_src, cible)

    def plages(liste):
        return list(dict.fromkeys(x for p in liste for x in corr.get(p, [p])))

    def dispos(liste):
        out = []
        for motif in liste:
            jour, p = motif.split(":", 1)
            out += [motif] if p.strip() == "*" else [f"{jour}:{x}" for x in corr.get(p.strip(), [p.strip()])]
        return list(dict.fromkeys(out))

    def site(v):
        return v if sites is None or v is None else sites.get(v, v)

    def jour(v):
        if not v or not decalage:
            return v
        return ((date.fromisoformat(v) if isinstance(v, str) else v) + decalage).isoformat()

    def periodes(objs):
        for o in objs:
            o["date_debut"], o["date_fin"] = jour(o.get("date_debut")), jour(o.get("date_fin"))
            if "creneaux" in o:
                o["creneaux"] = plages(o["creneaux"])

    jours_etab = set(cible.calendrier.jours_cours)
    for l in brut.get("locaux", []):
        l["implantation"] = site(l.get("implantation"))
        l["disponibilites"] = dispos(l.get("disponibilites", []))
        periodes(l.get("indisponibilites", []))
    for e in brut.get("enseignants", []):
        e["disponibilites"] = dispos(e.get("disponibilites", []))
        e["implantations"] = list(dict.fromkeys(x for x in (site(s) for s in e.get("implantations", [])) if x))
        e["date_debut"], e["date_fin"] = jour(e.get("date_debut")), jour(e.get("date_fin"))
        periodes(e.get("indisponibilites", []))
    for m in brut.get("modules", []):
        m["implantation"] = site(m.get("implantation"))
        m["date_debut"], m["date_fin"] = jour(m.get("date_debut")), jour(m.get("date_fin"))
        for c in m.get("cours", []):
            c["implantation"] = site(c.get("implantation"))
            c["date_debut"], c["date_fin"] = jour(c.get("date_debut")), jour(c.get("date_fin"))
    for g in brut.get("groupes", []):
        g["implantation"] = site(g.get("implantation"))
        g["creneaux"] = plages(g.get("creneaux", []))
        g["jours_cours"] = [j for j in g.get("jours_cours", []) if j in jours_etab]
        g["date_debut"], g["date_fin"] = jour(g.get("date_debut")), jour(g.get("date_fin"))
        periodes(g.get("stages", []))

    etab = cible.etablissement.model_dump(mode="json")
    if sites is None:
        # sites présents dans les données mais pas dans l'établissement : ajoutés plutôt que refusés
        connus = {i["id"] for i in etab["implantations"]}
        etab["implantations"] += [i for i in brut.get("etablissement", {}).get("implantations", []) if i["id"] not in connus]
    brut["etablissement"] = etab
    brut["calendrier"] = cible.calendrier.model_dump(mode="json")
    return JeuDeDonnees.model_validate(brut)
