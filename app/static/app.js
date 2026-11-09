"use strict";
// Interface Horaires. Aucune donnée n'est insérée via innerHTML : tout passe par textContent (anti-XSS).

const etat = {
  moi: null, jeux: [], jeu: null, horaire: null, blocs: [],
  vue: { par: "groupe", entite: null, echelle: "semaine", date: null },
  etape: 1, modeParams: "parametres", entiteDonnees: "cours", sondage: null,
};
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"];
const SEMAINE = ["lun", "mar", "mer", "jeu", "ven"];
const JLONG = { lun: "Lundi", mar: "Mardi", mer: "Mercredi", jeu: "Jeudi", ven: "Vendredi", sam: "Samedi", dim: "Dimanche" };
const MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
const LIBELLES_BLOCS = { plage: "Plage entière", modulable: "Modulable (le moteur choisit)", 8: "8 périodes (journée complète)", 9: "9 périodes (journée complète)" };
const libelleBloc = (b) => LIBELLES_BLOCS[b] || `${b} périodes`;
const RANG = { lecteur: 0, planificateur: 1, admin: 2 };
const peut = (r) => etat.moi && RANG[etat.moi.role] >= RANG[r];

function h(tag, attrs = {}, ...enfants) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "classe") el.className = v;
    else if (k === "valeur") el.value = v;
    else if (k === "coche") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const e of enfants.flat()) if (e !== null && e !== undefined && e !== false) el.append(e instanceof Node ? e : String(e));
  return el;
}
const vider = (el) => { el.replaceChildren(); return el; };
const cloner = (o) => JSON.parse(JSON.stringify(o));

// ------------------------------------------------------------------ dates
const isoD = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const versD = (s) => new Date(s + "T00:00:00");
const plusJ = (s, n) => { const d = versD(s); d.setDate(d.getDate() + n); return isoD(d); };
const plusM = (s, n) => { const d = versD(s); d.setDate(1); d.setMonth(d.getMonth() + n); return isoD(d); };
const lundi = (s) => { const d = versD(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return isoD(d); };
const jourDe = (s) => JOURS[(versD(s).getDay() + 6) % 7];
const fmtDate = (s) => (s ? versD(s).toLocaleDateString("fr-BE") : "–");
const fmtCourt = (s) => versD(s).toLocaleDateString("fr-BE", { day: "numeric", month: "short" });
const fmtLong = (s) => versD(s).toLocaleDateString("fr-BE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const fmtDateHeure = (s) => new Date(s).toLocaleString("fr-BE", { dateStyle: "short", timeStyle: "short" });
const dans = (p, j) => p.date_debut <= j && j <= p.date_fin;
const teinte = (cle) => { let x = 0; for (const c of cle) x = (x * 31 + c.charCodeAt(0)) % 360; return x; };

// ------------------------------------------------------------------ API
async function api(chemin, options = {}) {
  const o = { credentials: "same-origin", headers: {}, ...options };
  if (o.method && o.method !== "GET" && etat.moi) o.headers["X-CSRF-Token"] = etat.moi.csrf;
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; delete o.json; }
  const r = await fetch(chemin, o);
  if (r.status === 401 && chemin !== "/api/connexion") { afficherConnexion(); throw new Error("Session expirée"); }
  const corps = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) {
    const d = corps?.detail;
    const err = new Error(typeof d === "string" ? d : d?.message || `Erreur ${r.status}`);
    err.details = d?.erreurs || [];
    throw err;
  }
  return corps;
}
function afficherErreur(zone, err) {
  vider(zone).append(err.message);
  if (err.details?.length) zone.append(h("ul", {}, err.details.slice(0, 12).map((d) => h("li", {}, d))));
}

// ------------------------------------------------------------------ connexion
function afficherConnexion() {
  etat.moi = null;
  $("#ecran-app").hidden = true;
  $("#ecran-connexion").hidden = false;
}

async function demarrer() {
  try { etat.moi = await api("/api/moi"); } catch { afficherConnexion(); return; }
  $("#ecran-connexion").hidden = true;
  $("#ecran-app").hidden = false;
  $("#nom-utilisateur").textContent = etat.moi.identifiant;
  $("#role-utilisateur").textContent = etat.moi.role;
  $$("[data-role]").forEach((el) => { el.hidden = !peut(el.dataset.role); });
  etat.blocs = await api("/api/blocs");
  await chargerJeux();
  if (!etat.jeux.length) {
    if (peut("planificateur")) { ouvrirAccueil(); return; }
    montrerVue("horaires");
    $("#horaire-alertes").replaceChildren(h("li", { classe: "avertissement" }, "Aucune donnée pour l'instant : un planificateur doit d'abord configurer l'établissement."));
    return;
  }
  let choix = null;
  try { choix = localStorage.getItem("jeu-courant"); } catch { /* stockage indisponible */ }
  await choisirJeu(etat.jeux.some((j) => j.id === choix) ? choix : etat.jeux[0].id);
  montrerVue("horaires");
}

$("#form-connexion").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  $("#erreur-connexion").textContent = "";
  try {
    etat.moi = await api("/api/connexion", { method: "POST", json: { identifiant: f.get("identifiant"), mot_de_passe: f.get("mot_de_passe") } });
    e.target.reset();
    demarrer();
  } catch (err) { $("#erreur-connexion").textContent = err.message; }
});
$("#btn-deconnexion").addEventListener("click", async () => {
  try { await api("/api/deconnexion", { method: "POST" }); } finally { location.reload(); }
});
$("#btn-mdp").addEventListener("click", () => { $("#retour-mdp").textContent = ""; $("#dialogue-mdp").showModal(); });
$("#mdp-annuler").addEventListener("click", () => $("#dialogue-mdp").close());
$("#form-mdp").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/moi/mot-de-passe", { method: "POST", json: { actuel: f.get("actuel"), nouveau: f.get("nouveau") } });
    e.target.reset(); $("#dialogue-mdp").close();
  } catch (err) { $("#retour-mdp").textContent = err.message; }
});

// ------------------------------------------------------------------ navigation
$$("#onglets button").forEach((b) => b.addEventListener("click", () => montrerVue(b.dataset.vue)));
function montrerVue(v) {
  $$("#onglets button").forEach((b) => b.classList.toggle("actif", b.dataset.vue === v));
  $$(".vue").forEach((s) => { s.hidden = s.id !== `vue-${v}`; });
  fermerFiche();
  if (v === "admin") chargerAdmin();
  if (v === "horaires") chargerListeHoraires();
  if (v === "parametres") ouvrirParametres();
  if (v === "donnees") afficherEntites();
}

// ------------------------------------------------------------------ jeux de données
async function chargerJeux() {
  etat.jeux = await api("/api/jeux");
  const sel = vider($("#jeu-courant"));
  for (const j of etat.jeux) sel.append(h("option", { value: j.id }, `${j.nom} (${fmtDateHeure(j.cree_le)})`));
  if (etat.jeu) sel.value = etat.jeu.id;
  const comp = $("#completer-avec");
  [...comp.options].slice(1).forEach((o) => o.remove());
  for (const j of etat.jeux) comp.append(h("option", { value: j.id }, `${j.nom} (${fmtDateHeure(j.cree_le)})`));
  if (etat.jeu) comp.value = etat.jeu.id;
  afficherTableJeux();
}
$("#jeu-courant").addEventListener("change", async (e) => { await choisirJeu(e.target.value); montrerVue("horaires"); });

async function choisirJeu(id) {
  etat.jeu = await api(`/api/jeux/${id}`);
  try { localStorage.setItem("jeu-courant", id); } catch { /* sans importance */ }
  $("#jeu-courant").value = id;
  $("#completer-avec").value = id;
  listeDiagnostic($("#diag-jeu"), etat.jeu.diagnostic, "Aucune incohérence détectée : les données sont prêtes à être planifiées.");
  afficherEntites();
}

function afficherTableJeux() {
  const tb = vider($("#table-jeux tbody"));
  for (const j of etat.jeux) {
    tb.append(h("tr", {},
      h("td", {}, j.nom), h("td", {}, fmtDateHeure(j.cree_le)),
      h("td", {}, j.nb_groupes), h("td", {}, j.nb_modules), h("td", {}, j.nb_cours ?? "–"), h("td", {}, j.nb_enseignants), h("td", {}, j.nb_locaux),
      h("td", {}, j.nb_erreurs ? h("span", { classe: "pastille rouge" }, `${j.nb_erreurs} erreur(s)`) : h("span", { classe: "pastille vert" }, "OK")),
      h("td", { classe: "actions" },
        h("button", { onclick: async () => { await choisirJeu(j.id); } }, "Utiliser"),
        h("a", { classe: "bouton", href: `/api/jeux/${j.id}/donnees.xlsx` }, "Excel"),
        h("a", { classe: "bouton", href: `/api/jeux/${j.id}/donnees-csv.zip` }, "CSV"),
        peut("planificateur") ? h("button", { classe: "danger", onclick: (e) => supprimerJeu(j, e.currentTarget) }, "Supprimer") : null)));
  }
}

function supprimerJeu(j, bouton) {
  if (bouton.dataset.confirmer !== "1") {  // confirmation en deux clics (pas de boîte de dialogue)
    bouton.dataset.confirmer = "1"; bouton.textContent = "Confirmer ?";
    setTimeout(() => { bouton.dataset.confirmer = ""; bouton.textContent = "Supprimer"; }, 4000);
    return;
  }
  api(`/api/jeux/${j.id}`, { method: "DELETE" }).then(async () => {
    if (etat.jeu?.id === j.id) etat.jeu = null;
    await chargerJeux();
    if (!etat.jeu && etat.jeux.length) await choisirJeu(etat.jeux[0].id);
  });
}

