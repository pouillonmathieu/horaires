"use strict";
// Interface Horaires. Aucune donnée n'est insérée via innerHTML : tout passe par textContent (anti-XSS).

const etat = { moi: null, jeux: [], jeuCourant: null, horaire: null, donneesHoraire: null, semaine: null, sondage: null };
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"];
const JOURS_LONG = { lun: "Lundi", mar: "Mardi", mer: "Mercredi", jeu: "Jeudi", ven: "Vendredi", sam: "Samedi", dim: "Dimanche" };

function h(tag, attrs = {}, ...enfants) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "classe") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const e of enfants.flat()) if (e !== null && e !== undefined) el.append(e instanceof Node ? e : String(e));
  return el;
}
const vider = (el) => { el.replaceChildren(); return el; };
const fmtDate = (iso) => iso ? new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString("fr-BE") : "–";
const fmtDateHeure = (iso) => new Date(iso).toLocaleString("fr-BE", { dateStyle: "short", timeStyle: "short" });

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
  const rang = { lecteur: 0, planificateur: 1, admin: 2 };
  $$("[data-role]").forEach((el) => { el.hidden = rang[etat.moi.role] < rang[el.dataset.role]; });
  await chargerJeux();
  await chargerListeHoraires();
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
  if (v === "admin") chargerAdmin();
  if (v === "horaires") chargerListeHoraires();
}

// ------------------------------------------------------------------ jeux de données
async function chargerJeux() {
  etat.jeux = await api("/api/jeux");
  const choix = $("#completer-avec"), garde = choix.value;
  [...choix.options].slice(1).forEach((o) => o.remove());
  for (const j of etat.jeux) choix.append(h("option", { value: j.id }, `${j.nom} (${fmtDateHeure(j.cree_le)})`));
  choix.value = etat.jeux.some((j) => j.id === garde) ? garde : "";
  const tb = vider($("#table-jeux tbody"));
  if (!etat.jeux.length) tb.append(h("tr", {}, h("td", { colspan: 9, classe: "discret" }, "Aucune donnée importée. Commencez par le modèle Excel ou l'exemple.")));
  for (const j of etat.jeux) {
    tb.append(h("tr", {},
      h("td", {}, j.nom), h("td", {}, fmtDateHeure(j.cree_le)),
      h("td", {}, j.nb_groupes), h("td", {}, j.nb_modules), h("td", {}, j.nb_cours ?? "–"), h("td", {}, j.nb_enseignants), h("td", {}, j.nb_locaux),
      h("td", {}, j.nb_erreurs ? h("span", { classe: "pastille rouge" }, `${j.nb_erreurs} erreur(s)`) : h("span", { classe: "pastille vert" }, "OK")),
      h("td", { classe: "actions" },
        h("button", { onclick: () => ouvrirJeu(j.id) }, "Ouvrir"),
        h("a", { classe: "bouton", href: `/api/jeux/${j.id}/donnees.xlsx` }, "Excel"),
        h("a", { classe: "bouton", href: `/api/jeux/${j.id}/donnees-csv.zip` }, "CSV"),
        etat.moi.role !== "lecteur" ? h("button", { classe: "danger", onclick: () => supprimerJeu(j) }, "Supprimer") : null),
    ));
  }
}

function listeDiagnostic(ul, items, vide) {
  vider(ul);
  if (!items.length) { ul.append(h("li", { classe: "ok" }, vide)); return; }
  for (const d of items) {
    ul.append(h("li", { classe: d.niveau },
      h("strong", {}, d.sujet ? `${d.sujet} — ` : ""), d.message,
      d.suggestion ? h("div", { classe: "suggestion" }, "→ " + d.suggestion) : null));
  }
}

async function ouvrirJeu(id) {
  const j = await api(`/api/jeux/${id}`);
  etat.jeuCourant = j;
  $("#detail-jeu").hidden = false;
  $("#detail-jeu-titre").textContent = j.nom;
  listeDiagnostic($("#detail-jeu-diag"), j.diagnostic, "Aucune incohérence détectée : les données sont prêtes à être planifiées.");
  $("#detail-jeu").scrollIntoView({ behavior: "smooth" });
}

async function supprimerJeu(j) {
  if (!confirm(`Supprimer définitivement « ${j.nom} » et ses horaires ?`)) return;
  await api(`/api/jeux/${j.id}`, { method: "DELETE" });
  if (etat.jeuCourant?.id === j.id) $("#detail-jeu").hidden = true;
  chargerJeux();
}

