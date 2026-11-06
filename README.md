# Horaires : planification des formations pour adultes

Application web qui construit automatiquement les horaires de formation à partir des contraintes de
l'établissement, explique ce qui bloque quand tout ne peut pas être placé, et exporte le résultat
(Excel, agendas `.ics`).

## Ce que le moteur prend en compte

| Contrainte | Où la saisir |
|---|---|
| Modules composés de plusieurs cours | `Modules`, `Cours` |
| Cours **réparti** entre enseignants (ex. 40 p. + 20 p.) | plusieurs lignes `Attributions` pour un cours |
| **Co-enseignement** (plusieurs enseignants présents ensemble) | plusieurs enseignants sur une ligne `Attributions` |
| Enseignant différent selon le groupe | colonne `Attributions.groupes` |
| Modules préalables (prérequis) : un module se termine avant le début du suivant | `Modules.prerequis` |
| Jours de cours et créneaux (matin / après-midi / soir…) par groupe | `Parametres`, `Creneaux`, `Groupes.jours_cours`, `Groupes.creneaux` |
| Jours de stage (pas de cours) | `Stages` |
| Jours fériés, congés, vacances | `Fermetures` |
| Taille du groupe et capacité des locaux | `Groupes.effectif`, `Locaux.capacite` |
| Disponibilités et indisponibilités des enseignants | `Enseignants.disponibilites`, `Indisponibilites` |
| Indisponibilités des locaux (travaux, examens…) | `Indisponibilites` (type `local`) |
| Équipement fixe d'un local (PC, lit médicalisé…) | `Locaux.equipements`, `Cours.equipements` |
| Matériel mobile en quantité limitée | `Ressources`, `Cours.ressources` |
| Local attitré d'un groupe | `Groupes.local_prefere` |
| Rythme **fixe** (mêmes jours/heures du début à la fin) ou **souple** (changements, pauses et reprises) | `Cours.rythme`, `Parametres.rythme_defaut` |
| Plafond hebdomadaire d'un enseignant, rythme maximal d'un cours | `max_periodes_semaine`, `max_seances_semaine` |

## Import des données

Trois formats, mêmes tables :

- **CSV**, pour les exports du logiciel de gestion : un fichier par table, nommé comme la table
  (`parametres.csv`, `creneaux.csv`, `fermetures.csv`, `locaux.csv`, `ressources.csv`, `enseignants.csv`,
  `indisponibilites.csv`, `modules.csv`, `cours.csv`, `attributions.csv`, `groupes.csv`, `stages.csv`).
  Envoyez-les tous d'un coup ou dans un `.zip`. L'import tolère :
  - les séparateurs `;`, `,` et tabulation ;
  - les encodages UTF-8 (avec ou sans BOM) et Windows-1252 ;
  - les dates `JJ/MM/AAAA` ou `AAAA-MM-JJ` ;
  - les en-têtes avec accents ou majuscules ;
  - les identifiants avec espaces ou « / » (ex. `UE 7/12`).
- **Excel** : un onglet par table (`exemples/modele_horaires.xlsx`).
- **JSON** : la structure interne complète.

Les modèles Excel et CSV sont téléchargeables depuis l'application et contiennent un exemple complet.
La spécification détaillée des fichiers CSV, à transmettre à la personne qui configure l'export du
logiciel de gestion, est dans [`docs/FORMAT_CSV.md`](docs/FORMAT_CSV.md).

**Import partiel.** Il suffit de réinjecter ce que le logiciel de gestion exporte (modules, cours,
attributions, groupes…). Avec l'option « Compléter ce qui manque avec », les autres tables (créneaux,
locaux, congés, stages…) et les colonnes absentes (disponibilités des enseignants…) sont reprises d'un
jeu existant. Les liens peuvent aussi être fournis une ligne par lien (`parcours.csv`, `prerequis.csv`).
Les données importées peuvent aussi être réexportées en Excel ou en CSV, corrigées, puis réimportées.

## Le « moteur IA »

Le cœur du moteur est de l'**optimisation sous contraintes** (Google OR-Tools CP-SAT). C'est la
technique d'IA adaptée aux horaires : elle garantit que **toutes les contraintes dures sont
respectées**, ce qu'un modèle de langage (type ChatGPT/Claude) ne sait pas garantir. Elle tourne
aussi entièrement sur votre serveur : aucune donnée ne sort.

**Modèle hebdomadaire récurrent.** Chaque cours a lieu sur un ou plusieurs créneaux hebdomadaires
(« le mardi soir, en A101, du 12/01 au 23/03 »). Un cours **souple** (par défaut) peut changer de
créneau, faire une pause puis reprendre, ou changer de local. Le moteur favorise malgré tout la
régularité. Un cours **fixe** garde les mêmes jours et heures du début à la fin. Les séances réelles sont ensuite calculées date par date : jours fériés, vacances, stages et
absences ponctuelles sont sautés, et le cours se prolonge d'autant. Cette formulation reste compacte
même pour un grand établissement.