function listeDiagnostic(ul, items, vide) {
  vider(ul);
  if (!items?.length) { ul.append(h("li", { classe: "ok" }, vide)); return; }
  for (const d of items) {
    ul.append(h("li", { classe: d.niveau },
      h("strong", {}, d.sujet ? `${d.sujet} — ` : ""), d.message,
      d.suggestion ? h("div", { classe: "suggestion" }, "→ " + d.suggestion) : null));
  }
}

$("#form-import").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const bouton = e.target.querySelector("button[type=submit]");
  bouton.disabled = true;
  const zone = vider($("#retour-import"));
  try {
    const res = await api("/api/jeux", { method: "POST", body: fd });
    zone.append(h("p", { classe: "succes" }, `« ${res.nom} » importé : ${res.nb_groupes} groupes, ${res.nb_modules} modules, ${res.nb_cours} cours.`));
    e.target.reset();
    await chargerJeux(); await choisirJeu(res.id);
  } catch (err) { afficherErreur(zone.appendChild(h("div", { classe: "erreur" })), err); } finally { bouton.disabled = false; }
});
$("#btn-exemple").addEventListener("click", async () => {
  const zone = vider($("#retour-import"));
  try {
    const res = await api("/api/jeux/exemple", { method: "POST" });
    zone.append(h("p", { classe: "succes" }, `Exemple chargé : ${res.nb_groupes} groupes, ${res.nb_cours} cours.`));
    await chargerJeux(); await choisirJeu(res.id);
  } catch (err) { afficherErreur(zone.appendChild(h("div", { classe: "erreur" })), err); }
});
$("#form-planif").addEventListener("submit", async (e) => {
  e.preventDefault();
  const res = await api(`/api/jeux/${etat.jeu.id}/planifier`, {
    method: "POST", json: { duree_max_s: +$("#pl-duree").value, poids_compacite: +$("#pl-compacite").value },
  });
  montrerVue("horaires");
  await chargerListeHoraires(res.id);
});

// enregistre une version modifiée des données du jeu courant
async function enregistrerDonnees(donnees) {
  const res = await api(`/api/jeux/${etat.jeu.id}/donnees`, { method: "PUT", json: { version: etat.jeu.version, donnees } });
  etat.jeu = { ...etat.jeu, ...res };
  listeDiagnostic($("#diag-jeu"), etat.jeu.diagnostic, "Aucune incohérence détectée : les données sont prêtes à être planifiées.");
  afficherEntites();
  return res;
}

// ------------------------------------------------------------------ assistant d'accueil / paramètres
function anneeScolaireParDefaut() {
  const d = new Date();
  const a = d.getMonth() >= 6 ? d.getFullYear() : d.getFullYear() - 1;
  return { debut: `${a}-09-01`, fin: `${a + 1}-06-30` };
}
function parametresParDefaut() {
  const an = anneeScolaireParDefaut();
  return {
    nom: "Mon établissement",
    etablissement: { implantations: [], multi_sites_meme_jour: true, delai_deplacement_min: 30, enseignants_toutes_implantations: true,
      max_periodes_semaine_defaut: null, bloc_defaut: "plage" },
    calendrier: { date_debut: an.debut, date_fin: an.fin, jours_cours: ["lun", "mar", "mer", "jeu", "ven"], cours_pendant_conges: false,
      rythme_defaut: "souple", fermetures: [],
      creneaux: [
        { id: "matin", libelle: "Matin", heure_debut: "08:30", heure_fin: "12:00", periodes: 4, jours: [] },
        { id: "aprem", libelle: "Après-midi", heure_debut: "13:00", heure_fin: "16:20", periodes: 4, jours: [] },
        { id: "soir", libelle: "Soir", heure_debut: "18:00", heure_fin: "21:20", periodes: 4, jours: [] },
      ] },
  };
}

function ouvrirAccueil() {
  etat.modeParams = "accueil";
  $$("#onglets button").forEach((b) => b.classList.remove("actif"));
  $$(".vue").forEach((s) => { s.hidden = !["vue-accueil", "vue-parametres"].includes(s.id); });
  remplirParams(parametresParDefaut());
  allerEtape(1);
}
function ouvrirParametres() {
  if (!etat.jeu) { ouvrirAccueil(); return; }
  etat.modeParams = "parametres";
  $("#vue-accueil").hidden = true;
  const d = etat.jeu.donnees;
  remplirParams({ nom: d.nom, etablissement: d.etablissement, calendrier: d.calendrier });
  allerEtape(0);
}

const ETAPES = ["Établissement", "Horaires", "Blocs", "Implantations", "Enseignants"];
function allerEtape(n) {
  etat.etape = n;
  const tout = n === 0;  // mode Paramètres : tout le formulaire d'un coup
  $$("#form-parametres .etape").forEach((f) => { f.hidden = !tout && +f.dataset.etape !== n; });
  const ol = vider($("#etapes"));
  ol.hidden = tout;
  ETAPES.forEach((e, i) => ol.append(h("li", { classe: i + 1 === n ? "actif" : i + 1 < n ? "fait" : "" }, e)));
  $("#p-prec").hidden = tout || n === 1;
  $("#p-suiv").hidden = tout || n === ETAPES.length;
  $("#p-valider").hidden = !tout && n !== ETAPES.length;
  $("#p-valider").textContent = etat.modeParams === "accueil" ? "Créer l'établissement" : "Enregistrer les paramètres";
  $("#p-erreur").textContent = "";
}
$("#p-prec").addEventListener("click", () => allerEtape(etat.etape - 1));
$("#p-suiv").addEventListener("click", () => allerEtape(etat.etape + 1));

const TYPES_FERMETURE = { conges: "Congé scolaire", ferie: "Jour férié", special: "Jour spécial" };
// options « toute la journée / plage » et « tous les sites / site », d'après le formulaire en cours d'édition
function optionsPortee(sel, options, valeur) {
  const v = valeur ?? sel.value;
  vider(sel).append(...options.map(([val, lib]) => h("option", { value: val }, lib)));
  const nom = (id) => options.find(([val]) => val === id)?.[1] || id;
  if (v && !options.some(([val]) => val === v)) sel.append(h("option", { value: v }, v.split(",").map(nom).join(" + ")));
  sel.value = v || "";
}
// une plage seule, ou une plage et toutes les suivantes (ex. « Après-midi + Soir »)
function optionsPlages() {
  const pl = lirePlages().filter((p) => p.heure_debut).sort((a, b) => a.heure_debut.localeCompare(b.heure_debut));
  const nom = (p) => p.libelle || p.id;
  const suites = pl.slice(1, -1).map((p, i) => pl.slice(i + 1)).map((x) => [x.map((p) => p.id).join(","), x.map(nom).join(" + ")]);
  return [["", "toute la journée"], ...pl.map((p) => [p.id, nom(p)]), ...suites];
}
const optionsSites = () => [["", "tous les sites"], ...($("#p-multi-impl").checked ? lireImplantations().map((i) => [i.id, i.nom || i.id]) : [])];
const nouvelleFermeture = { date_debut: "", date_fin: "", motif: "", type: "conges", creneaux: [], implantations: [] };
function ligneFermeture(f = nouvelleFermeture) {
  const type = h("select", { "aria-label": "Type" }, Object.entries(TYPES_FERMETURE).map(([v, l]) => h("option", { value: v }, l)));
  type.value = f.type || "ferie";
  const plages = h("select", { "aria-label": "Moment", onfocus: () => optionsPortee(plages, optionsPlages()) });
  const sites = h("select", { "aria-label": "Implantation", onfocus: () => optionsPortee(sites, optionsSites()) });
  optionsPortee(plages, optionsPlages(), (f.creneaux || []).join(","));
  optionsPortee(sites, optionsSites(), (f.implantations || []).join(","));
  return h("tr", {},
    h("td", {}, h("input", { type: "date", valeur: f.date_debut, "aria-label": "Du" })),
    h("td", {}, h("input", { type: "date", valeur: f.date_fin, "aria-label": "Au" })),
    h("td", {}, h("input", { valeur: f.motif, placeholder: "Formation continue", "aria-label": "Motif" })),
    h("td", {}, type), h("td", {}, plages), h("td", {}, sites),
    h("td", {}, h("button", { type: "button", classe: "petit danger", onclick: (e) => e.currentTarget.closest("tr").remove() }, "Retirer")));
}
function lignePlage(p = { id: "", libelle: "", heure_debut: "", heure_fin: "", periodes: 4, jours: [] }) {
  const sel = h("select", { "aria-label": "Jours" }, h("option", { value: "" }, "tous les jours"),
    h("option", { value: SEMAINE.join(",") }, "du lundi au vendredi"),
    JOURS.map((j) => h("option", { value: j }, JLONG[j].toLowerCase())));
  sel.value = p.jours.join(",");
  if (sel.value !== p.jours.join(",")) {  // combinaison particulière venant d'un import
    sel.append(h("option", { value: p.jours.join(",") }, p.jours.join(", ")));
    sel.value = p.jours.join(",");
  }
  return h("tr", { "data-id": p.id },
    h("td", {}, h("input", { valeur: p.libelle || p.id, placeholder: "Matin", "aria-label": "Nom", required: true })),
    h("td", {}, h("input", { type: "time", valeur: p.heure_debut, "aria-label": "Début", required: true })),
    h("td", {}, h("input", { type: "time", valeur: p.heure_fin, "aria-label": "Fin", required: true })),
    h("td", {}, h("input", { type: "number", min: 1, max: 20, valeur: p.periodes, "aria-label": "Périodes", classe: "court" })),
    h("td", {}, sel),
    h("td", {}, h("button", { type: "button", classe: "petit danger", onclick: (e) => e.currentTarget.closest("tr").remove() }, "Retirer")));
}
function ligneImpl(i = { id: "", nom: "", adresse: "" }) {
  return h("tr", {},
    h("td", {}, h("input", { valeur: i.id, placeholder: "NORD", "aria-label": "Code", classe: "court" })),
    h("td", {}, h("input", { valeur: i.nom, placeholder: "Site Nord", "aria-label": "Nom" })),
    h("td", {}, h("input", { valeur: i.adresse, placeholder: "Adresse", "aria-label": "Adresse" })),
    h("td", {}, h("button", { type: "button", classe: "petit danger", onclick: (e) => e.currentTarget.closest("tr").remove() }, "Retirer")));
}
$("#p-ajout-fermeture").addEventListener("click", () => $("#p-fermetures tbody").append(ligneFermeture({ ...nouvelleFermeture, type: "conges" })));
$("#p-ajout-special").addEventListener("click", () => {
  const l = ligneFermeture({ ...nouvelleFermeture, type: "special" });
  $("#p-fermetures tbody").append(l);
  $("input", l).focus();
});
$("#p-ajout-plage").addEventListener("click", () => $("#p-plages tbody").append(lignePlage()));
$("#p-ajout-impl").addEventListener("click", () => $("#p-implantations tbody").append(ligneImpl()));
$("#p-multi-impl").addEventListener("change", (e) => { $("#p-impl-details").hidden = !e.target.checked; });
$("#p-multi-sites").addEventListener("change", (e) => { $("#p-delai-l").hidden = !e.target.checked; });