function afficherRetourImport(res, err) {
  const zone = vider($("#retour-import"));
  if (err) {
    zone.append(h("p", { classe: "erreur" }, err.message));
    if (err.details?.length) zone.append(h("ul", { classe: "diagnostic" }, err.details.map((d) => h("li", { classe: "erreur" }, d))));
    return;
  }
  zone.append(h("p", { classe: "succes" }, `« ${res.nom} » importé : ${res.nb_groupes} groupes, ${res.nb_modules} modules, ${res.nb_cours} cours.`));
}

$("#form-import").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const bouton = e.target.querySelector("button[type=submit]");
  bouton.disabled = true;
  try {
    const res = await api("/api/jeux", { method: "POST", body: fd });
    afficherRetourImport(res); e.target.reset(); await chargerJeux(); ouvrirJeu(res.id);
  } catch (err) { afficherRetourImport(null, err); } finally { bouton.disabled = false; }
});

$("#btn-exemple").addEventListener("click", async () => {
  try { const res = await api("/api/jeux/exemple", { method: "POST" }); afficherRetourImport(res); await chargerJeux(); ouvrirJeu(res.id); }
  catch (err) { afficherRetourImport(null, err); }
});

$("#form-planif").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const res = await api(`/api/jeux/${etat.jeuCourant.id}/planifier`, {
    method: "POST", json: { duree_max_s: +f.get("duree_max_s"), poids_compacite: +f.get("poids_compacite") },
  });
  montrerVue("horaires");
  await chargerListeHoraires(res.id);
});

// ------------------------------------------------------------------ horaires
async function chargerListeHoraires(selection) {
  const liste = await api("/api/horaires");
  const noms = Object.fromEntries(etat.jeux.map((j) => [j.id, j.nom]));
  const sel = vider($("#choix-horaire"));
  if (!liste.length) sel.append(h("option", { value: "" }, "Aucun horaire calculé"));
  for (const x of liste) sel.append(h("option", { value: x.id }, `${noms[x.jeu_id] || "?"} — ${fmtDateHeure(x.cree_le)} (${x.statut})`));
  const cible = selection || etat.horaire?.id || liste[0]?.id;
  if (cible) { sel.value = cible; await chargerHoraire(cible); }
}
$("#choix-horaire").addEventListener("change", (e) => chargerHoraire(e.target.value));

async function chargerHoraire(id) {
  clearTimeout(etat.sondage);
  const x = await api(`/api/horaires/${id}`);
  etat.horaire = x;
  const st = $("#statut-horaire");
  const libelles = { en_cours: "Calcul en cours…", OPTIMAL: "Optimal", REALISABLE: "Réalisable (non prouvé optimal)", IMPOSSIBLE: "Impossible", INCONNU: "Pas de solution trouvée", ERREUR: "Erreur", interrompu: "Interrompu" };
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
  listeDiagnostic($("#horaire-alertes"), alertes,
    `Toutes les périodes sont planifiées (${c.seances.length} séances, calcul en ${c.duree_s ?? "?"} s).`);
  if (manque) $("#horaire-alertes").prepend(h("li", { classe: "erreur" }, h("strong", {}, `${manque} périodes non planifiées au total.`)));

  if (!etat.donneesHoraire || etat.donneesHoraire.id !== x.jeu_id) {
    const j = await api(`/api/jeux/${x.jeu_id}`);
    etat.donneesHoraire = { id: x.jeu_id, ...j.donnees };
  }
  afficherSynthese(c.synthese);
  $("#bloc-grille").hidden = $("#bloc-synthese").hidden = !c.seances.length;
  etat.semaine = null;
  remplirEntites();
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
    tb.append(h("tr", { classe: l.periodes_manquantes ? "incomplet" : "" },
      h("td", {}, l.groupe), h("td", { title: l.module_nom }, l.module), h("td", {}, `${l.cours} – ${l.cours_nom}`),
      h("td", { title: l.enseignant_noms || "" }, l.enseignant || "–"),
      h("td", {}, `${l.periodes_planifiees} / ${l.periodes_requises}`), h("td", {}, barre),
      h("td", {}, (l.creneaux_hebdo || []).join(", ")), h("td", {}, (l.locaux || []).join(", ")),
      h("td", {}, fmtDate(l.premiere_seance)), h("td", {}, fmtDate(l.derniere_seance))));
  }
}
$("#filtre-manques").addEventListener("change", () => afficherSynthese(etat.synthese || []));

function remplirEntites() {
  const par = $("#vue-par").value;
  const d = etat.donneesHoraire;
  const src = { groupe: d.groupes, enseignant: d.enseignants, local: d.locaux }[par];
  const sel = vider($("#vue-entite"));
  for (const x of src) sel.append(h("option", { value: x.id }, x.nom ? `${x.id} – ${x.nom}` : x.id));
  afficherGrille();
}
$("#vue-par").addEventListener("change", remplirEntites);
$("#vue-entite").addEventListener("change", () => { etat.semaine = null; afficherGrille(); });
$("#sem-prec").addEventListener("click", () => { etat.semaine = ajouterJours(etat.semaine, -7); afficherGrille(); });
$("#sem-suiv").addEventListener("click", () => { etat.semaine = ajouterJours(etat.semaine, 7); afficherGrille(); });

