"""
scraper.py — Pipeline unifié : diagnostic -> choix du cas -> extraction -> CSV + journal

Usage :
    python scraper.py URL [URL ...] [--sortie resultats] [--pages 3] [--navigateur]

Pour chaque URL :
  1. diagnostiquer(url)                      (rapport affiché)
  2. extraction automatique selon le cas détecté :
       STOP / G   -> rien n'est collecté
       E          -> JSON intégré, sinon rendu Playwright si --navigateur
       B          -> chaque tableau dans un CSV
       C / A / D  -> blocs répétés, en suivant la pagination (max --pages)
  3. une ligne ajoutée à resultats/journal_tests.csv
"""

# --- Importations -------------------------------------------------------------
# argparse : lit les arguments donnés dans le terminal (URL, --pages, etc.)
# csv : écrit les fichiers CSV
# json : sauvegarde les données JSON intégrées trouvées dans une page
# re : expressions régulières, utilisées pour nettoyer les noms de fichiers
# datetime : ajoute la date et l'heure dans le journal
# Path : manipule facilement les chemins de fichiers et dossiers
# urlparse : sépare une URL en domaine, chemin, etc.
import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# ws = ton module principal (projet_scraping.py)
# Il contient les fonctions de diagnostic, requêtes HTTP, extraction, etc.
import projet_scraping as ws


def nom_fichier(url):
    """
    Transforme une URL en nom de fichier sûr.

    Exemple :
        https://exemple.com/actualites/page-2
        -> exemple.com_actualites_page-2

    On retire les caractères interdits dans les noms de fichiers
    (/, :, ?, &, etc.) et on limite la longueur à 80 caractères.
    """
    p = urlparse(url)

    # netloc = domaine (exemple.com), path = chemin après le domaine
    brut = (p.netloc + p.path).strip("/") or "page"

    # Remplace tout caractère non alphanumérique, point ou tiret par "_"
    return re.sub(r"[^\w.-]+", "_", brut)[:80]