function remplirParams(p) {
  const { etablissement: et, calendrier: cal } = p;
  $("#p-nom").value = p.nom;
  $("#p-debut").value = cal.date_debut; $("#p-fin").value = cal.date_fin;
  const jours = vider($("#p-jours"));
  for (const j of JOURS) jours.append(h("label", { classe: "case-a-cocher" }, h("input", { type: "checkbox", value: j, coche: cal.jours_cours.includes(j) }), JLONG[j]));
  $("#p-conges").checked = !!cal.cours_pendant_conges;
  const tf = vider($("#p-fermetures tbody"));
  const tp = vider($("#p-plages tbody"));
  for (const c of cal.creneaux) tp.append(lignePlage(c));
  const blocs = vider($("#p-blocs"));
  for (const b of etat.blocs) blocs.append(h("label", { classe: "choix" }, h("input", { type: "radio", name: "bloc", value: b, coche: et.bloc_defaut === b }), libelleBloc(b)));
  $("#p-multi-impl").checked = et.implantations.length > 0;
  $("#p-impl-details").hidden = !et.implantations.length;
  const ti = vider($("#p-implantations tbody"));
  for (const i of et.implantations) ti.append(ligneImpl(i));
  if (!et.implantations.length) { ti.append(ligneImpl()); ti.append(ligneImpl()); }
  for (const f of cal.fermetures) tf.append(ligneFermeture(f));
  $("#p-multi-sites").checked = et.multi_sites_meme_jour;
  $("#p-delai").value = et.delai_deplacement_min;
  $("#p-delai-l").hidden = !et.multi_sites_meme_jour;
  $("#p-max-sem").value = et.max_periodes_semaine_defaut ?? "";
  $("#p-toutes-impl").checked = et.enseignants_toutes_implantations;
}

function codeDepuis(nom, pris) {
  let base = nom.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "plage";
  let c = base, i = 2;
  while (pris.has(c)) c = `${base}_${i++}`;
  pris.add(c);
  return c;
}
function lirePlages() {
  const pris = new Set();
  return $$("#p-plages tbody tr").map((tr) => {
    const [nom, deb, fin, per] = $$("input", tr).map((i) => i.value.trim());
    const jour = $("select", tr).value;
    const id = tr.dataset.id || codeDepuis(nom, pris);
    pris.add(id);
    return { id, libelle: nom, heure_debut: deb, heure_fin: fin, periodes: +per || 1, jours: jour ? jour.split(",") : [] };
  });
}
function lireImplantations() {
  return $$("#p-implantations tbody tr").map((tr) => {
    const [id, nom, adresse] = $$("input", tr).map((i) => i.value.trim());
    return { id, nom, adresse };
  }).filter((i) => i.id);
}
function lireParams() {
  const plages = lirePlages();
  const fermetures = $$("#p-fermetures tbody tr").map((tr) => {
    const [d, f, motif] = $$("input", tr).map((i) => i.value.trim());
    const [type, moment, site] = $$("select", tr).map((x) => x.value);
    return { date_debut: d, date_fin: f || d, motif, type,
      creneaux: moment ? moment.split(",") : [], implantations: site ? site.split(",") : [] };
  }).filter((f) => f.date_debut);
  const implantations = $("#p-multi-impl").checked ? lireImplantations() : [];
  const anc = etat.modeParams === "parametres" ? etat.jeu.donnees : parametresParDefaut();
  return {
    nom: $("#p-nom").value.trim() || "Mon établissement",
    etablissement: {
      ...anc.etablissement, implantations,
      multi_sites_meme_jour: $("#p-multi-sites").checked,
      delai_deplacement_min: +$("#p-delai").value || 0,
      enseignants_toutes_implantations: $("#p-toutes-impl").checked,
      max_periodes_semaine_defaut: $("#p-max-sem").value ? +$("#p-max-sem").value : null,
      bloc_defaut: $("#p-blocs input:checked")?.value || "plage",
    },
    calendrier: {
      ...anc.calendrier, date_debut: $("#p-debut").value, date_fin: $("#p-fin").value,
      jours_cours: $$("#p-jours input:checked").map((i) => i.value),
      cours_pendant_conges: $("#p-conges").checked, creneaux: plages, fermetures,
    },
  };
}
$("#form-parametres").addEventListener("submit", async (e) => {
  e.preventDefault();
  const p = lireParams();
  const zone = $("#p-erreur");
  zone.textContent = "";
  try {
    if (etat.modeParams === "accueil") {
      const res = await api("/api/jeux/vierge", { method: "POST", json: p });
      await chargerJeux(); await choisirJeu(res.id);
      montrerVue("donnees");
      $("#retour-import").replaceChildren(h("p", { classe: "succes" },
        "Établissement créé. Importez maintenant vos données (export CSV de votre logiciel ou modèle Excel) : " +
        "les paramètres que vous venez de saisir seront repris automatiquement."));
    } else {
      const d = cloner(etat.jeu.donnees);
      Object.assign(d, { nom: p.nom, etablissement: p.etablissement, calendrier: p.calendrier });
      await enregistrerDonnees(d);
      zone.replaceChildren(h("span", { classe: "succes" }, "Paramètres enregistrés. Relancez une planification pour les appliquer à l'horaire."));
    }
  } catch (err) { afficherErreur(zone, err); }
});

// ------------------------------------------------------------------ données : listes d'entités
$$("#sous-onglets button").forEach((b) => b.addEventListener("click", () => {
  etat.entiteDonnees = b.dataset.entite;
  $$("#sous-onglets button").forEach((x) => x.classList.toggle("actif", x === b));
  afficherEntites();
}));
$("#filtre-donnees").addEventListener("input", () => afficherEntites());

function nomEns(d, id) { return d.enseignants.find((e) => e.id === id)?.nom || id; }
function implNom(d, id) { return id ? d.etablissement.implantations.find((i) => i.id === id)?.nom || id : "–"; }
function attributionsEnseignant(d, eid) {
  const out = [];
  for (const m of d.modules) for (const c of m.cours) for (const a of c.attributions) {
    if (!a.enseignants.includes(eid)) continue;
    const groupes = a.groupes.length ? a.groupes : d.groupes.filter((g) => g.modules.includes(m.id)).map((g) => g.id);
    out.push({ module: m, cours: c, attribution: a, groupes });
  }
  return out;
}

function afficherEntites() {
  const tete = vider($("#table-entites thead")), corps = vider($("#table-entites tbody"));
  if (!etat.jeu) return;
  const d = etat.jeu.donnees, q = $("#filtre-donnees").value.trim().toLowerCase();
  const garde = (...txt) => !q || txt.join(" ").toLowerCase().includes(q);
  const ligne = (type, id, cellules) => corps.append(h("tr", { tabindex: 0, onclick: () => ouvrirFiche(type, id),
    onkeydown: (e) => { if (e.key === "Enter") ouvrirFiche(type, id); } }, cellules.map((c) => h("td", {}, c))));
  const entetes = (cols) => tete.append(h("tr", {}, cols.map((c) => h("th", {}, c))));
  if (etat.entiteDonnees === "cours") {
    entetes(["Cours", "Module", "Périodes", "Enseignant(s)", "Bloc", "Mode", "Implantation"]);
    for (const m of d.modules) for (const c of m.cours) {
      const ens = c.attributions.map((a) => a.enseignants.map((e) => nomEns(d, e)).join(" + ")).join(" / ");
      if (!garde(c.id, c.nom, m.nom, ens)) continue;
      ligne("cours", c.id, [h("span", {}, h("b", {}, c.nom || c.id), h("div", { classe: "discret" }, c.id)), m.nom || m.id, c.periodes, ens,
        c.bloc ? libelleBloc(c.bloc) : "défaut", c.periodes_distanciel ? h("span", { classe: "pastille bleu" }, `hybride · ${c.periodes_distanciel} p. à distance`) : "présentiel",
        c.implantation ? implNom(d, c.implantation) : "celle du groupe"]);
    }
  } else if (etat.entiteDonnees === "enseignants") {
    entetes(["Enseignant", "Code", "Périodes attribuées", "Max / semaine", "Implantations", "Contrat"]);
    for (const e of d.enseignants) {
      if (!garde(e.id, e.nom)) continue;
      const total = attributionsEnseignant(d, e.id).reduce((s, x) => s + x.attribution.periodes * Math.max(1, x.groupes.length), 0);
      ligne("enseignant", e.id, [h("b", {}, e.nom || e.id), e.id, total, e.max_periodes_semaine ?? (d.etablissement.max_periodes_semaine_defaut ? `${d.etablissement.max_periodes_semaine_defaut} (défaut)` : "–"),
        e.implantations.length ? e.implantations.map((i) => implNom(d, i)).join(", ") : (d.etablissement.enseignants_toutes_implantations ? "toutes" : "à préciser"),
        e.date_debut || e.date_fin ? `${e.date_debut ? fmtDate(e.date_debut) : "…"} → ${e.date_fin ? fmtDate(e.date_fin) : "…"}` : "toute l'année"]);
    }
  } else if (etat.entiteDonnees === "groupes") {
    entetes(["Groupe", "Code", "Effectif", "Implantation", "Jours", "Plages", "Modules"]);
    for (const g of d.groupes) {
      if (!garde(g.id, g.nom)) continue;
      ligne("groupe", g.id, [h("b", {}, g.nom || g.id), g.id, g.effectif, implNom(d, g.implantation),
        (g.jours_cours.length ? g.jours_cours : d.calendrier.jours_cours).join(", "), g.creneaux.length ? g.creneaux.join(", ") : "toutes", g.modules.length]);
    }
  } else {
    entetes(["Local", "Code", "Implantation", "Capacité", "Équipements"]);
    for (const l of d.locaux) {
      if (!garde(l.id, l.nom, l.equipements.join(" "))) continue;
      ligne("local", l.id, [h("b", {}, l.nom || l.id), l.id, implNom(d, l.implantation), l.capacite, l.equipements.join(", ") || "–"]);
    }
  }
  if (!corps.children.length) corps.append(h("tr", {}, h("td", { colspan: 7, classe: "discret" }, q ? "Aucun résultat." : "Rien pour l'instant : importez vos données.")));
}

