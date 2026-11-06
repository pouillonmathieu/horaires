"""Jeu de données d'exemple (fictif) : sert de modèle Excel téléchargeable et de base de tests.

Il illustre : modules à plusieurs cours, cours répartis entre enseignants, co-enseignement,
attributions propres à un groupe, stages, congés, matériel limité et local attitré.
"""
from .modeles import JeuDeDonnees


def _c(id, nom, periodes, attributions, **kw):
    return {"id": id, "nom": nom, "periodes": periodes, "attributions": attributions, **kw}


def _a(enseignants, periodes, groupes=None):
    return {"enseignants": enseignants if isinstance(enseignants, list) else [enseignants], "periodes": periodes,
            "groupes": groupes or []}


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
         "indisponibilites": [{"date_debut": "2027-06-14", "date_fin": "2027-06-25", "motif": "Travaux"}]},
    ],
    "ressources": [{"id": "mannequin", "nom": "Mannequin de simulation", "quantite": 1}],
    "enseignants": [
        {"id": "DUPONT", "nom": "M. Dupont", "disponibilites": ["lun:*", "mar:*", "mer:*", "jeu:*"]},
        {"id": "LAMBERT", "nom": "S. Lambert", "max_periodes_semaine": 16,
         "disponibilites": ["lun:matin", "lun:aprem", "mar:matin", "mar:aprem", "mer:matin", "jeu:matin", "jeu:aprem", "ven:matin"],
         "indisponibilites": [{"date_debut": "2027-04-12", "date_fin": "2027-04-16", "motif": "Formation continue"}]},
        {"id": "MERTENS", "nom": "A. Mertens", "disponibilites": ["mar:aprem", "mer:aprem", "ven:*"]},
        {"id": "PEETERS", "nom": "J. Peeters", "disponibilites": ["lun:soir", "mar:soir", "mer:soir", "jeu:soir"]},
        {"id": "VERMEULEN", "nom": "C. Vermeulen", "disponibilites": ["mar:soir", "jeu:soir"]},
        {"id": "JANSSENS", "nom": "L. Janssens", "disponibilites": ["mar:matin", "jeu:matin", "lun:soir", "mer:soir", "jeu:soir"]},
        {"id": "CLAES", "nom": "N. Claes", "disponibilites": ["mar:matin", "jeu:matin", "mar:soir"], "max_periodes_semaine": 12},
    ],
    "modules": [
        {"id": "AS-FOND", "nom": "Fondements du métier d'aide-soignant", "cours": [
            _c("AS-FOND-TH", "Fondements – théorie", 60, [_a("DUPONT", 60)]),
            _c("AS-FOND-DEO", "Déontologie", 20, [_a("MERTENS", 20)]),
        ]},
        {"id": "AS-COMM", "nom": "Communication professionnelle", "cours": [
            _c("AS-COMM-1", "Communication", 40, [_a("DUPONT", 40)], max_seances_semaine=1),
        ]},
        {"id": "AS-HYG", "nom": "Hygiène et ergonomie", "prerequis": ["AS-FOND"], "cours": [
            _c("AS-HYG-TH", "Hygiène – théorie", 20, [_a("LAMBERT", 20)]),
            # co-enseignement : les deux enseignantes encadrent ensemble la pratique
            _c("AS-HYG-PR", "Ergonomie – pratique", 40, [_a(["LAMBERT", "MERTENS"], 40)],
               equipements=["lit_medicalise"], ressources=["mannequin"]),
        ]},
        {"id": "AS-SOINS", "nom": "Soins de base", "prerequis": ["AS-HYG"], "cours": [
            # cours réparti : 60 périodes pour l'une, 40 pour l'autre
            _c("AS-SOINS-PR", "Soins de base – pratique", 100, [_a("LAMBERT", 60), _a("MERTENS", 40)],
               equipements=["lit_medicalise"], ressources=["mannequin"]),
        ]},
        {"id": "CPT-BASE", "nom": "Comptabilité générale", "cours": [
            # attributions différentes selon le groupe
            _c("CPT-BASE-TH", "Comptabilité – théorie", 60, [_a("PEETERS", 60, ["CPT"]), _a("VERMEULEN", 60, ["CPT2"])]),
            _c("CPT-BASE-EX", "Comptabilité – exercices", 20, [_a("PEETERS", 20, ["CPT"]), _a("VERMEULEN", 20, ["CPT2"])]),
        ]},
        {"id": "CPT-TVA", "nom": "TVA", "prerequis": ["CPT-BASE"], "cours": [
            _c("CPT-TVA-1", "TVA", 60, [_a("PEETERS", 60)]),
        ]},
        {"id": "CPT-FISC", "nom": "Fiscalité des entreprises", "prerequis": ["CPT-TVA"], "cours": [
            _c("CPT-FISC-1", "Fiscalité", 40, [_a("PEETERS", 40)]),
        ]},
        {"id": "CPT-INFO", "nom": "Logiciel comptable", "cours": [
            _c("CPT-INFO-1", "Logiciel comptable", 60, [_a("JANSSENS", 60)], equipements=["pc"], max_seances_semaine=1),
        ]},
        {"id": "INF-WIN", "nom": "Environnement Windows", "cours": [
            _c("INF-WIN-1", "Windows", 40, [_a("JANSSENS", 40)], equipements=["pc"]),
        ]},
        {"id": "INF-WORD", "nom": "Traitement de texte", "prerequis": ["INF-WIN"], "cours": [
            _c("INF-WORD-1", "Traitement de texte", 40, [_a("CLAES", 40)], equipements=["pc"]),
        ]},
        {"id": "INF-EXCEL", "nom": "Tableur", "prerequis": ["INF-WIN"], "cours": [
            _c("INF-EXCEL-1", "Tableur", 60, [_a("CLAES", 40), _a("JANSSENS", 20)], equipements=["pc"]),
        ]},
    ],
    "groupes": [
        {"id": "AS1", "nom": "Aide-soignant·e (jour)", "effectif": 12, "creneaux": ["matin", "aprem"], "local_prefere": "A102",
         "modules": ["AS-FOND", "AS-COMM", "AS-HYG", "AS-SOINS"],
         "stages": [
             {"date_debut": "2027-03-15", "date_fin": "2027-03-26", "motif": "Stage d'observation"},
             {"date_debut": "2027-05-31", "date_fin": "2027-06-11", "motif": "Stage d'intégration"},
         ]},
        {"id": "CPT", "nom": "Comptabilité (soir)", "effectif": 18, "creneaux": ["soir"], "jours_cours": ["lun", "mar", "mer", "jeu"],
         "local_prefere": "A101", "modules": ["CPT-BASE", "CPT-TVA", "CPT-FISC", "CPT-INFO"]},
        {"id": "CPT2", "nom": "Comptabilité (soir, 2e groupe)", "effectif": 15, "creneaux": ["soir"], "jours_cours": ["mar", "jeu"],
         "modules": ["CPT-BASE"]},
        {"id": "INF", "nom": "Bureautique (matin)", "effectif": 12, "creneaux": ["matin"], "jours_cours": ["mar", "jeu"],
         "modules": ["INF-WIN", "INF-WORD", "INF-EXCEL"]},
    ],
}


def jeu_exemple() -> JeuDeDonnees:
    return JeuDeDonnees.model_validate(EXEMPLE)