function iso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function ajouterJours(isoDate, n) { const d = new Date(isoDate + "T00:00:00"); d.setDate(d.getDate() + n); return iso(d); }
function lundi(isoDate) { const d = new Date(isoDate + "T00:00:00"); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d); }
function couleur(cle) { let x = 0; for (const c of cle) x = (x * 31 + c.charCodeAt(0)) % 360; return x; }
const dans = (p, j) => p.date_debut <= j && j <= p.date_fin;

function afficherGrille() {
  const par = $("#vue-par").value, id = $("#vue-entite").value;
  const d = etat.donneesHoraire, cal = d.calendrier;
  const seances = etat.horaire.contenu.seances.filter((s) => (par === "enseignant" ? s.enseignants.includes(id) : s[par] === id));
  if (!etat.semaine) etat.semaine = lundi(seances[0]?.date || cal.date_debut);
  const exportIcs = $("#export-ics");
  exportIcs.href = `/api/horaires/${etat.horaire.id}/horaire.ics?type=${par}&id=${encodeURIComponent(id)}`;
  exportIcs.textContent = `Exporter agenda de ${id} (.ics)`;

  const jours = JOURS.filter((j) => cal.jours_cours.includes(j)).map((j) => ({ j, date: ajouterJours(etat.semaine, JOURS.indexOf(j)) }));
  $("#sem-titre").textContent = `Semaine du ${fmtDate(etat.semaine)}`;
  const creneaux = [...cal.creneaux].sort((a, b) => a.heure_debut.localeCompare(b.heure_debut));
  const groupe = par === "groupe" ? d.groupes.find((g) => g.id === id) : null;

  const grille = vider($("#grille"));
  grille.style.gridTemplateColumns = `7rem repeat(${jours.length}, minmax(8rem, 1fr))`;
  grille.append(h("div", { classe: "case entete-case" }));
  for (const { j, date } of jours) grille.append(h("div", { classe: "case entete-case" }, JOURS_LONG[j], h("small", {}, fmtDate(date))));
  for (const c of creneaux) {
    grille.append(h("div", { classe: "case entete-ligne" }, c.libelle || c.id, h("small", {}, `${c.heure_debut}–${c.heure_fin}`)));
    for (const { date } of jours) {
      const cas = h("div", { classe: "case" });
      const hors = date < cal.date_debut || date > cal.date_fin;
      const ferme = cal.fermetures.find((f) => dans(f, date));
      const stage = groupe?.stages.find((s) => dans(s, date) && (!s.jours.length || s.jours.includes(JOURS[(new Date(date + "T00:00:00").getDay() + 6) % 7])));
      if (hors) cas.classList.add("hors");
      else if (ferme) cas.append(h("div", { classe: "marque ferme" }, ferme.motif || "Fermé"));
      else if (stage) cas.append(h("div", { classe: "marque stage" }, stage.motif || "Stage"));
      for (const s of seances.filter((s) => s.date === date && s.creneau === c.id)) {
        const carte = h("div", { classe: "seance", title: `${s.module} – ${s.module_nom}\n${s.cours_nom}\n${s.groupe_nom}\n${s.local} · ${s.enseignant}` },
          h("strong", {}, s.cours), h("span", {}, s.cours_nom || s.module_nom),
          h("small", {}, [par !== "groupe" ? s.groupe : null, par !== "local" ? s.local : null, par !== "enseignant" ? s.enseignant : null].filter(Boolean).join(" · ")));
        carte.style.setProperty("--teinte", couleur(s.module));
        if (s.local === "À attribuer") carte.classList.add("sans-local");
        cas.append(carte);
      }
      grille.append(cas);
    }
  }
}

// ------------------------------------------------------------------ administration
async function chargerAdmin() {
  const us = await api("/api/utilisateurs");
  const tb = vider($("#table-utilisateurs tbody"));
  for (const u of us) {
    tb.append(h("tr", { classe: u.actif ? "" : "inactif" },
      h("td", {}, u.identifiant), h("td", {}, u.role), h("td", {}, u.actif ? "oui" : "non"), h("td", {}, fmtDateHeure(u.cree_le)),
      h("td", {}, u.actif && u.identifiant !== etat.moi.identifiant
        ? h("button", { classe: "danger", onclick: async () => { if (confirm(`Désactiver ${u.identifiant} ?`)) { await api(`/api/utilisateurs/${u.id}/desactiver`, { method: "POST" }); chargerAdmin(); } } }, "Désactiver")
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