// ------------------------------------------------------------------ fiches (cours, enseignant, groupe, local)
const champ = (libelle, ctrl, aide) => h("label", { classe: "champ" }, h("span", {}, libelle), ctrl, aide ? h("small", { classe: "discret" }, aide) : null);
const entree = (id, valeur, attrs = {}) => h("input", { id, valeur: valeur ?? "", ...attrs });
function choix(id, options, valeur) {
  const s = h("select", { id }, options.map(([v, l]) => h("option", { value: v }, l)));
  s.value = valeur ?? "";
  return s;
}
function cases(nom, options, coches) {
  return h("div", { classe: "cases" }, options.map(([v, l]) => h("label", { classe: "case-a-cocher" },
    h("input", { type: "checkbox", name: nom, value: v, coche: coches.includes(v) }), l)));
}
const coches = (form, nom) => $$(`input[name="${nom}"]:checked`, form).map((i) => i.value);

function fermerFiche() { $("#fiche").hidden = true; }
$("#fiche-fermer").addEventListener("click", fermerFiche);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#fiche").hidden) fermerFiche(); });

function ouvrirFiche(type, id) {
  if (!etat.jeu) return;
  const d = etat.jeu.donnees;
  const form = vider($("#fiche-form"));
  const lecture = !peut("planificateur");
  let appliquer = null;
  $("#fiche-type").textContent = { cours: "Cours", enseignant: "Enseignant", groupe: "Groupe", local: "Local" }[type];

  if (type === "cours") {
    const m = d.modules.find((x) => x.cours.some((c) => c.id === id));
    if (!m) return;
    const c = m.cours.find((x) => x.id === id);
    $("#fiche-titre").textContent = c.nom || c.id;
    const impl = d.etablissement.implantations;
    form.append(
      h("p", { classe: "discret" }, `Module : ${m.nom || m.id}${m.prerequis.length ? ` · prérequis : ${m.prerequis.join(", ")}` : ""}`),
      h("div", { classe: "grille-champs" },
        champ("Nom", entree("f-nom", c.nom)),
        champ("Périodes", entree("f-periodes", c.periodes, { type: "number", min: 1 })),
        champ("Taille des séances", choix("f-bloc", [["", `Par défaut (${libelleBloc(d.etablissement.bloc_defaut)})`], ...etat.blocs.map((b) => [b, libelleBloc(b)])], c.bloc)),
        champ("Rythme", choix("f-rythme", [["", `Par défaut (${d.calendrier.rythme_defaut})`], ["fixe", "Fixe (mêmes jours et heures)"], ["souple", "Souple"]], c.rythme)),
        impl.length ? champ("Implantation", choix("f-impl", [["", "Celle du groupe"], ...impl.map((i) => [i.id, i.nom || i.id])], c.implantation)) : null,
        champ("Séances max. par semaine", entree("f-max", c.max_seances_semaine, { type: "number", min: 1 })),
        champ("Équipements requis", entree("f-equip", c.equipements.join("; "), { placeholder: "pc; projecteur" }))),
      h("fieldset", { classe: "sous-bloc" }, h("legend", {}, "Cours hybride"),
        h("label", { classe: "case-a-cocher" }, h("input", { type: "checkbox", id: "f-hybride", coche: c.periodes_distanciel > 0 }), "Organisé en partie à distance"),
        champ("Périodes à distance (sans local)", entree("f-dist", c.periodes_distanciel, { type: "number", min: 0 }),
          "Vous pouvez aussi basculer une séance précise en distanciel dans la liste ci-dessous.")),
      h("fieldset", { classe: "sous-bloc" }, h("legend", {}, "Locaux autorisés"),
        h("p", { classe: "discret" }, "Aucun coché : tout local compatible (capacité, équipements, implantation)."),
        cases("f-locaux", d.locaux.map((l) => [l.id, `${l.nom || l.id} (${l.capacite} pl.)`]), c.locaux)),
      h("fieldset", { classe: "sous-bloc" }, h("legend", {}, "Enseignants"),
        h("p", { classe: "discret" }, "Une ligne par enseignant qui donne ses propres séances ; un second enseignant sur la même ligne = co-enseignement."),
        h("table", { classe: "tableau compact", id: "f-attr" }, h("thead", {}, h("tr", {}, h("th", {}, "Enseignant"), h("th", {}, "Co-enseignant"), h("th", {}, "Périodes"), h("th", {}, "Groupes (vide = tous)"), h("th", {}))),
          h("tbody", {}, c.attributions.map((a) => ligneAttribution(d, a)))),
        lecture ? null : h("button", { type: "button", classe: "petit", onclick: () => $("#f-attr tbody").append(ligneAttribution(d)) }, "+ Ajouter un enseignant")),
    );
    const hyb = $("#f-hybride", form), dist = $("#f-dist", form);
    dist.closest("label").hidden = !hyb.checked;
    hyb.addEventListener("change", () => { dist.closest("label").hidden = !hyb.checked; if (!hyb.checked) dist.value = 0; });
    form.append(sectionSeancesCours(c.id));
    appliquer = (nd) => {
      const cc = nd.modules.flatMap((x) => x.cours).find((x) => x.id === id);
      cc.nom = $("#f-nom", form).value.trim();
      cc.periodes = +$("#f-periodes", form).value;
      cc.bloc = $("#f-bloc", form).value || null;
      cc.rythme = $("#f-rythme", form).value || null;
      if ($("#f-impl", form)) cc.implantation = $("#f-impl", form).value || null;
      cc.max_seances_semaine = $("#f-max", form).value ? +$("#f-max", form).value : null;
      cc.equipements = $("#f-equip", form).value.split(/[;,|]/).map((x) => x.trim()).filter(Boolean);
      cc.periodes_distanciel = $("#f-hybride", form).checked ? +$("#f-dist", form).value || 0 : 0;
      cc.locaux = coches(form, "f-locaux");
      cc.attributions = $$("#f-attr tbody tr", form).map((tr) => {
        const [e1, e2] = $$("select", tr).map((s) => s.value);
        const [per, grp] = $$("input", tr).map((i) => i.value.trim());
        return { enseignants: [e1, e2].filter(Boolean), periodes: +per, groupes: grp.split(/[;,|]/).map((x) => x.trim()).filter(Boolean) };
      }).filter((a) => a.enseignants.length);
    };
  } else if (type === "enseignant") {
    const e = d.enseignants.find((x) => x.id === id);
    if (!e) return;
    $("#fiche-titre").textContent = e.nom || e.id;
    const attrs = attributionsEnseignant(d, id);
    const total = attrs.reduce((s, x) => s + x.attribution.periodes * Math.max(1, x.groupes.length), 0);
    const plages = d.calendrier.creneaux;
    const toujours = !e.disponibilites.length;
    const grille = h("table", { classe: "tableau compact dispo" }, h("thead", {}, h("tr", {}, h("th", {}), plages.map((p) => h("th", {}, p.libelle || p.id)))),
      h("tbody", {}, d.calendrier.jours_cours.map((j) => h("tr", {}, h("th", {}, JLONG[j]), plages.map((p) => h("td", {},
        p.jours.length && !p.jours.includes(j) ? "–" : h("input", { type: "checkbox", name: "f-dispo", value: `${j}:${p.id}`, "aria-label": `${JLONG[j]} ${p.libelle || p.id}`,
          coche: toujours || e.disponibilites.includes(`${j}:${p.id}`) || e.disponibilites.includes(`${j}:*`) })))))));
    form.append(
      h("p", { classe: "discret" }, `Code ${e.id} · ${total} périodes attribuées sur l'année`),
      h("div", { classe: "grille-champs" },
        champ("Nom", entree("f-nom", e.nom)),
        champ("Max. de périodes par semaine", entree("f-max", e.max_periodes_semaine, { type: "number", min: 0, placeholder: d.etablissement.max_periodes_semaine_defaut ?? "pas de limite" })),
        champ("Date d'engagement", entree("f-debut", e.date_debut, { type: "date" })),
        champ("Fin de contrat / pension", entree("f-fin", e.date_fin, { type: "date" }))),
      d.etablissement.implantations.length ? h("fieldset", { classe: "sous-bloc" }, h("legend", {}, "Implantations où il ou elle enseigne"),
        h("p", { classe: "discret" }, d.etablissement.enseignants_toutes_implantations ? "Aucune cochée : toutes." : "Aucune cochée : aucune (à préciser)."),
        cases("f-impl", d.etablissement.implantations.map((i) => [i.id, i.nom || i.id]), e.implantations)) : null,
      h("fieldset", { classe: "sous-bloc" }, h("legend", {}, "Disponibilités habituelles"), h("div", { classe: "defile" }, grille)),
      e.indisponibilites.length ? h("p", { classe: "discret" }, "Absences : " + e.indisponibilites.map((i) => `${fmtDate(i.date_debut)} → ${fmtDate(i.date_fin)}${i.motif ? ` (${i.motif})` : ""}`).join(", ")) : null,
      h("h3", {}, "Attributions"),
      attrs.length ? h("table", { classe: "tableau compact cliquable" }, h("thead", {}, h("tr", {}, h("th", {}, "Cours"), h("th", {}, "Groupe(s)"), h("th", {}, "Périodes"))),
        h("tbody", {}, attrs.map((x) => h("tr", { onclick: () => ouvrirFiche("cours", x.cours.id) },
          h("td", {}, h("b", {}, x.cours.nom || x.cours.id), x.attribution.enseignants.length > 1 ? h("div", { classe: "discret" }, "co-enseignement avec " + x.attribution.enseignants.filter((y) => y !== id).map((y) => nomEns(d, y)).join(", ")) : null),
          h("td", {}, x.groupes.join(", ")), h("td", {}, x.attribution.periodes)))))
        : h("p", { classe: "discret" }, "Aucune attribution."),
      boutonVoirHoraire("enseignant", id),
    );
    appliquer = (nd) => {
      const ee = nd.enseignants.find((x) => x.id === id);
      ee.nom = $("#f-nom", form).value.trim();
      ee.max_periodes_semaine = $("#f-max", form).value === "" ? null : +$("#f-max", form).value;
      ee.date_debut = $("#f-debut", form).value || null;
      ee.date_fin = $("#f-fin", form).value || null;
      if (d.etablissement.implantations.length) ee.implantations = coches(form, "f-impl");
      const tout = $$('input[name="f-dispo"]', form);
      ee.disponibilites = tout.every((i) => i.checked) ? [] : tout.filter((i) => i.checked).map((i) => i.value);
    };
  } else if (type === "groupe") {
    const g = d.groupes.find((x) => x.id === id);
    if (!g) return;
    $("#fiche-titre").textContent = g.nom || g.id;
    form.append(
      h("p", { classe: "discret" }, `Code ${g.id}`),
      h("div", { classe: "grille-champs" },
        champ("Nom", entree("f-nom", g.nom)),
        champ("Effectif", entree("f-eff", g.effectif, { type: "number", min: 1 })),
        d.etablissement.implantations.length ? champ("Implantation", choix("f-impl", [["", "—"], ...d.etablissement.implantations.map((i) => [i.id, i.nom || i.id])], g.implantation)) : null,
        champ("Local attitré", choix("f-local", [["", "—"], ...d.locaux.map((l) => [l.id, l.nom || l.id])], g.local_prefere)),
        champ("Début", entree("f-debut", g.date_debut, { type: "date" })),
        champ("Fin", entree("f-fin", g.date_fin, { type: "date" }))),
      h("fieldset", { classe: "sous-bloc" }, h("legend", {}, "Jours de cours (aucun coché : ceux de l'établissement)"),
        cases("f-jours", d.calendrier.jours_cours.map((j) => [j, JLONG[j]]), g.jours_cours)),
      h("fieldset", { classe: "sous-bloc" }, h("legend", {}, "Plages horaires (aucune cochée : toutes)"),
        cases("f-plages", d.calendrier.creneaux.map((p) => [p.id, p.libelle || p.id]), g.creneaux)),
      h("h3", {}, "Parcours"),
      h("ul", { classe: "liste" }, g.modules.map((mid) => { const m = d.modules.find((x) => x.id === mid); return h("li", {}, m ? `${m.nom || m.id} — ${m.cours.length} cours, ${m.cours.reduce((s, c) => s + c.periodes, 0)} p.` : mid); })),
      g.stages.length ? h("p", { classe: "discret" }, "Stages : " + g.stages.map((s) => `${fmtDate(s.date_debut)} → ${fmtDate(s.date_fin)}`).join(", ")) : null,
      boutonVoirHoraire("groupe", id),
    );
    appliquer = (nd) => {
      const gg = nd.groupes.find((x) => x.id === id);
      gg.nom = $("#f-nom", form).value.trim();
      gg.effectif = +$("#f-eff", form).value;
      if ($("#f-impl", form)) gg.implantation = $("#f-impl", form).value || null;
      gg.local_prefere = $("#f-local", form).value || null;
      gg.date_debut = $("#f-debut", form).value || null;
      gg.date_fin = $("#f-fin", form).value || null;
      gg.jours_cours = coches(form, "f-jours");
      gg.creneaux = coches(form, "f-plages");
    };
  } else {
    const l = d.locaux.find((x) => x.id === id);
    if (!l) return;
    $("#fiche-titre").textContent = l.nom || l.id;
    form.append(
      h("p", { classe: "discret" }, `Code ${l.id}`),
      h("div", { classe: "grille-champs" },
        champ("Nom", entree("f-nom", l.nom)),
        champ("Capacité", entree("f-cap", l.capacite, { type: "number", min: 1 })),
        d.etablissement.implantations.length ? champ("Implantation", choix("f-impl", [["", "—"], ...d.etablissement.implantations.map((i) => [i.id, i.nom || i.id])], l.implantation)) : null,
        champ("Équipements", entree("f-equip", l.equipements.join("; "), { placeholder: "pc; projecteur" }))),
      boutonVoirHoraire("local", id),
    );
    appliquer = (nd) => {
      const ll = nd.locaux.find((x) => x.id === id);
      ll.nom = $("#f-nom", form).value.trim();
      ll.capacite = +$("#f-cap", form).value;
      if ($("#f-impl", form)) ll.implantation = $("#f-impl", form).value || null;
      ll.equipements = $("#f-equip", form).value.split(/[;,|]/).map((x) => x.trim()).filter(Boolean);
    };
  }

  const retour = h("div", { classe: "erreur", role: "alert" });
  if (lecture) {
    $$("input, select", form).forEach((el) => { el.disabled = true; });
  } else {
    form.append(retour, h("div", { classe: "ligne pied-fiche" },
      h("button", { type: "submit", classe: "primaire" }, "Enregistrer"),
      h("button", { type: "button", onclick: fermerFiche }, "Fermer")));
  }
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    vider(retour);
    try {
      const nd = cloner(etat.jeu.donnees);
      appliquer(nd);
      await enregistrerDonnees(nd);
      retour.replaceChildren(h("span", { classe: "succes" }, "Enregistré. Relancez une planification pour mettre l'horaire à jour."));
    } catch (err) { afficherErreur(retour, err); }
  };
  $("#fiche").hidden = false;
  $("#fiche-form").scrollTop = 0;
}

