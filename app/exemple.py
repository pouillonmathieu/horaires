"""Jeu de données d'exemple (fictif) : sert de modèle Excel téléchargeable et de base de tests."""
from .modeles import JeuDeDonnees

EXEMPLE = {
    "nom": "Exemple – 2e quadrimestre 2027",
    "calendrier": {
        "date_debut": "2027-01-11",
        "date_fin": "2027-06-25",
        "jours_cours": ["lun", "mar", "mer", "jeu", "ven"],
        "creneaux": [
            {"id": "matin", "libelle": "Matin", "heure_debut": "08:30", "heure_fin": "12:00", "periodes": 4},
            {"id": "aprem", "libelle": "Après-midi", "heure_debut": "13:00", "heure_fin": "16:20", "periodes": 4},
            {"id": "soir", "libelle": "Soir", "heure_debut": "18:00", "heure_fin": "21:20", "periodes": 4},
        ],
        "fermetures": [
            {"date_debut": "2027-02-15", "date_fin": "2027-02-26", "motif": "Vacances de détente"},
            {"date_debut": "2027-03-29", "date_fin": "2027-03-29", "motif": "Lundi de Pâques"},
            {"date_debut": "2027-04-26", "date_fin": "2027-05-07", "motif": "Vacances de printemps"},
            {"date_debut": "2027-05-17", "date_fin": "2027-05-17", "motif": "Lundi de Pentecôte"},
        ],
    },
    "locaux": [
        {"id": "A101", "nom": "Salle A101", "capacite": 24, "equipements": ["projecteur"]},
        {"id": "A102", "nom": "Salle A102", "capacite": 16, "equipements": ["projecteur"]},
        {"id": "INFO1", "nom": "Labo informatique", "capacite": 20, "equipements": ["pc", "projecteur"]},
        {"id": "SOINS", "nom": "Salle de soins", "capacite": 14, "equipements": ["lit_medicalise"],
         "indisponibilites": [{"date_debut": "2027-03-01", "date_fin": "2027-03-05", "motif": "Maintenance"}]},
    ],
    "ressources": [{"id": "mannequin", "nom": "Mannequin de simulation", "quantite": 1}],
    "enseignants": [
        {"id": "DUPONT", "nom": "M. Dupont", "disponibilites": ["lun:*", "mar:*", "mer:*", "jeu:*"]},
        {"id": "LAMBERT", "nom": "S. Lambert", "max_periodes_semaine": 16,
         "disponibilites": ["lun:matin", "lun:aprem", "mar:matin", "mar:aprem", "mer:matin", "jeu:matin", "jeu:aprem", "ven:matin"],
         "indisponibilites": [{"date_debut": "2027-04-12", "date_fin": "2027-04-16", "motif": "Formation continue"}]},
        {"id": "MERTENS", "nom": "A. Mertens", "disponibilites": ["mer:aprem", "ven:*"]},
        {"id": "PEETERS", "nom": "J. Peeters", "disponibilites": ["lun:soir", "mar:soir", "mer:soir", "jeu:soir"]},
        {"id": "JANSSENS", "nom": "L. Janssens", "disponibilites": ["mar:matin", "jeu:matin", "lun:soir", "mer:soir", "jeu:soir"]},
        {"id": "CLAES", "nom": "N. Claes", "disponibilites": ["mar:matin", "jeu:matin", "mar:soir"], "max_periodes_semaine": 12},
    ],
    "modules": [
        {"id": "AS-FOND", "nom": "Fondements du métier d'aide-soignant", "periodes": 80, "enseignants": ["DUPONT"]},
        {"id": "AS-COMM", "nom": "Communication professionnelle", "periodes": 40, "enseignants": ["DUPONT"],
         "max_seances_semaine": 2},
        {"id": "AS-HYG", "nom": "Hygiène et ergonomie", "periodes": 60, "prerequis": ["AS-FOND"],
         "equipements": ["lit_medicalise"], "ressources": ["mannequin"], "enseignants": ["LAMBERT"]},
        {"id": "AS-SOINS", "nom": "Soins de base", "periodes": 100, "prerequis": ["AS-HYG"],
         "equipements": ["lit_medicalise"], "ressources": ["mannequin"], "enseignants": ["LAMBERT", "MERTENS"]},
        {"id": "CPT-BASE", "nom": "Comptabilité générale", "periodes": 80, "enseignants": ["PEETERS"]},
        {"id": "CPT-TVA", "nom": "TVA", "periodes": 60, "prerequis": ["CPT-BASE"], "enseignants": ["PEETERS"]},
        {"id": "CPT-FISC", "nom": "Fiscalité des entreprises", "periodes": 40, "prerequis": ["CPT-TVA"], "enseignants": ["PEETERS"]},
        {"id": "CPT-INFO", "nom": "Logiciel comptable", "periodes": 60, "equipements": ["pc"],
         "enseignants": ["JANSSENS", "CLAES"], "max_seances_semaine": 1},
        {"id": "INF-WIN", "nom": "Environnement Windows", "periodes": 40, "equipements": ["pc"], "enseignants": ["JANSSENS"]},
        {"id": "INF-WORD", "nom": "Traitement de texte", "periodes": 40, "prerequis": ["INF-WIN"], "equipements": ["pc"],
         "enseignants": ["CLAES", "JANSSENS"]},
        {"id": "INF-EXCEL", "nom": "Tableur", "periodes": 60, "prerequis": ["INF-WIN"], "equipements": ["pc"],
         "enseignants": ["CLAES", "JANSSENS"]},
    ],
    "groupes": [
        {"id": "AS1", "nom": "Aide-soignant·e (jour)", "effectif": 12, "creneaux": ["matin", "aprem"],
         "modules": ["AS-FOND", "AS-COMM", "AS-HYG", "AS-SOINS"],
         "stages": [
             {"date_debut": "2027-03-15", "date_fin": "2027-03-26", "motif": "Stage d'observation"},
             {"date_debut": "2027-05-31", "date_fin": "2027-06-11", "motif": "Stage d'intégration"},
         ]},
        {"id": "CPT", "nom": "Comptabilité (soir)", "effectif": 18, "creneaux": ["soir"], "jours_cours": ["lun", "mar", "mer", "jeu"],
         "modules": ["CPT-BASE", "CPT-TVA", "CPT-FISC", "CPT-INFO"]},
        {"id": "INF", "nom": "Bureautique (matin)", "effectif": 12, "creneaux": ["matin"], "jours_cours": ["mar", "jeu"],
         "modules": ["INF-WIN", "INF-WORD", "INF-EXCEL"]},
    ],
}


def jeu_exemple() -> JeuDeDonnees:
    return JeuDeDonnees.model_validate(EXEMPLE)