def traiter(url, session, dossier, max_pages=3, navigateur=False):
    """
    Traite une URL complète :
      1. Diagnostic du site
      2. Choix de la méthode d'extraction selon le cas détecté
      3. Sauvegarde des résultats dans un fichier CSV ou JSON
      4. Retourne une ligne de journal décrivant ce qui a été fait
    """

    # 1. Diagnostic automatique du site
    d = ws.diagnostiquer(url, session)

    # Ligne qui sera ajoutée au journal_tests.csv
    ligne = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "url": url,
        "statut": d.get("statut"),
        "robots": d.get("robots_raison"),
        "cas": " | ".join(d.get("cas", [])),
        "recommandation": d.get("recommandation"),
        "nb_lignes": 0,
        "fichiers": "",
        "remarques": "",
    }

    # On prend le premier cas détecté comme cas principal
    # Exemple : ["C", "A"] -> on traite "C"
    principal = (d.get("cas") or ["?"])[0]

    # Base du nom de fichier, sans extension
    # Exemple : resultats/exemple_com_actualites
    base = dossier / nom_fichier(url)

    # Liste des fichiers créés pour cette URL
    fichiers = []

    try:
        # ------------------------------------------------------------
        # CAS STOP ou G : on ne collecte rien
        # (robots.txt interdit, page inaccessible, etc.)
        # ------------------------------------------------------------
        if principal.startswith(("STOP", "G")) or d.get("statut") != 200:
            ligne["remarques"] = "aucune collecte (arrêt volontaire)"

        # ------------------------------------------------------------
        # CAS E : données intégrées dans la page (JSON dans le HTML)
        # ------------------------------------------------------------
        elif principal.startswith("E"):
            # On récupère le HTML brut de la page
            r = session.get(url, timeout=ws.TIMEOUT)

            # On cherche du JSON intégré dans le HTML
            donnees = ws.extraire_json_integre(r.text)

            if donnees:
                # Si du JSON a été trouvé, on le sauvegarde dans un fichier .json
                chemin = Path(f"{base}.json")
                chemin.write_text(
                    json.dumps(donnees, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                fichiers.append(chemin)

                # Compte le nombre d'éléments :
                # - si c'est une liste, on compte sa longueur
                # - sinon, on compte 1
                ligne["nb_lignes"] = sum(
                    len(x) if isinstance(x, list) else 1 for x in donnees
                )
                ligne["remarques"] = f"{len(donnees)} bloc(s) JSON intégrés trouvés"

            elif navigateur:
                # Pas de JSON trouvé, mais l'utilisateur a autorisé Playwright
                # -> on rend la page avec un vrai navigateur (JavaScript exécuté)
                html = ws.rendre_page(url)
                sp = ws.soupe(html)

                # On cherche des blocs répétés dans la page rendue
                blocs = ws.detecter_blocs_repetes(sp)

                if blocs:
                    # On extrait les blocs trouvés
                    lignes = ws.extraire_blocs_generiques(sp, url, blocs["selecteur"])

                    # Sauvegarde en CSV
                    fichiers.append(ws.sauvegarder_csv(lignes, f"{base}.csv"))
                    ligne["nb_lignes"] = len(lignes)

                ligne["remarques"] = "rendu avec Playwright"

            else:
                # Ni JSON ni Playwright : on ne peut pas extraire automatiquement
                ligne["remarques"] = (
                    "pas de JSON intégré : inspecter l'onglet Réseau "
                    "ou relancer avec --navigateur"
                )

        # ------------------------------------------------------------
        # CAS B : la page contient des tableaux HTML (<table>)
        # ------------------------------------------------------------
        elif principal.startswith("B"):
            # Extraire tous les tableaux de la page
            tableaux = ws.extraire_tableaux(session, url)

            # Chaque tableau devient un CSV séparé
            for i, df in enumerate(tableaux, 1):
                if len(df) >= 2:  # on ignore les tableaux trop petits
                    fichiers.append(
                        ws.sauvegarder_csv(
                            ws.nettoyer_tableau(df), f"{base}_tableau{i}.csv"
                        )
                    )
                    ligne["nb_lignes"] += len(df)

            ligne["remarques"] = f"{len(tableaux)} tableau(x) lus"

        # ------------------------------------------------------------
        # CAS F : page de connexion
        # ------------------------------------------------------------
        elif principal.startswith("F"):
            ligne["remarques"] = (
                "page de connexion : utiliser se_connecter() avec votre propre compte, "
                "puis collecter"
            )

        # ------------------------------------------------------------
        # CAS A, C, D, H : blocs répétés + pagination
        # Exemple : liste d'articles, liste de produits, annonces, etc.
        # ------------------------------------------------------------
        else:
            # On récupère la première page
            r = ws.obtenir(session, url)

            # On cherche les blocs répétés (ex : chaque carte d'article)
            blocs = ws.detecter_blocs_repetes(ws.soupe(r)) if r is not None else None

            if not blocs:
                ligne["remarques"] = (
                    "aucun bloc répété : écrire un extracteur sur mesure (cas A)"
                )
            else:
                # Sélecteur CSS des blocs répétés
                sel = blocs["selecteur"]

                # Fonction d'extraction réutilisable pour chaque page
                extraire = lambda sp, u: ws.extraire_blocs_generiques(sp, u, sel)

                # On parcourt plusieurs pages (pagination)
                lignes = ws.parcourir_pages(
                    session, url, extraire, max_pages=max_pages
                )

                # Sauvegarde de toutes les lignes dans un seul CSV
                fichiers.append(ws.sauvegarder_csv(lignes, f"{base}.csv"))
                ligne["nb_lignes"] = len(lignes)
                ligne["remarques"] = f"blocs « {sel} »"

    # ------------------------------------------------------------
    # Gestion des erreurs
    # ------------------------------------------------------------
    except ws.AccesBloque as e:
        # Le site a bloqué l'accès (403, captcha, etc.)
        ligne["remarques"] = f"accès bloqué pendant la collecte : {e}"

    except Exception as e:
        # Toute autre erreur : on la note et on passe au site suivant
        ligne["remarques"] = f"ERREUR {type(e).__name__} : {e}"

    # On enregistre la liste des fichiers créés dans le journal
    ligne["fichiers"] = ";".join(str(f) for f in fichiers)

    return ligne


def ajouter_au_journal(ligne, dossier):
    """
    Ajoute une ligne au fichier journal_tests.csv.

    Si le fichier n'existe pas encore, on écrit d'abord l'en-tête
    (les noms de colonnes).
    """
    journal = dossier / "journal_tests.csv"

    # Vérifie si le fichier existe déjà
    nouveau = not journal.exists()

    # "a" = mode append (ajouter à la fin, sans écraser)
    # utf-8-sig = encodage compatible avec Excel
    with journal.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(ligne))

        # Si c'est un nouveau fichier, on écrit les en-têtes
        if nouveau:
            w.writeheader()

        # On écrit la ligne
        w.writerow(ligne)

    return journal


def main():
    """
    Point d'entrée du script.
    Lit les arguments du terminal, puis traite chaque URL une par une.
    """
    ap = argparse.ArgumentParser(description="Diagnostic et collecte automatique")

    # Une ou plusieurs URLs obligatoires
    ap.add_argument("urls", nargs="+")

    # Dossier de sortie (défaut : resultats)
    ap.add_argument("--sortie", default="resultats")

    # Nombre maximum de pages à suivre pour la pagination
    ap.add_argument(
        "--pages", type=int, default=3, help="pages maximum à suivre (défaut : 3)"
    )

    # Option pour autoriser Playwright (navigateur headless)
    ap.add_argument(
        "--navigateur", action="store_true", help="autoriser Playwright pour le cas E"
    )

    args = ap.parse_args()

    # Création du dossier de sortie s'il n'existe pas
    dossier = Path(args.sortie)
    dossier.mkdir(parents=True, exist_ok=True)

    # Création de la session HTTP (headers, cookies, etc.)
    session = ws.creer_session()

    # Traitement de chaque URL
    for url in args.urls:
        ligne = traiter(url, session, dossier, args.pages, args.navigateur)

        # Ajout au journal
        journal = ajouter_au_journal(ligne, dossier)

        # Affichage du résultat dans le terminal
        print(f"  → {ligne['nb_lignes']} lignes | {ligne['remarques']}\n")

    # Affichage du chemin du journal à la fin
    print(f"Journal : {journal}")


# Ce bloc ne s'exécute que si on lance le script directement :
# python scraper.py https://exemple.com
if __name__ == "__main__":
    main()