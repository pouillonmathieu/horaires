# Format CSV attendu

Ce document décrit les fichiers à exporter de votre logiciel de gestion pour alimenter
l'application Horaires. Le modèle `exemples/modele_horaires_csv.zip`, téléchargeable aussi depuis
l'application avec « Modèle CSV », contient un exemple complet de chaque fichier.

## Règles générales

- **Un fichier par table**, nommé comme la table : `cours.csv`, `groupes.csv`… Les majuscules et
  les accents sont ignorés. Envoyez les fichiers ensemble, ou dans un `.zip`.
- **Première ligne = noms des colonnes.** L'ordre des colonnes est libre. Les colonnes inconnues
  sont ignorées.
- **Séparateur** : `;` (recommandé), `,` ou tabulation. **Encodage** : UTF-8 ou Windows (export Excel).
- **Dates** : `JJ/MM/AAAA` ou `AAAA-MM-JJ`. **Heures** : `18:00` ou `18h00`.
- **Listes dans une cellule** (ex. plusieurs enseignants) : séparées par `|`, ou par `;` si la
  cellule est entre guillemets.
- **Identifiants** : utilisez les codes de votre logiciel tels quels (ex. `UE 7/12`, `MAT-0042`).
  Ils doivent être **identiques d'un fichier à l'autre et d'un export à l'autre**. Seuls les
  caractères `; , | : < > " ' \` y sont interdits.

## Ce que votre logiciel exporte (à chaque import)

Ce sont les données qui changent d'une année ou d'un quadrimestre à l'autre.

### `modules.csv` : modules / unités d'enseignement
| Colonne | Obligatoire | Exemple | Remarque |
|---|---|---|---|
| `id` | oui | `UE-COMPTA-1` | |
| `nom` | | `Comptabilité générale` | |
| `prerequis` | | `UE-BASE` | ou fichier `prerequis.csv` ci-dessous |

### `cours.csv` : cours (activités d'enseignement) de chaque module
| Colonne | Obligatoire | Exemple | Remarque |
|---|---|---|---|
| `id` | oui | `AE-1234` | unique dans tout le fichier |
| `module` | oui | `UE-COMPTA-1` | |
| `nom` | | `Comptabilité – théorie` | |
| `periodes` | oui | `60` | nombre total de périodes du cours |
| `equipements` | | `pc` | équipement fixe exigé dans la salle |
| `ressources` | | `mannequin` | matériel mobile (voir `ressources.csv`) |
| `max_seances_semaine` | | `2` | |
| `rythme` | | `fixe` | `fixe` = mêmes jours/heures du début à la fin ; `souple` (défaut) = changements, pauses et reprises permis |
| `bloc` | | `2` | taille des séances : `plage` (plage entière), `2` à `9` périodes (`8`/`9` = journée complète), `modulable` ; vide = paramètre de l'établissement |
| `implantation` | | `NORD` | si le cours est donné ailleurs que sur l'implantation du groupe |
| `locaux` | | `INFO1\|INFO2` | locaux imposés (vide = tout local compatible) |
| `periodes_distanciel` | | `12` | cours hybride : périodes données à distance, sans local |

### `attributions.csv` : qui donne quoi
Une ligne par enseignant et par cours, avec ses périodes.

| Colonne | Obligatoire | Exemple | Remarque |
|---|---|---|---|
| `cours` | oui | `AE-1234` | |
| `enseignants` | oui | `E-017` | plusieurs enseignants sur **une même ligne** = co-enseignement (présents ensemble) : `E-017\|E-052` |
| `periodes` | oui | `40` | la somme des lignes d'un cours doit égaler ses périodes |
| `groupes` | | `G-2027-SOIR` | si l'attribution ne vaut que pour certains groupes |

Un cours **réparti** entre deux enseignants qui donnent chacun leurs séances s'écrit en deux lignes :
`AE-1234;E-017;40` et `AE-1234;E-052;20`.

### `groupes.csv` : groupes-classes
| Colonne | Obligatoire | Exemple | Remarque |
|---|---|---|---|
| `id` | oui | `G-2027-SOIR` | |
| `nom` | | `Comptabilité soir` | |
| `effectif` | oui | `18` | nombre d'inscrits (pas de liste nominative) |
| `implantation` | | `CENTRE` | implantation où la formation est organisée |
| `modules` | | `UE-COMPTA-1\|UE-TVA` | ou fichier `parcours.csv` ci-dessous |
| `date_debut`, `date_fin` | | `14/09/2026` | dates propres au groupe, sinon celles du calendrier |

### `parcours.csv` (alternative à `groupes.modules`) : une ligne par module suivi
| `groupe` | `module` |
|---|---|
| `G-2027-SOIR` | `UE-COMPTA-1` |

### `prerequis.csv` (alternative à `modules.prerequis`) : une ligne par prérequis
| `module` | `prerequis` |
|---|---|
| `UE-TVA` | `UE-COMPTA-1` |

### `enseignants.csv` : identifiants et noms
| Colonne | Obligatoire | Exemple |
|---|---|---|
| `id` | oui | `E-017` |
| `nom` | | `J. Peeters` |

## Ce qui est saisi une fois puis réutilisé

Ces données n'existent souvent pas dans le logiciel de gestion. Les paramètres généraux (année scolaire,
plages horaires, blocs, implantations, règles de déplacement) se saisissent dans l'assistant de première
connexion, puis dans le menu **Paramètres** ; locaux, disponibilités et contrats se complètent dans les
fiches de l'onglet **Données**, ou une fois dans le modèle Excel ou CSV. Ensuite, à l'import, choisissez « **Compléter ce qui manque avec** » un jeu
existant : les tables absentes de l'export sont reprises telles quelles. Les colonnes absentes d'une
table présente (par exemple les disponibilités dans `enseignants.csv`) sont complétées en rapprochant
les identifiants.

| Fichier | Contenu |
|---|---|
| `parametres.csv` | `cle;valeur` : `date_debut`, `date_fin` (année scolaire), `jours_cours` (ex. `lun\|mar\|mer\|jeu\|ven\|sam`), `cours_pendant_conges` (oui/non), `rythme_defaut`, `bloc_defaut`, `multi_sites_meme_jour` (oui/non), `delai_deplacement_min`, `enseignants_toutes_implantations` (oui/non), `max_periodes_semaine_defaut` |
| `implantations.csv` | `id;nom;adresse` : sites de l'établissement (inutile s'il n'y en a qu'un) |
| `creneaux.csv` | plages horaires : `id;libelle;heure_debut;heure_fin;periodes;jours` (ex. `soir;Soir;18:00;21:20;4;` ou `sam;Samedi;08:00;12:10;5;sam`) |
| `fermetures.csv` | `date_debut;date_fin;motif;type;creneaux;implantations` : `type` = `ferie` (toujours fermé), `conges` (congé scolaire) ou `special` (jour spécial : formation continue, fête de l'école…, fermé même si `cours_pendant_conges` = oui). `creneaux` (ex. `aprem\|soir`) et `implantations` (ex. `CENTRE`) limitent la fermeture à ces plages ou à ces sites ; vides = toute la journée, partout. Ces deux colonnes sont facultatives. |
| `locaux.csv` | `id;nom;implantation;capacite;equipements;disponibilites` : `disponibilites` (facultatif) = jours et plages où le local peut servir, même format que pour les enseignants (ex. `lun:*\|mar:*\|mer:matin`) ; vide = toujours disponible |
| `ressources.csv` | `id;nom;quantite` : matériel mobile partagé |
| `enseignants.csv` (colonnes en plus) | `disponibilites` (ex. `lun:soir\|mer:*`), `max_periodes_semaine`, `implantations`, `date_debut` (engagement), `date_fin` (fin de contrat, pension) |
| `indisponibilites.csv` | `type;id;date_debut;date_fin;creneaux;motif` (`type` = `enseignant` ou `local`) |
| `groupes.csv` (colonnes en plus) | `jours_cours`, `creneaux` (plages autorisées), `local_prefere` |
| `stages.csv` | `groupe;date_debut;date_fin;jours;motif` |

## En pratique

1. Première fois : remplissez le modèle complet et importez-le.
2. Ensuite : exportez de votre logiciel `modules`, `cours`, `attributions`, `groupes` (+ `parcours`),
   `enseignants`, puis importez ces fichiers en choisissant « Compléter ce qui manque avec » le jeu précédent.
3. Si l'export de votre logiciel utilise d'autres noms de colonnes ou une autre structure,
   une correspondance peut être ajoutée dans l'application à partir d'un échantillon.