function ligneAttribution(d, a = { enseignants: [], periodes: 0, groupes: [] }) {
  const opts = [["", "—"], ...d.enseignants.map((e) => [e.id, e.nom || e.id])];
  return h("tr", {},
    h("td", {}, choix(null, opts, a.enseignants[0] || "")),
    h("td", {}, choix(null, opts, a.enseignants[1] || "")),
    h("td", {}, h("input", { type: "number", min: 1, valeur: a.periodes || "", classe: "court", "aria-label": "Périodes" })),
    h("td", {}, h("input", { valeur: a.groupes.join("; "), "aria-label": "Groupes" })),
    h("td", {}, peut("planificateur") ? h("button", { type: "button", classe: "petit danger", onclick: (e) => e.currentTarget.closest("tr").remove() }, "Retirer") : null));
}

function boutonVoirHoraire(par, id) {
  return h("button", { type: "button", classe: "petit", onclick: () => { fermerFiche(); montrerVue("horaires"); voirEntite(par, id); } }, "Voir son horaire");
}

function sectionSeancesCours(cid) {
  const zone = h("div", {}, h("h3", {}, "Horaire de ce cours"));
  const c = etat.horaire?.contenu;
  if (!c?.seances) { zone.append(h("p", { classe: "discret" }, "Aucun horaire calculé pour l'instant.")); return zone; }
  const liste = c.seances.filter((s) => s.cours === cid);
  if (!liste.length) { zone.append(h("p", { classe: "discret" }, "Ce cours n'a pas de séance dans l'horaire sélectionné.")); return zone; }
  const d = c.donnees || etat.jeu.donnees;
  const tb = h("tbody"), compteur = h("p", { classe: "discret" });
  const dessiner = () => {
    vider(tb);
    compteur.textContent = `${liste.length} séances, dont ${liste.filter((s) => s.distanciel).length} à distance.`;
    for (const s of liste) {
      tb.append(h("tr", { classe: s.distanciel ? "distanciel" : "" },
        h("td", {}, fmtCourt(s.date), h("div", { classe: "discret" }, JLONG[s.jour])),
        h("td", {}, `${s.heure_debut}–${s.heure_fin}`), h("td", {}, s.groupe),
        h("td", {}, s.enseignants.map((e) => nomEns(d, e)).join(" + ")),
        h("td", {}, s.distanciel ? h("span", { classe: "pastille bleu" }, "À distance") : s.local),
        h("td", {}, peut("planificateur") ? h("button", { type: "button", classe: "petit", onclick: async (e) => {
          e.currentTarget.disabled = true;
          try {
            const maj = await api(`/api/horaires/${etat.horaire.id}/seances/${s.id}`, { method: "PATCH", json: { distanciel: !s.distanciel } });
            Object.assign(s, maj);
            dessiner(); rendre();
          } catch (err) { e.currentTarget.textContent = err.message; }
        } }, s.distanciel ? "En présentiel" : "À distance") : null)));
    }
  };
  dessiner();
  zone.append(compteur,
    h("div", { classe: "defile hauteur-limitee" }, h("table", { classe: "tableau compact" },
      h("thead", {}, h("tr", {}, ["Date", "Heure", "Groupe", "Enseignant(s)", "Local", ""].map((x) => h("th", {}, x)))), tb)));
  return zone;
}

