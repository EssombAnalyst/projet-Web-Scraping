"""
etape1_diagnostic.py
Projet de groupe : vente et réparation de vélos en Outaouais.
Étape 1 : diagnostic des pages de départ, avant d'écrire les extracteurs.

Pour chaque page :
  1. diagnostiquer() affiche le rapport (cas détectés, recommandation) ;
  2. une copie du HTML est enregistrée dans resultats/pages_html/ ;
  3. une ligne est ajoutée à resultats/journal_tests.csv.
Les sites refusés sont aussi notés au journal, sans aucune requête.
Tous les rapports sont enregistrés dans resultats/diagnostics.json.

Avant de lancer : mettre votre courriel dans USER_AGENT (projet_scraping.py, ligne 45).
Lancer (dans le dossier qui contient projet_scraping.py et scraper.py) :
    python etape1_diagnostic.py
Durée : environ 2 minutes, à cause des pauses de 10 secondes.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from urllib.robotparser import RobotFileParser

import projet_scraping as ws
import scraper  # pour ajouter_au_journal() et nom_fichier()

DOSSIER = Path("resultats")
DOSSIER_HTML = DOSSIER / "pages_html"

# (entreprise, page de départ, pause en secondes)
# 10 s : Crawl-delay demandé dans robots.txt (Vélozophie, Primeau Vélo)
# 1 s  : règle du cours pour les autres sites
PAGES_A_DIAGNOSTIQUER = [
    ("Vélozophie", "https://www.velozophie.ca/store/Velos-c164664755", 10),
    ("Vélozophie", "https://www.velozophie.ca/entretiens-complets/", 10),
    ("Primeau Vélo", "https://www.primeauvelo.com/fr_ca/velos", 10),
    ("Primeau Vélo", "https://www.primeauvelo.com/fr_ca/velos/velos-de-montagne", 10),
    (
        "Expéditions Wakefield",
        "https://expeditionswakefield.com/product-category/bikes/",
        1,
    ),
    ("Expéditions Wakefield", "https://expeditionswakefield.com/service/", 1),
    ("Expéditions Wakefield", "https://expeditionswakefield.com/rentals/", 1),
    ("SAP Outaouais", "https://sapoutaouais.com/fr/sports/velo/", 1),
    ("SAP Outaouais", "https://sapoutaouais.com/fr/atelier/", 1),
]

# Sites refusés : une ligne au journal, aucune requête envoyée.
# Ajoutez les autres sites de votre liste (Cycle Outaouais, Scooteretti...).
SITES_REFUSES = [
    (
        "Polo-Vélo",
        "https://polovelo.ca",
        "conditions d'utilisation, article 12 : spider, crawl et scraping interdits",
    ),
    (
        "Vélo la Shop",
        "https://www.velolashop.com",
        "page d'attente (une image), aucune donnée",
    ),
    ("Cycle Bertrand", "https://www.cyclebertrand.ca", "interdit par robots.txt"),
    ("Rack à Bécik", "https://www.rackabecik.com", "interdit par robots.txt"),
    ("Quantum eBikes", "https://quantumebikes.com", "interdit par robots.txt"),
    ("Pace E-bike", "https://pacebike.info", "interdit par robots.txt"),
    ("Centre Vorlage", "https://skivorlage.com", "interdit par robots.txt"),
]


def verifier_python():
    """Test du cours (Avant de scraper, section 3.6) : robotparser suit-il la RFC 9309 ?"""
    test = RobotFileParser()
    test.parse(["User-agent: *", "Disallow: /docs/", "Allow: /docs/public/"])
    if not test.can_fetch("*", "https://www.exemple.ca/docs/public/guide.html"):
        raise SystemExit(
            "Python trop ancien : il lit mal les règles avec * de Primeau Vélo. "
            "Installez Python 3.13 ou 3.14 à jour, puis relancez."
        )


def ligne_journal(url, statut, robots, cas, recommandation, fichiers, remarques):
    """Une ligne du journal, avec les mêmes colonnes que scraper.py."""
    return {
        "date": datetime.now().isoformat(timespec="seconds"),
        "url": url,
        "statut": statut,
        "robots": robots,
        "cas": cas,
        "recommandation": recommandation,
        "nb_lignes": 0,
        "fichiers": fichiers,
        "remarques": remarques,
    }


def main():
    verifier_python()
    if "exemple.ca" in ws.USER_AGENT:
        raise SystemExit(
            "Mettez votre courriel dans USER_AGENT (projet_scraping.py, ligne 45)."
        )

    DOSSIER_HTML.mkdir(parents=True, exist_ok=True)
    session = ws.creer_session(ws.USER_AGENT)

    # 1. Sites refusés : on note la décision, sans contacter le site
    for entreprise, url, raison in SITES_REFUSES:
        ligne = ligne_journal(
            url, "", "", "STOP", "Ne pas collecter", "", f"{entreprise} : {raison}"
        )
        scraper.ajouter_au_journal(ligne, DOSSIER)

    # 2. Pages à diagnostiquer
    rapports = []
    for entreprise, url, pause in PAGES_A_DIAGNOSTIQUER:
        d = ws.diagnostiquer(url, session)  # une requête, rapport affiché
        d["entreprise"] = entreprise
        rapports.append(d)
        time.sleep(pause)  # diagnostiquer() ne fait pas de pause

        fichier = ""
        remarque = f"{entreprise} : diagnostic seulement"
        if d.get("statut") == 200 and d.get("robots_autorise"):
            try:
                # Copie locale : on mettra au point les extracteurs sur ce fichier
                r = ws.obtenir(session, url, delai=pause)
            except (ws.AccesBloque, RuntimeError) as e:
                r = None
                remarque += f" ; copie impossible : {e}"
            if r is not None:
                chemin = DOSSIER_HTML / (scraper.nom_fichier(url) + ".html")
                chemin.write_text(r.text, encoding="utf-8")
                fichier = str(chemin)

        ligne = ligne_journal(
            url,
            d.get("statut"),
            d.get("robots_raison"),
            " | ".join(d.get("cas", [])),
            d.get("recommandation"),
            fichier,
            remarque,
        )
        scraper.ajouter_au_journal(ligne, DOSSIER)

    chemin_json = DOSSIER / "diagnostics.json"
    chemin_json.write_text(
        json.dumps(rapports, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("Journal :", DOSSIER / "journal_tests.csv")
    print("Rapports :", chemin_json)
    print("Copies HTML :", DOSSIER_HTML)


if __name__ == "__main__":
    main()