**Étapes de calcul**

1. **Contrôle des données** : cycles de prérequis, aucun local assez grand, enseignant surchargé,
   volume horaire impossible à caser…
2. **Heuristique constructive** : un premier horaire complet en quelques secondes.
3. **Optimisation du volume** (CP-SAT) : placer le maximum de périodes.
4. **Optimisation de la qualité**, à volume égal : finir tôt, resserrer ou étaler les cours (réglable).
5. **Attribution des locaux**, créneau par créneau : même salle sur toute la durée d'un cours, local
   attitré en priorité, salles spécialisées réservées aux cours qui en ont besoin. La capacité en salles
   est déjà garantie à l'étape 3.

S'il manque de la place, le moteur ne s'arrête pas sur « pas de solution ». Il produit le meilleur
horaire possible et signale, cours par cours, les périodes non placées, la cause et quoi changer.

**Temps de calcul.** Plus on laisse de temps, meilleur est le résultat. L'exemple fourni est résolu de
façon optimale en quelques secondes. Pour un grand établissement, prévoir 10 à 30 minutes (voir
« Performances » plus bas). `scripts/generer_instance.py` génère un établissement fictif de la taille voulue pour tester.

Un assistant conversationnel (LLM) pourra être ajouté par-dessus, par exemple pour saisir une
contrainte en langage naturel (« Mme Lambert n'est plus disponible le vendredi ») ou commenter un
horaire. Voir la feuille de route.

## Sécurité

| Mesure | Détail |
|---|---|
| Chiffrement au repos | Toutes les données métier (noms, modules, horaires) sont chiffrées en **AES-256-GCM**. Chaque bloc est lié à son enregistrement : une copie ou une falsification est détectée. Les tests vérifient qu'aucun nom n'apparaît en clair dans la base. |
| Clé maîtresse | Fournie par variable d'environnement ou coffre-fort de secrets, jamais stockée avec la base. |
| Mots de passe | Argon2id, 12 caractères minimum, comparaison à temps constant. |
| Anti-force brute | Blocage de 15 min après 5 échecs (par compte et par IP). |
| Sessions | Jeton aléatoire dont seule l'empreinte est stockée côté serveur. Cookie `__Host-`, `HttpOnly`, `Secure`, `SameSite=Strict`. Expiration après 30 min d'inactivité et 8 h au maximum. |
| CSRF | Jeton lié à la session, exigé sur toute modification. |
| Rôles | `lecteur` (consulter, exporter), `planificateur` (importer, planifier), `admin` (comptes, journal). |
| Journal d'audit | Connexions, imports, calculs, exports, gestion des comptes. Chaînage SHA-256 : une modification a posteriori est signalée dans l'interface. |
| Navigateur | CSP stricte (aucun script tiers ni inline), HSTS, anti-clickjacking, `no-referrer`, pas de cache des réponses API. Aucune donnée insérée via `innerHTML` (anti-XSS). |
| Imports | Taille limitée, type détecté par le contenu, protection contre les « zip bombs » et les attaques XML (`defusedxml`), validation stricte et références croisées vérifiées. |
| Conteneur | Utilisateur non privilégié, système de fichiers en lecture seule, aucune capacité Linux. |
| RGPD | Aucune donnée d'apprenant n'est nécessaire : seuls les effectifs des groupes sont utilisés. |

À prévoir côté hébergement : HTTPS (inclus via Caddy), sauvegardes chiffrées du volume `donnees`,
hébergement dans l'UE ou sur site, et idéalement une authentification unique (SSO, voir feuille de route).

## Démarrage

### En local (développement)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export HORAIRES_MODE_DEV=1 HORAIRES_COOKIE_SECURE=0   # dev uniquement : clé auto-générée, HTTP local
python -m scripts.creer_admin admin
uvicorn app.main:app --reload
```

Ouvrez <http://localhost:8000>, connectez-vous, puis cliquez sur « Charger l'exemple » et « Planifier ».

### Tester sans rien installer : GitHub Codespaces

Depuis un simple navigateur, sans installer Python :

1. Sur la page du dépôt GitHub : bouton vert **Code** → onglet **Codespaces** → **Create codespace on main**.
2. Patientez 2 à 3 minutes : l'installation se fait toute seule. Le terminal affiche alors
   l'**identifiant** (`admin`) et un **mot de passe** de test.
3. L'application s'ouvre dans un nouvel onglet. Sinon : onglet **Ports** en bas → icône 🌐 sur le port 8000.

L'adresse n'est accessible qu'à votre compte GitHub (port privé). La machine s'arrête d'elle-même
après 30 minutes d'inactivité ; on la relance depuis la même page (**Code → Codespaces**).
**Données fictives uniquement** : c'est un environnement de test, hébergé par GitHub, sans la clé de
chiffrement gérée comme en production. Le quota gratuit de GitHub couvre environ 60 heures par mois.

### Sur un poste Windows, pour tester

Pré-requis : Python 3.12 ou plus récent ([python.org](https://www.python.org/downloads/), cochez
« Add Python to PATH »). Dans PowerShell, depuis le dossier du projet :

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:HORAIRES_MODE_DEV="1"; $env:HORAIRES_COOKIE_SECURE="0"
python -m scripts.creer_admin admin
uvicorn app.main:app
```

Puis ouvrez <http://localhost:8000>. Les données restent sur le poste, dans le dossier `donnees`.

### En production (Docker + HTTPS)

```bash
cp .env.exemple .env && chmod 600 .env    # renseigner HORAIRES_CLE_MAITRE et DOMAINE
docker compose up -d --build
docker compose exec horaires python -m scripts.creer_admin admin
```

⚠️ Conservez la clé maîtresse en lieu sûr : sans elle, les données sont **irrécupérables**.

### Tests

```bash
pytest
```

Les 25 tests couvrent le moteur, les imports et la sécurité :
- **moteur** : aucune double réservation (groupe, local, enseignant), respect de chaque contrainte,
  prérequis, co-enseignement, répartition entre enseignants, attributions par groupe, matériel et
  plafonds, salle fixe par créneau, signalement des manques, cohérence complète sur un établissement généré ;
- **imports** : allers-retours Excel et CSV, CSV Windows-1252 avec dates belges, identifiants du logiciel de gestion ;
- **sécurité** : accès anonyme refusé, CSRF, rôles, force brute, chiffrement au repos, détection de
  falsification, parcours complet par l'API.

## Performances

Mesures sur un établissement fictif à la taille maximale visée, généré par `scripts/generer_instance.py`
(machine de test à 4 cœurs) :
- **taille** : 100 groupes (≈ 2 000 apprenants), 200 enseignants, 458 modules, 920 cours, 35 100 périodes, 71 locaux ;
- **calendrier** : une année scolaire avec congés et stages.

| Durée de calcul | Périodes placées | Séances | Séances sans local |
|---|---|---|---|
| Heuristique seule (< 1 s) | 93,1 % | | |
| 10 min | 95,9 % | 8 535 | 0 |
| 30 min | **97,6 %** | 8 687 | 0 |

Les périodes non placées sont listées cours par cours, avec leur cause. Une partie est inévitable avec
ces données : par exemple, un enseignant dont le plafond hebdomadaire ne suffit pas pour ses attributions.
Avec des données réelles, généralement moins tendues que ce générateur volontairement chargé, le taux
devrait être plus élevé.

## Architecture

```
app/
  modeles.py            modèle de données + validation stricte
  solveur/moteur.py     modèle d'optimisation CP-SAT (hebdomadaire récurrent) + attribution des locaux
  solveur/heuristique.py premier horaire rapide (point de départ du solveur)
  solveur/diagnostic.py explications lisibles (avant / après calcul)
  fichiers.py           import/export Excel, CSV & JSON, export Excel/iCalendar des horaires
  connecteurs/          sources de données : fichiers (Excel/CSV/JSON) aujourd'hui, logiciel de gestion demain
scripts/
  creer_admin.py        création / réinitialisation d'un administrateur
  generer_instance.py   établissement fictif de taille paramétrable (tests de charge)
  chiffrement.py        AES-256-GCM
  stockage.py           SQLite chiffré + journal d'audit chaîné
  securite.py           authentification, sessions, rôles, CSRF, anti-force brute
  main.py               API web
  static/               interface (HTML/CSS/JS sans dépendance externe)
```

## Feuille de route

1. **Connecteur vers votre logiciel de gestion.** Le gabarit `app/connecteurs/api_gestion.py` est
   prêt. Il reste à définir la correspondance des champs : API REST, vues SQL en lecture seule ou
   export planifié. Le reste de l'application ne change pas.
2. **Édition manuelle** dans la grille (glisser-déposer, verrouillage de séances puis recalcul du reste).
3. **Préférences « souples »** : jours préférés des enseignants, regroupement des cours d'un même
   enseignant, régularité hebdomadaire, limite d'heures par jour.
4. **SSO** (Microsoft Entra ID / Google) et authentification à deux facteurs.
5. **Assistant en langage naturel** : il traduirait une demande en modification de contraintes, en
   n'envoyant que des identifiants anonymisés au modèle de langage, ou avec un modèle hébergé localement.