// ------------------------------------------------------------------ horaires
async function chargerListeHoraires(selection) {
  const liste = await api("/api/horaires" + (etat.jeu ? `?jeu=${etat.jeu.id}` : ""));
  const sel = vider($("#choix-horaire"));
  if (!liste.length) sel.append(h("option", { value: "" }, etat.jeu ? "Aucun horaire calculé pour ce jeu (onglet Données → Planifier)" : "Aucun horaire"));
  for (const x of liste) sel.append(h("option", { value: x.id }, `${fmtDateHeure(x.cree_le)} (${x.statut})`));
  const cible = selection || (liste.some((x) => x.id === etat.horaire?.id) ? etat.horaire.id : liste[0]?.id);
  if (cible) { sel.value = cible; await chargerHoraire(cible); }
  else { etat.horaire = null; $("#bloc-grille").hidden = $("#bloc-synthese").hidden = true; vider($("#horaire-alertes")); $("#statut-horaire").textContent = ""; }
}
$("#choix-horaire").addEventListener("change", (e) => chargerHoraire(e.target.value));

async function chargerHoraire(id) {
  clearTimeout(etat.sondage);
  const x = await api(`/api/horaires/${id}`);
  etat.horaire = x;
  const st = $("#statut-horaire");
  const libelles = { en_cours: "Calcul en cours…", OPTIMAL: "Optimal", REALISABLE: "Réalisable", IMPOSSIBLE: "Impossible", INCONNU: "Pas de solution", ERREUR: "Erreur", interrompu: "Interrompu" };
  st.textContent = libelles[x.statut] || x.statut;
  st.className = "pastille " + ({ OPTIMAL: "vert", REALISABLE: "vert", en_cours: "bleu" }[x.statut] || "rouge");
  const opt = $(`#choix-horaire option[value="${x.id}"]`);
  if (opt) opt.textContent = opt.textContent.replace(/\([^)]*\)$/, `(${x.statut})`);
  $("#export-xlsx").href = `/api/horaires/${id}/horaire.xlsx`;
  if (x.statut === "en_cours") {
    $("#bloc-grille").hidden = $("#bloc-synthese").hidden = true;
    vider($("#horaire-alertes"));
    etat.sondage = setTimeout(() => chargerListeHoraires(id), 2000);
    return;
  }
  const c = x.contenu || { seances: [], synthese: [], alertes: [], diagnostic: [] };
  const manque = c.synthese.reduce((s, l) => s + l.periodes_manquantes, 0);
  const alertes = [...(c.diagnostic || []), ...c.alertes.map((a) => ({ niveau: "avertissement", sujet: "", message: a }))];
  listeDiagnostic($("#horaire-alertes"), alertes, `Toutes les périodes sont planifiées (${c.seances.length} séances, calcul en ${Math.round(c.duree_s ?? 0)} s).`);
  if (manque) $("#horaire-alertes").prepend(h("li", { classe: "erreur" }, h("strong", {}, `${manque} périodes non planifiées au total.`)));
  afficherSynthese(c.synthese);
  $("#bloc-grille").hidden = $("#bloc-synthese").hidden = !c.seances.length;
  etat.vue.date = null;
  remplirEntites();
}

const donneesH = () => etat.horaire?.contenu?.donnees || etat.jeu?.donnees;
function structureJours() {
  const c = etat.horaire?.contenu;
  if (c?.structure_jours) return c.structure_jours;
  const cal = donneesH().calendrier, out = {};  // anciens horaires : reconstitution
  const min = (t) => +t.slice(0, 2) * 60 + +t.slice(3);
  const hhmm = (m) => { m = 5 * Math.round(m / 5); return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };
  for (const j of cal.jours_cours) {
    const per = [];
    for (const p of [...cal.creneaux].filter((p) => !p.jours?.length || p.jours.includes(j)).sort((a, b) => a.heure_debut.localeCompare(b.heure_debut))) {
      const d0 = min(p.heure_debut), d1 = min(p.heure_fin);
      for (let k = 0; k < p.periodes; k++) per.push({ rang: per.length, plage: p.id, libelle: p.libelle || p.id, debut: hhmm(d0 + (k * (d1 - d0)) / p.periodes), fin: hhmm(d0 + ((k + 1) * (d1 - d0)) / p.periodes) });
    }
    out[j] = per;
  }
  return out;
}

function afficherSynthese(lignes) {
  etat.synthese = lignes;
  const tb = vider($("#table-synthese tbody"));
  const seulementManques = $("#filtre-manques").checked;
  for (const l of lignes) {
    if (seulementManques && !l.periodes_manquantes) continue;
    const pct = Math.round((100 * l.periodes_planifiees) / l.periodes_requises);
    const barre = h("div", { classe: "barre" }, h("span"));
    barre.firstChild.style.width = Math.min(100, pct) + "%";
    if (l.periodes_manquantes) barre.classList.add("incomplet");
    tb.append(h("tr", { classe: l.periodes_manquantes ? "incomplet" : "", onclick: () => ouvrirFiche("cours", l.cours) },
      h("td", {}, l.groupe), h("td", { title: l.module_nom }, l.module), h("td", {}, l.cours_nom || l.cours),
      h("td", { title: l.enseignant_noms || "" }, l.enseignant_noms || l.enseignant || "–"),
      h("td", {}, l.distanciel ? h("span", { classe: "pastille bleu" }, "à distance") : "présentiel"),
      h("td", {}, `${l.periodes_planifiees} / ${l.periodes_requises}`), h("td", {}, barre),
      h("td", {}, (l.creneaux_hebdo || []).join(", ")), h("td", {}, (l.locaux || []).join(", ")),
      h("td", {}, fmtDate(l.premiere_seance)), h("td", {}, fmtDate(l.derniere_seance))));
  }
}
$("#filtre-manques").addEventListener("change", () => afficherSynthese(etat.synthese || []));

function remplirEntites() {
  const par = $("#vue-par").value, d = donneesH();
  etat.vue.par = par;
  $("#l-entite").hidden = par === "jour";
  $("#echelle").hidden = par === "jour";
  if (par !== "jour") {
    const src = { groupe: d.groupes, enseignant: d.enseignants, local: d.locaux }[par];
    const sel = vider($("#vue-entite"));
    for (const x of src) sel.append(h("option", { value: x.id }, x.nom ? `${x.nom} (${x.id})` : x.id));
    if (etat.vue.entite && src.some((x) => x.id === etat.vue.entite)) sel.value = etat.vue.entite;
    etat.vue.entite = sel.value;
  }
  rendre();
}
function voirEntite(par, id) {
  $("#vue-par").value = par;
  etat.vue.entite = id; etat.vue.date = null;
  remplirEntites();
}
$("#vue-par").addEventListener("change", () => { etat.vue.date = null; remplirEntites(); });
$("#vue-entite").addEventListener("change", (e) => { etat.vue.entite = e.target.value; etat.vue.date = null; rendre(); });
$$("#echelle button").forEach((b) => b.addEventListener("click", () => {
  etat.vue.echelle = b.dataset.echelle;
  $$("#echelle button").forEach((x) => x.classList.toggle("actif", x === b));
  rendre();
}));
$("#nav-date").addEventListener("change", (e) => { if (e.target.value) { etat.vue.date = e.target.value; rendre(); } });
$("#nav-prec").addEventListener("click", () => naviguer(-1));
$("#nav-suiv").addEventListener("click", () => naviguer(1));

function echelleActive() { return etat.vue.par === "jour" ? "jour" : etat.vue.echelle; }
function naviguer(sens) {
  const cal = donneesH().calendrier, e = echelleActive();
  if (e === "jour") {
    let d = etat.vue.date;
    for (let i = 0; i < 7; i++) { d = plusJ(d, sens); if (cal.jours_cours.includes(jourDe(d))) break; }
    etat.vue.date = d;
  } else if (e === "semaine") etat.vue.date = plusJ(etat.vue.date, 7 * sens);
  else if (e === "mois") etat.vue.date = plusM(etat.vue.date, sens);
  else if (e === "civile") etat.vue.date = `${+etat.vue.date.slice(0, 4) + sens}-01-01`;
  rendre();
}

function seancesFiltrees() {
  const s = etat.horaire.contenu.seances, par = etat.vue.par, id = etat.vue.entite;
  if (par === "jour") return s;
  return s.filter((x) => (par === "enseignant" ? x.enseignants.includes(id) : x[par] === id));
}
// implantation de ce qui est affiché (groupe ou local) ; inconnue pour un enseignant ou la vue « jour »
function implantationVue(d) {
  const { par, entite } = etat.vue;
  if (par === "groupe") return d.groupes.find((g) => g.id === entite)?.implantation || null;
  if (par === "local") return d.locaux.find((l) => l.id === entite)?.implantation || null;
  return null;
}
const fermeturesDuJour = (cal, date) => cal.fermetures.filter((f) => dans(f, date) && ((f.type || "ferie") !== "conges" || !cal.cours_pendant_conges));
const concerneSite = (f, impl) => !f.implantations?.length || (!!impl && f.implantations.includes(impl));
// fermeture de toute la journée pour ce qui est affiché
function fermeture(cal, date, d) {
  const impl = d ? implantationVue(d) : null;
  return fermeturesDuJour(cal, date).find((f) => !f.creneaux?.length && concerneSite(f, impl));
}
// fermetures qui ne touchent qu'un moment de la journée, ou un site quand l'implantation affichée est inconnue
function fermeturesPartielles(cal, date, d) {
  const impl = implantationVue(d);
  return fermeturesDuJour(cal, date).filter((f) => (f.creneaux?.length || f.implantations?.length)
    && !(!f.creneaux?.length && concerneSite(f, impl)) && (concerneSite(f, impl) || !impl));
}
function libelleFermeture(f, d) {
  const plages = (f.creneaux || []).map((c) => (d.calendrier.creneaux.find((x) => x.id === c)?.libelle || c).toLowerCase());
  const sites = (f.implantations || []).map((i) => d.etablissement.implantations.find((x) => x.id === i)?.nom || i);
  const portee = [plages.join(" + "), sites.join(", ")].filter(Boolean).join(", ");
  return (f.motif || TYPES_FERMETURE[f.type || "ferie"]) + (portee ? ` (${portee})` : "");
}
function stageGroupe(d, date) {
  if (etat.vue.par !== "groupe") return null;
  const g = d.groupes.find((x) => x.id === etat.vue.entite);
  return g?.stages.find((s) => dans(s, date) && (!s.jours.length || s.jours.includes(jourDe(date))));
}
function details(s) {
  const d = donneesH(), par = etat.vue.par;
  return [par !== "groupe" ? s.groupe : null, par !== "local" ? (s.distanciel ? "à distance" : s.local) : null,
    par !== "enseignant" ? s.enseignants.map((e) => nomEns(d, e)).join(" + ") : null].filter(Boolean).join(" · ");
}
function carteSeance(s, compacte = false) {
  const el = h("button", { type: "button", classe: "seance" + (s.distanciel ? " distanciel" : "") + (s.local === "À attribuer" ? " sans-local" : ""),
    title: `${s.cours_nom}\n${s.module_nom}\n${s.groupe_nom}\n${s.heure_debut}–${s.heure_fin} · ${s.distanciel ? "à distance" : s.local}`,
    onclick: () => ouvrirFiche("cours", s.cours) },
  compacte ? h("span", {}, h("b", {}, s.heure_debut), " ", s.cours_nom || s.cours) : [h("strong", {}, s.cours_nom || s.cours), h("span", {}, details(s))],
  s.distanciel && !compacte ? h("em", {}, "À distance") : null);
  el.style.setProperty("--teinte", teinte(s.module));
  return el;
}

function rendre() {
  if (!etat.horaire?.contenu?.seances?.length) return;
  const d = donneesH(), cal = d.calendrier, e = echelleActive();
  const seances = seancesFiltrees();
  if (!etat.vue.date) {
    const premiere = seances[0]?.date || cal.date_debut;
    etat.vue.date = e === "civile" ? `${premiere.slice(0, 4)}-01-01` : premiere;
  }
  $("#nav-date").value = etat.vue.date;
  $("#nav-prec").disabled = $("#nav-suiv").disabled = e === "scolaire";
  const ics = $("#export-ics");
  if (etat.vue.par === "jour") { ics.href = `/api/horaires/${etat.horaire.id}/horaire.ics`; ics.textContent = "Exporter agenda complet (.ics)"; }
  else { ics.href = `/api/horaires/${etat.horaire.id}/horaire.ics?type=${etat.vue.par}&id=${encodeURIComponent(etat.vue.entite)}`; ics.textContent = `Exporter agenda de ${etat.vue.entite} (.ics)`; }
  const zone = vider($("#rendu"));
  if (etat.vue.par === "jour") return rendreJourTousGroupes(zone, d, etat.vue.date);
  if (e === "jour") return rendreGrilleTemps(zone, d, [etat.vue.date], seances, `${fmtLong(etat.vue.date)}`);
  if (e === "semaine") {
    const l = lundi(etat.vue.date);
    const jours = JOURS.filter((j) => cal.jours_cours.includes(j)).map((j) => plusJ(l, JOURS.indexOf(j)));
    return rendreGrilleTemps(zone, d, jours, seances, `Semaine du ${fmtCourt(l)} au ${fmtCourt(plusJ(l, 6))} ${l.slice(0, 4)}`);
  }
  if (e === "mois") return rendreMois(zone, d, etat.vue.date, seances);
  if (e === "civile") { const a = etat.vue.date.slice(0, 4); return rendreAnnee(zone, d, `${a}-01-01`, `${a}-12-31`, seances, `Année civile ${a}`); }
  return rendreAnnee(zone, d, cal.date_debut, cal.date_fin, seances, `Année scolaire, du ${fmtDate(cal.date_debut)} au ${fmtDate(cal.date_fin)}`);
}

// grille horaire : colonnes = jours, lignes = périodes (selon les plages de chaque jour)
function rendreGrilleTemps(zone, d, dates, seances, titre) {
  $("#titre-periode").textContent = titre;
  const cal = d.calendrier, struct = structureJours();
  const lignes = [...new Map(dates.flatMap((dt) => struct[jourDe(dt)] || []).map((p) => [p.debut, p])).values()].sort((a, b) => a.debut.localeCompare(b.debut));
  if (!lignes.length) { zone.append(h("p", { classe: "discret" }, "Pas de cours ce jour-là.")); return; }
  const rangLigne = Object.fromEntries(lignes.map((p, i) => [p.debut, i]));
  const g = h("div", { classe: "grille-temps" });
  g.style.gridTemplateColumns = `5.5rem repeat(${dates.length}, minmax(${dates.length > 1 ? "8.5rem" : "16rem"}, 1fr))`;
  g.style.gridTemplateRows = `auto repeat(${lignes.length}, minmax(1.9rem, auto))`;
  const place = (el, col, l0, l1) => { el.style.gridColumn = String(col); el.style.gridRow = `${l0 + 2} / ${l1 + 3}`; return el; };
  g.append(h("div", { classe: "case entete-case coin" }));
  const implVue = implantationVue(d);
  dates.forEach((dt, i) => {
    // fermetures d'un site dont l'implantation affichée est inconnue : simple mention dans l'en-tête
    const mentions = fermeturesPartielles(cal, dt, d).filter((f) => !f.creneaux?.length || !concerneSite(f, implVue));
    const c = h("div", { classe: "case entete-case" }, JLONG[jourDe(dt)], h("small", {}, fmtCourt(dt)),
      mentions.map((f) => h("div", { classe: "marque ferme" }, libelleFermeture(f, d))));
    c.style.gridColumn = String(i + 2); c.style.gridRow = "1"; g.append(c);
  });
  lignes.forEach((p, i) => {
    const nouvellePlage = i === 0 || lignes[i - 1].plage !== p.plage;
    g.append(place(h("div", { classe: "case entete-ligne" + (nouvellePlage ? " debut-plage" : "") }, nouvellePlage ? h("b", {}, p.libelle) : null, h("small", {}, p.debut)), 1, i, i));
  });
  dates.forEach((dt, i) => {
    const col = i + 2, periodesJour = struct[jourDe(dt)] || [];
    lignes.forEach((p, k) => g.append(place(h("div", { classe: "case" + (periodesJour.some((q) => q.debut === p.debut) ? "" : " vide") + (lignes[k - 1]?.plage !== p.plage ? " debut-plage" : "") }), col, k, k)));
    const hors = dt < cal.date_debut || dt > cal.date_fin, f = fermeture(cal, dt, d), stg = stageGroupe(d, dt);
    if (hors || f || stg) g.append(place(h("div", { classe: "voile " + (hors ? "hors" : f ? "ferme" : "stage") }, hors ? "" : (f ? libelleFermeture(f, d) : stg.motif || "Stage")), col, 0, lignes.length - 1));
    else {
      for (const fp of fermeturesPartielles(cal, dt, d).filter((x) => x.creneaux?.length && concerneSite(x, implVue))) {
        for (const c of fp.creneaux) {
          const rangs = lignes.map((p, k) => (p.plage === c && periodesJour.some((q) => q.debut === p.debut) ? k : -1)).filter((k) => k >= 0);
          if (rangs.length) g.append(place(h("div", { classe: "voile ferme" }, libelleFermeture(fp, d)), col, rangs[0], rangs.at(-1)));
        }
      }
    }
    for (const s of seances.filter((x) => x.date === dt)) {
      const per = struct[s.jour] || [];
      const l0 = rangLigne[per[s.debut_periode]?.debut ?? s.heure_debut], l1 = rangLigne[per[s.debut_periode + s.periodes - 1]?.debut];
      if (l0 === undefined) continue;
      g.append(place(carteSeance(s), col, l0, l1 ?? l0));
    }
  });
  zone.append(g);
}

// vue « Jour » : tous les groupes, avec leurs locaux
function rendreJourTousGroupes(zone, d, date) {
  const cal = d.calendrier;
  if (!cal.jours_cours.includes(jourDe(date))) {  // aller au prochain jour de cours
    let x = date; for (let i = 0; i < 7 && !cal.jours_cours.includes(jourDe(x)); i++) x = plusJ(x, 1);
    etat.vue.date = date = x; $("#nav-date").value = date;
  }
  $("#titre-periode").textContent = `${fmtLong(date)} — tous les groupes`;
  const f = fermeture(cal, date, d);
  if (f) zone.append(h("p", { classe: "voile-texte" }, `Pas de cours : ${libelleFermeture(f, d)}`));
  for (const fp of fermeturesPartielles(cal, date, d)) zone.append(h("p", { classe: "voile-texte" }, `Pas de cours : ${libelleFermeture(fp, d)}`));
  const per = structureJours()[jourDe(date)] || [];
  const seances = etat.horaire.contenu.seances.filter((s) => s.date === date);
  const groupes = d.groupes.filter((g) => seances.some((s) => s.groupe === g.id));
  if (!groupes.length) { zone.append(h("p", { classe: "discret" }, "Aucun cours ce jour-là.")); return; }
  const g = h("div", { classe: "grille-jour" });
  g.style.gridTemplateColumns = `11rem repeat(${per.length}, minmax(3.2rem, 1fr))`;
  const place = (el, l, c0, c1) => { el.style.gridRow = String(l); el.style.gridColumn = `${c0} / ${c1}`; return el; };
  g.append(place(h("div", { classe: "case entete-case coin" }, "Groupe"), 1, 1, 2));
  per.forEach((p, i) => g.append(place(h("div", { classe: "case entete-case" + (per[i - 1]?.plage !== p.plage ? " debut-plage" : "") }, per[i - 1]?.plage !== p.plage ? h("b", {}, p.libelle) : null, h("small", {}, p.debut)), 1, i + 2, i + 3)));
  groupes.forEach((gr, k) => {
    const l = k + 2;
    g.append(place(h("button", { type: "button", classe: "case entete-ligne lien-groupe", onclick: () => ouvrirFiche("groupe", gr.id) }, h("b", {}, gr.nom || gr.id), h("small", {}, `${gr.id} · ${gr.effectif} pers.`)), l, 1, 2));
    per.forEach((p, i) => g.append(place(h("div", { classe: "case" + (per[i - 1]?.plage !== p.plage ? " debut-plage" : "") }), l, i + 2, i + 3)));
    for (const s of seances.filter((x) => x.groupe === gr.id)) {
      const c = h("button", { type: "button", classe: "seance" + (s.distanciel ? " distanciel" : "") + (s.local === "À attribuer" ? " sans-local" : ""), onclick: () => ouvrirFiche("cours", s.cours),
        title: `${s.cours_nom} · ${s.heure_debut}–${s.heure_fin}` },
      h("strong", {}, s.distanciel ? "À distance" : s.local), h("span", {}, s.cours_nom || s.cours), h("span", {}, s.enseignants.map((e) => nomEns(d, e)).join(" + ")));
      c.style.setProperty("--teinte", teinte(s.module));
      g.append(place(c, l, s.debut_periode + 2, s.debut_periode + s.periodes + 2));
    }
  });
  zone.append(g);
}

// vue mois : calendrier, une case par jour
function rendreMois(zone, d, date, seances) {
  const cal = d.calendrier, debut = `${date.slice(0, 8)}01`;
  $("#titre-periode").textContent = `${MOIS[+date.slice(5, 7) - 1]} ${date.slice(0, 4)}`;
  const jours = JOURS.filter((j) => cal.jours_cours.includes(j) || ["lun", "mar", "mer", "jeu", "ven"].includes(j));
  const g = h("div", { classe: "grille-mois" });
  g.style.gridTemplateColumns = `repeat(${jours.length}, minmax(7.5rem, 1fr))`;
  jours.forEach((j) => g.append(h("div", { classe: "case entete-case" }, JLONG[j])));
  const mois = debut.slice(0, 7);
  const dernier = plusJ(plusM(debut, 1), -1);
  const finGrille = plusJ(lundi(dernier), 6);
  for (let x = lundi(debut); x <= finGrille; x = plusJ(x, 1)) {
    if (jours.includes(jourDe(x))) {
      const dt = x, dansMois = dt.slice(0, 7) === mois;
      const f = fermeture(cal, dt, d), stg = stageGroupe(d, dt), partielles = f ? [] : fermeturesPartielles(cal, dt, d);
      const liste = seances.filter((s) => s.date === dt);
      const cas = h("div", { classe: "case jour-mois" + (dansMois ? "" : " autre-mois") + (dt < cal.date_debut || dt > cal.date_fin ? " hors" : "") },
        h("button", { type: "button", classe: "num-jour", title: "Voir la journée", onclick: () => { etat.vue.date = dt; choisirEchelle("jour"); } }, String(+dt.slice(8))),
        f ? h("div", { classe: "marque ferme" }, libelleFermeture(f, d)) : null,
        partielles.map((x) => h("div", { classe: "marque ferme" }, libelleFermeture(x, d))), stg ? h("div", { classe: "marque stage" }, stg.motif || "Stage") : null,
        liste.slice(0, 4).map((s) => carteSeance(s, true)),
        liste.length > 4 ? h("button", { type: "button", classe: "lien-plus", onclick: () => { etat.vue.date = dt; choisirEchelle("jour"); } }, `+ ${liste.length - 4} autres`) : null);
      g.append(cas);
    }
  }
  zone.append(g);
}
function choisirEchelle(e) {
  etat.vue.echelle = e;
  $$("#echelle button").forEach((x) => x.classList.toggle("actif", x.dataset.echelle === e));
  rendre();
}

// vue année : une ligne par mois, une case par jour (couleur = module du jour)
function rendreAnnee(zone, d, debut, fin, seances, titre) {
  $("#titre-periode").textContent = titre;
  const cal = d.calendrier, parDate = {};
  for (const s of seances) (parDate[s.date] ||= []).push(s);
  const t = h("table", { classe: "annee" });
  t.append(h("thead", {}, h("tr", {}, h("th", {}, ""), Array.from({ length: 31 }, (_, i) => h("th", {}, String(i + 1))), h("th", {}, "Pér."))));
  const tb = h("tbody");
  let m = `${debut.slice(0, 7)}-01`;
  let totalAn = 0;
  while (m <= fin) {
    const a = +m.slice(0, 4), mo = +m.slice(5, 7), nbJours = new Date(a, mo, 0).getDate();
    let total = 0;
    const tr = h("tr", {}, h("th", {}, `${MOIS[mo - 1].slice(0, 4)}. ${a}`));
    for (let j = 1; j <= 31; j++) {
      if (j > nbJours) { tr.append(h("td", { classe: "inexistant" })); continue; }
      const dt = `${m.slice(0, 8)}${String(j).padStart(2, "0")}`;
      const liste = parDate[dt] || [], f = fermeture(cal, dt, d), stg = stageGroupe(d, dt), fp = f ? [] : fermeturesPartielles(cal, dt, d);
      const per = liste.reduce((s, x) => s + x.periodes, 0);
      total += per;
      const td = h("td", { classe: [jourDe(dt) === "sam" || jourDe(dt) === "dim" ? "weekend" : "", dt < cal.date_debut || dt > cal.date_fin ? "hors" : "", f ? "ferme" : fp.length ? "partiel" : "", stg ? "stage" : ""].join(" ").trim() || null,
        title: `${fmtLong(dt)}${f ? ` — ${libelleFermeture(f, d)}` : ""}${fp.map((x) => ` — ${libelleFermeture(x, d)}`).join("")}${stg ? ` — ${stg.motif || "stage"}` : ""}${liste.length ? "\n" + liste.map((s) => `${s.heure_debut} ${s.cours_nom} (${s.groupe}, ${s.distanciel ? "à distance" : s.local})`).join("\n") : ""}` });
      if (liste.length) {
        const b = h("button", { type: "button", classe: "pastille-jour", "aria-label": `${fmtLong(dt)} : ${liste.length} séance(s)`, onclick: () => { etat.vue.date = dt; choisirEchelle("jour"); } },
          liste.length > 1 ? String(liste.length) : "");
        b.style.setProperty("--teinte", teinte(liste[0].module));
        td.append(b);
      }
      tr.append(td);
    }
    totalAn += total;
    tr.append(h("td", { classe: "total" }, total || ""));
    tb.append(tr);
    m = plusM(m, 1);
  }
  t.append(tb, h("tfoot", {}, h("tr", {}, h("th", { colspan: 32 }, "Total des périodes"), h("td", { classe: "total" }, totalAn))));
  zone.append(t);
}

// ------------------------------------------------------------------ administration
async function chargerAdmin() {
  const us = await api("/api/utilisateurs");
  const tb = vider($("#table-utilisateurs tbody"));
  for (const u of us) {
    tb.append(h("tr", { classe: u.actif ? "" : "inactif" },
      h("td", {}, u.identifiant), h("td", {}, u.role), h("td", {}, u.actif ? "oui" : "non"), h("td", {}, fmtDateHeure(u.cree_le)),
      h("td", {}, u.actif && u.identifiant !== etat.moi.identifiant
        ? h("button", { classe: "danger", onclick: async (e) => {
          const b = e.currentTarget;
          if (b.dataset.confirmer !== "1") { b.dataset.confirmer = "1"; b.textContent = "Confirmer ?"; return; }
          await api(`/api/utilisateurs/${u.id}/desactiver`, { method: "POST" }); chargerAdmin();
        } }, "Désactiver")
        : null)));
  }
  const j = await api("/api/journal");
  const p = $("#journal-integrite");
  p.textContent = j.integre ? "Intégrité vérifiée" : "ALERTE : journal altéré";
  p.className = "pastille " + (j.integre ? "vert" : "rouge");
  const tj = vider($("#table-journal tbody"));
  for (const l of j.lignes) tj.append(h("tr", {}, h("td", {}, l.ts.replace("T", " ").slice(0, 19)), h("td", {}, l.utilisateur || ""), h("td", {}, l.action), h("td", {}, l.cible || ""), h("td", {}, l.ip || "")));
}
$("#form-utilisateur").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  $("#retour-utilisateur").textContent = "";
  try {
    await api("/api/utilisateurs", { method: "POST", json: Object.fromEntries(f) });
    e.target.reset(); chargerAdmin();
  } catch (err) { $("#retour-utilisateur").textContent = err.message; }
});

demarrer();
