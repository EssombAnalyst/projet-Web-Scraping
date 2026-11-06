"""
etape2_collecte.py
Projet de groupe : vente et réparation de vélos en Outaouais.
Étape 2 : collecte des vélos et des tarifs d'atelier sur 4 sites.

Fichiers produits dans resultats/ :
  velos_outaouais.csv    un vélo par ligne
  atelier_outaouais.csv  un service d'atelier ou un tarif de location par ligne
  journal_tests.csv      une ligne de plus par source collectée

Principe général (le même pour chaque site) :
    1. télécharger la page avec ws.obtenir() (robots.txt + délai + reprises) ;
    2. la transformer en « soupe » BeautifulSoup avec ws.soupe() ;
    3. repérer le bloc HTML qui se répète (une « carte » par produit) ;
    4. lire dans chaque carte les champs utiles avec des sélecteurs CSS ;
    5. produire une liste de dictionnaires (une ligne par produit) ;
    6. à la fin, tout regrouper dans un DataFrame pandas, nettoyer, sauvegarder.

Rappel des cas du cours (voir projet_scraping.py) :
    A : HTML statique          B : tableaux HTML
    C : pagination             H : encodage et nettoyage du texte

Lancer (dans le dossier du projet, environnement scraping) :
    python etape2_collecte.py
Durée : environ 10 minutes, à cause du Crawl-delay de 10 s
de Vélozophie et de Primeau Vélo.
"""

# ============================================================================
# 1. IMPORTATION DES MODULES
# ============================================================================

# Expressions régulières : chercher des motifs dans du texte
# (un nombre, un tiret, un nom de marque...).
import re

# Date du jour inscrite dans chaque ligne des CSV et dans le journal.
from datetime import datetime

# Construction des chemins de fichiers, indépendante du système (Windows, Linux).
from pathlib import Path

# urljoin : transforme un lien relatif (« /produit/12 ») en adresse complète.
# urlparse : découpe une adresse ; on s'en sert pour lire le nom de domaine.
from urllib.parse import urljoin, urlparse

# Lecteur de fichiers robots.txt fourni avec Python.
from urllib.robotparser import RobotFileParser

# pandas : tableaux de données (DataFrame), nettoyage et export CSV.
import pandas as pd

# Boîte à outils du cours. On l'abrège « ws » (web scraping).
# Fonctions utilisées ici : creer_session(), obtenir(), soupe(), texte(),
# prix_en_nombre(), nettoyer_chaine(), parcourir_pages(), extraire_tableaux(),
# robots_autorise() et sauvegarder_csv().
import projet_scraping as ws

# Script de l'étape précédente : on réutilise sa fonction ajouter_au_journal()
# pour que toutes les étapes écrivent dans le même journal, au même format.
import scraper  # pour ajouter_au_journal()


# ============================================================================
# 2. CONFIGURATION : FICHIERS, COLONNES, MARQUES, DÉLAIS
# ============================================================================

# Dossier où sont écrits tous les résultats (chemin relatif : il faut lancer
# le script depuis le dossier du projet).
DOSSIER = Path("resultats")
# L'opérateur « / » de pathlib colle un nom de fichier au dossier.
FICHIER_VELOS = DOSSIER / "velos_outaouais.csv"
FICHIER_ATELIER = DOSSIER / "atelier_outaouais.csv"
# Date au format AAAA-MM-JJ, par exemple « 2026-11-06 ».
AUJOURDHUI = datetime.now().strftime("%Y-%m-%d")

# Ordre des colonnes des deux fichiers CSV.
# Astuce : écrire les noms dans une seule chaîne puis la découper avec split()
# évite de taper une longue liste de guillemets et de virgules.
COLONNES_VELOS = (
    "entreprise categorie marque nom prix prix_regulier en_solde "
    "etat disponibilite remarque url date_collecte"
).split()
COLONNES_ATELIER = (
    "entreprise type service duree prix_min prix_max prix_texte "
    "description url date_collecte"
).split()

# Marques cherchées dans le nom, écrites d'une seule façon pour tous les sites.
# Ordre : marques en plusieurs mots d'abord, mots courts (EVO, Neo...) à la fin,
# car « Stumpjumper EVO » est un Specialized.
# (trouver_marque() s'arrête à la PREMIÈRE marque trouvée : l'ordre compte.)
MARQUES = (
    "Santa Cruz, Rocky Mountain, Urban Arrow, We The People, Specialized, "
    "Cannondale, Mondraker, Cervelo, Devinci, Giant, Liv, Trek, Norco, Kona, "
    "Salsa, Ibis, Amflow, Cube, Maui, Marin, Opus, Scott, Orbea, Pivot, Garneau, "
    "Sombrio, Haro, Dabomb, Surly, Gazelle, Benno, Husqvarna, GasGas, Electra, "
    "Shotgun, Reid, Minelli, Genesis, KHS, Felt, Radio, EVO, Neo"
).split(", ")

# Crawl-delay lu à la main dans robots.txt. Chez Vélozophie, la ligne
# « Crawl-delay: 10 » est avant « User-agent: * » : robotparser ne la voit pas.
# Dictionnaire : clé = nom de domaine, valeur = pause minimale en secondes.
DELAI_MINIMUM = {"www.velozophie.ca": 10, "www.primeauvelo.com": 10}


# ============================================================================
# 3. OUTILS COMMUNS (utilisés par les quatre sites)
# ============================================================================


def verifier_python():
    """Test du cours (Avant de scraper, 3.6) : robotparser suit-il la RFC 9309 ?"""
    # On fabrique un petit robots.txt de test, sans aucune requête réseau.
    test = RobotFileParser()
    test.parse(["User-agent: *", "Disallow: /docs/", "Allow: /docs/public/"])
    # Selon la norme, la règle la plus précise (Allow /docs/public/) l'emporte.
    # Les vieilles versions de Python répondent « interdit » : on arrête tout,
    # car elles pourraient aussi mal lire les vrais robots.txt.
    if not test.can_fetch("*", "https://www.exemple.ca/docs/public/guide.html"):
        raise SystemExit("Python trop ancien : utilisez l'environnement scraping.")


def delai_du_site(session, url):
    """Pause entre deux requêtes : le plus grand entre le Crawl-delay lu par
    robotparser, celui de DELAI_MINIMUM et 1 s (règle du cours)."""
    # Récupère l'User-Agent configuré dans la session
    agent = session.headers["User-Agent"]
    # Vérifie les permissions dans robots.txt et récupère le crawl-delay officiel.
    # La fonction renvoie trois valeurs ; « _ » signifie « valeur ignorée ».
    _, _, crawl_delay = ws.robots_autorise(url, agent, session)
    # Cherche si on a un délai minimum forcé pour ce domaine (ex: 10s).
    # .get(clé, défaut) renvoie 1.0 si le domaine n'est pas dans le dictionnaire.
    minimum = DELAI_MINIMUM.get(urlparse(url).netloc, 1.0)
    # On prend la valeur la plus restrictive.
    # « crawl_delay or 0 » remplace None (pas de Crawl-delay) par 0.
    return max(1.0, minimum, crawl_delay or 0)


def nombre_annonce(texte):
    """« 1 - 60 sur 366 » -> 366   « Résultats 1 - 12 de 365 » -> 365"""
    # (?:sur|de|of) : un des trois mots, sans le capturer.
    # \s+ : un ou plusieurs espaces.  (\d+) : le nombre, capturé dans group(1).
    # « texte or "" » évite une erreur si texte vaut None.
    m = re.search(r"(?:sur|de|of)\s+(\d+)", texte or "")
    # Expression conditionnelle : le nombre si on l'a trouvé, sinon None.
    return int(m.group(1)) if m else None


def prix_min_max(texte):
    """« $260.00- $330.00 » -> (260.0, 330.0)   « 59,99 $ » -> (59.99, 59.99)"""
    # Sépare le texte s'il y a un tiret ou le mot "à" (fourchette de prix)
    morceaux = re.split(r"\s*-\s*|\s+à\s+", texte or "")
    # Convertit chaque morceau en nombre flottant (liste en compréhension)
    nombres = [ws.prix_en_nombre(m) for m in morceaux]
    # Filtre les valeurs None (e.g. si un texte n'était pas un prix)
    nombres = [n for n in nombres if n is not None]
    if not nombres:
        return None, None  # par exemple « Prix à confirmer »
    # Retourne la borne basse et la borne haute.
    # Un prix unique donne deux fois le même nombre.
    return min(nombres), max(nombres)


def trouver_marque(texte, marque_du_site=""):
    """Marque de la liste MARQUES trouvée dans le texte ; sinon celle du site."""
    for marque in MARQUES:
        # \b = frontière de mot : « Liv » ne doit pas être trouvé dans « Olivier ».
        # re.escape() protège les caractères spéciaux éventuels du nom.
        # re.IGNORECASE : « TREK », « Trek » et « trek » sont équivalents.
        if re.search(r"\b" + re.escape(marque) + r"\b", texte, re.IGNORECASE):
            return marque  # première marque trouvée : on s'arrête là
    # Aucune marque connue : on garde ce que le site indiquait (peut-être vide).
    return marque_du_site


def etat_du_velo(texte):
    """« occasion » ou « démo » quand le site l'indique ; vide sinon."""
    # Tout en minuscules pour comparer sans se soucier des majuscules.
    texte = texte.lower()
    if "occasion" in texte or "usagé" in texte:
        return "occasion"
    # On teste aussi « demo » sans accent : certains sites l'écrivent ainsi.
    if "démo" in texte or "demo" in texte:
        return "démo"
    return ""  # vélo neuf (ou état non précisé)


# Colonnes de texte à nettoyer (espaces en trop, caractères invisibles...).
TEXTES = "entreprise categorie marque nom etat disponibilite remarque url".split()


def nettoyer_velos(df):
    """Nettoyage des vélos (cas H et suivants). Ne contacte aucun site :
    etape3_nettoyage.py peut le refaire sur un CSV déjà collecté."""
    # On travaille sur une copie pour ne pas modifier le DataFrame reçu.
    df = df.copy()
    # Nettoyage des espaces et caractères spéciaux dans les colonnes textuelles.
    # fillna("") : cases vides -> chaîne vide ; astype(str) : tout en texte ;
    # map() applique nettoyer_chaine() à chaque case de la colonne.
    for colonne in TEXTES:
        df[colonne] = df[colonne].fillna("").astype(str).map(ws.nettoyer_chaine)

    # Supprime le tiret final souvent présent dans les noms
    df["nom"] = df["nom"].str.rstrip(" -")  # « Felt VR 60 - » -> « Felt VR 60 »

    # Enrichit la colonne marque en cherchant dans le nom si elle est vide.
    # zip() parcourt les deux colonnes en parallèle, ligne par ligne.
    # On cherche dans « marque + nom » : la marque du site sert de repli.
    df["marque"] = [
        trouver_marque(marque + " " + nom, marque)
        for marque, nom in zip(df["marque"], df["nom"])
    ]

    # Filtrage spécifique pour SAP : exclure les accessoires qui ne sont pas des vélos
    # SAP range parfois des roues dans la catégorie vélos.
    # On construit un « masque » : une colonne de True/False, une valeur par ligne.
    # & = ET logique, ~ = NON logique (opérateurs de pandas sur les masques).
    nom = df["nom"].str.lower()
    piece = (
        (df["entreprise"] == "SAP Outaouais")
        & nom.str.contains("roue")
        & ~nom.str.contains("vélo")
    )
    # On garde seulement les lignes où le masque vaut False (pas une pièce).
    df = df[~piece]

    # Supprime les doublons basés sur l'URL (même produit vu dans plusieurs catégories).
    # drop_duplicates garde la PREMIÈRE occurrence : c'est pourquoi les
    # catégories les plus précises sont parcourues en premier (voir Vélozophie).
    df = df.drop_duplicates(subset=["entreprise", "url"])

    # Conversion des prix en nombres pour permettre les calculs.
    # errors="coerce" : une valeur impossible à convertir devient NaN (vide)
    # au lieu de faire planter le programme.
    df["prix"] = pd.to_numeric(df["prix"], errors="coerce")
    df["prix_regulier"] = pd.to_numeric(df["prix_regulier"], errors="coerce")
    # Calcule si le produit est en promotion (True si le prix barré est plus élevé).
    # Une comparaison avec NaN donne False : pas de prix barré = pas en solde.
    df["en_solde"] = df["prix_regulier"] > df["prix"]

    # Trie par entreprise, puis catégorie, puis prix croissant,
    # et remet les colonnes dans l'ordre de COLONNES_VELOS.
    return df.sort_values(["entreprise", "categorie", "prix"])[COLONNES_VELOS]


# Première ligne attendue du journal : les noms de colonnes séparés par des virgules.
ENTETE_JOURNAL = (
    "date,url,statut,robots,cas,recommandation,nb_lignes,fichiers,remarques"
)


def reparer_entete_journal():
    """Remet la ligne d'en-tête si journal_tests.csv avait été créé vide à la main."""
    journal = DOSSIER / "journal_tests.csv"
    if journal.exists():
        # « utf-8-sig » : UTF-8 avec la marque (BOM) qu'Excel sait reconnaître.
        texte = journal.read_text(encoding="utf-8-sig")
        # Le fichier contient des lignes mais ne commence pas par l'en-tête :
        # on réécrit le fichier avec l'en-tête au début.
        if texte and not texte.startswith("date,"):
            journal.write_text(ENTETE_JOURNAL + "\n" + texte, encoding="utf-8-sig")
            print("Ligne d'en-tête ajoutée au début du journal.")


def noter(url, statut, cas, technique, nb_lignes, fichier, remarques):
    """Une ligne au journal, avec les mêmes colonnes que scraper.py."""
    # Un dictionnaire = une ligne du CSV (clé = nom de colonne).
    ligne = {
        # isoformat() donne par exemple « 2026-11-06T02:54:53 ».
        "date": datetime.now().isoformat(timespec="seconds"),
        "url": url,
        "statut": statut,
        # Un statut non vide (200) veut dire que la collecte a eu lieu,
        # donc que robots.txt l'autorisait (obtenir() le vérifie).
        "robots": "autorisé par robots.txt" if statut else "",
        "cas": cas,
        "recommandation": technique,
        "nb_lignes": nb_lignes,
        "fichiers": fichier,
        "remarques": remarques,
    }
    scraper.ajouter_au_journal(ligne, DOSSIER)


# ============================================================================
# 4. SITE 1 — VÉLOZOPHIE
#    Une page par catégorie (60 vélos au plus par page).
#    La pagination est en JavaScript (liens javascript:;) : on ne la suit pas.
# ============================================================================

# Adresse commune à toutes les pages de la boutique.
VELOZOPHIE = "https://www.velozophie.ca/store/"
# Catégories précises d'abord : un vélo vu deux fois garde la plus précise.
# Chaque tuple : (libellé écrit dans le CSV, fin de l'adresse de la catégorie).
# Comme la pagination est inaccessible, parcourir beaucoup de petites
# catégories permet de récupérer plus de 60 vélos au total.
CATEGORIES_VELOZOPHIE = [
    ("Montagne / Double suspension", "Double-Suspension-c164654323"),
    ("Montagne / Hardtail", "Hardtail-c164644821"),
    ("Gravel / Cyclocross", "Cyclocross-c164644812"),
    ("Gravel / Cyclotourisme", "Gravel-et-cyclotourisme-c164644816"),
    ("Route / Performance et endurance", "Performance-et-Endurance-c164644568"),
    ("Ville et hybride", "Ville-et-Hybride-c164644562"),
    ("Électrique / Montagne", "Montagne-electrique-c164654333"),
    ("Électrique / Route", "Route-electrique-c164643078"),
    ("Électrique / Hybride", "Hybride-electrique-c164644569"),
    ("Fat bike", "Fat-Bike-c164644798"),
    ("Enfants / Vélo-trotteur", "Velo-trotteur-c164664773"),
    ("Enfants / 12 po", "12po-c164664778"),
    ("Enfants / 14 po", "14po-c164652569"),
    ("Enfants / 16 po", "16po-c164654327"),
    ("Enfants / 20 po", "20po-c164643080"),
    ("Enfants / 24 po", "24po-c164644829"),
    ("Enfants / 26 po", "26po-c164654328"),
    ("Démo", "Velos-demos-c164644556"),
    ("Montagne", "Montagne-c164654293"),
    ("Gravel et cross", "Gravel-et-Cross-c164644803"),
    ("Route", "Route-c164644544"),
    ("Électrique", "Velos-electriques-c164654296"),
    ("Enfants", "Enfants-c164654294"),
    ("Vélos", "Velos-c164664755"),
]


def extraire_velozophie(sp, url_page):
    """Une ligne par carte div.grid-product__wrap."""
    # sp = la « soupe » BeautifulSoup de la page ; url_page = son adresse.
    # Toutes les fonctions extraire_...() ont cette même signature, ce qui
    # permet de les passer à ws.parcourir_pages().
    lignes = []
    # Parcourt chaque bloc de produit dans la grille.
    # select() renvoie TOUS les éléments qui correspondent au sélecteur CSS ;
    # select_one() renvoie seulement le premier (ou None).
    for carte in sp.select("div.grid-product__wrap"):
        lien = carte.select_one("a.grid-product__title")
        # ws.texte(carte, sélecteur) : texte de l'élément, ou "" s'il manque.
        nom = ws.texte(carte, ".grid-product__title-inner")
        # Récupère l'étiquette (ex: "Démo", "Nouveau", "Épuisé")
        etiquette = ws.texte(carte, ".grid-product__label")
        prix = ws.texte(carte, ".grid-product__price-value")
        # Prix barré s'il existe
        prix_barre = ws.texte(carte, ".grid-product__price-compare-values")

        lignes.append(
            {
                "entreprise": "Vélozophie",
                "marque": "",  # La marque sera extraite du nom plus tard
                "nom": nom,
                # prix_en_nombre() : « 1 299,99 $ » -> 1299.99
                "prix": ws.prix_en_nombre(prix),
                "prix_regulier": ws.prix_en_nombre(prix_barre),
                # Détecte si c'est un vélo de démo ou d'occasion via le nom ou l'étiquette
                "etat": etat_du_velo(nom + " " + etiquette),
                # Marque comme épuisé si l'étiquette contient "puisé"
                # (sans le « É » : on évite les problèmes de majuscule accentuée).
                "disponibilite": "épuisé" if "puisé" in etiquette else "",
                "remarque": "",
                # lien["href"] lit l'attribut href de la balise <a>.
                "url": lien["href"] if lien else "",
            }
        )
    return lignes


def collecter_velozophie(session):
    # Toutes les fonctions collecter_...() renvoient le même couple :
    # (liste des lignes collectées, texte de remarques pour le journal).

    # Calcule le délai nécessaire pour respecter robots.txt (10s pour ce site)
    delai = delai_du_site(session, VELOZOPHIE)
    velos, total = [], None
    # Parcourt chaque catégorie définie dans CATEGORIES_VELOZOPHIE.
    # Chaque tuple est « déballé » dans deux variables : categorie et fin.
    for categorie, fin in CATEGORIES_VELOZOPHIE:
        # obtenir() vérifie robots.txt, attend le délai et réessaie en cas d'erreur.
        r = ws.obtenir(session, VELOZOPHIE + fin, delai=delai)
        if r is None:  # Saute si la page est introuvable (ex: 404)
            continue
        sp = ws.soupe(r)
        # Extrait les vélos de la page courante
        for ligne in extraire_velozophie(sp, r.url):
            # La catégorie ne figure pas dans la carte : on l'ajoute nous-mêmes.
            ligne["categorie"] = categorie
            velos.append(ligne)
        # Récupère le nombre total de vélos annoncé par le site (pour le journal)
        if categorie == "Vélos":
            total = nombre_annonce(ws.texte(sp, ".pager__count-pages"))

    # f-string : les expressions entre accolades sont remplacées par leur valeur.
    # {delai:.0f} affiche le nombre sans décimale.
    remarques = (
        f"{len(CATEGORIES_VELOZOPHIE)} catégories, délai {delai:.0f} s ; "
        f"le site annonce {total} vélos ; pagination en JavaScript non suivie"
    )
    return velos, remarques


# ============================================================================
# 5. SITE 2 — PRIMEAU VÉLO
#    Page 1 de chaque sous-catégorie (12 vélos au plus).
#    robots.txt interdit toute URL avec un paramètre (Disallow: *?*=*).
#    Donc pas de « ?p=2 » : on contourne en visitant les sous-catégories.
# ============================================================================

PRIMEAU = "https://www.primeauvelo.com/fr_ca/velos"
# Expression régulière précompilée : une adresse à exactement deux niveaux
# sous /velos/, par exemple /velos/velos-de-montagne/enduro.
# [\w-]+ = une suite de lettres, chiffres, « _ » ou « - ».
SOUS_CATEGORIE = re.compile(r"https://www\.primeauvelo\.com/fr_ca/velos/[\w-]+/[\w-]+")


def sous_categories_primeau(sp):
    """(nom, url) des liens du menu à deux niveaux sous /velos/, sans « ? »."""
    # Dictionnaire url -> nom : une même adresse peut apparaître plusieurs fois
    # dans le menu ; on la garde une seule fois.
    noms = {}
    # Cherche tous les liens vers les catégories de vélos.
    # [href^='...'] : attribut href qui COMMENCE par ce texte.
    for a in sp.select("a[href^='https://www.primeauvelo.com/fr_ca/velos/']"):
        url = a["href"]
        # Nettoie le nom (ex: enlève les nombres à la fin comme "Enduro 27")
        nom = re.sub(r"\s*\d+$", "", ws.texte(a))
        # On évite les URLs avec des paramètres "?" (interdites par robots.txt).
        # Si l'adresse est déjà connue, on garde le nom le plus long (le plus clair).
        if "?" not in url and len(nom) > len(noms.get(url, "")):
            noms[url] = nom
    resultat = []
    # Construit le chemin complet de la catégorie (Parent / Enfant)
    for url, nom in noms.items():
        # fullmatch() : toute l'adresse doit correspondre au motif (2 niveaux).
        if SOUS_CATEGORIE.fullmatch(url):
            # rsplit("/", 1) coupe au dernier « / » : on obtient l'adresse
            # du parent, dont on cherche le nom dans le même dictionnaire.
            parent = noms.get(url.rsplit("/", 1)[0], "")
            resultat.append((f"{parent} / {nom}", url))
    return resultat


def extraire_primeau(sp, url_page):
    """Une ligne par li.product-item. Les prix sont des nombres (data-price-amount)."""
    lignes = []
    # Parcourt chaque élément de produit dans la liste
    for carte in sp.select("li.product-item"):
        lien = carte.select_one("a.product-item-link")
        # Les prix sont stockés dans des attributs data-price-amount.
        # [data-price-type=finalPrice] : élément dont l'attribut a cette valeur.
        prix = carte.select_one("[data-price-type=finalPrice]")
        ancien = carte.select_one("[data-price-type=oldPrice]")
        nom = ws.texte(lien)
        marque = ws.texte(carte, ".product-item-manufacturer")
        # Certains prix sont indicatifs ("à partir de").
        # « is not None » transforme le résultat en True/False.
        a_partir_de = carte.select_one(".pdp-tile-starting-at") is not None
        lignes.append(
            {
                "entreprise": "Primeau Vélo",
                "marque": marque,
                "nom": nom,
                # L'attribut contient déjà un nombre (« 1299.99 ») : float() suffit,
                # pas besoin de nettoyer un texte comme « 1 299,99 $ ».
                "prix": float(prix["data-price-amount"]) if prix else None,
                "prix_regulier": float(ancien["data-price-amount"]) if ancien else None,
                # L'adresse de la page peut aussi indiquer « occasion » ou « demo ».
                "etat": etat_du_velo(nom + " " + url_page),
                "disponibilite": "",
                "remarque": "prix à partir de" if a_partir_de else "",
                "url": lien["href"] if lien else "",
            }
        )
    return lignes


def collecter_primeau(session):
    # Respecte le délai de 10s imposé par robots.txt
    delai = delai_du_site(session, PRIMEAU)
    r = ws.obtenir(session, PRIMEAU, delai=delai)  # page de départ
    if r is None:
        return [], "page de départ introuvable (404)"
    sp_depart = ws.soupe(r)
    # Récupère le nombre total de vélos via le compteur de la barre d'outils
    total = nombre_annonce(ws.texte(sp_depart, ".toolbar-amount"))
    # Détecte automatiquement les sous-catégories pour avoir des libellés précis
    sous_categories = sous_categories_primeau(sp_depart)
    velos = []
    # Scrape la première page de chaque sous-catégorie
    for categorie, url in sous_categories:
        r = ws.obtenir(session, url, delai=delai)
        if r is None:
            continue
        for ligne in extraire_primeau(ws.soupe(r), r.url):
            ligne["categorie"] = categorie
            velos.append(ligne)
    # Complète avec la page de départ (contient les nouveaux arrivages par ex).
    # Ajoutée EN DERNIER : si un vélo est déjà vu dans une sous-catégorie,
    # c'est la catégorie précise qui sera gardée par drop_duplicates().
    for ligne in extraire_primeau(sp_depart, PRIMEAU):
        ligne["categorie"] = "Vélos"
        velos.append(ligne)
    remarques = (
        f"page 1 de {len(sous_categories)} sous-catégories, délai {delai:.0f} s ; "
        f"le site annonce {total} vélos ; ?p=2 interdit par robots.txt"
    )
    return velos, remarques


# ============================================================================
# 6. SITE 3 — EXPÉDITIONS WAKEFIELD
#    Vélos (pagination /page/2/), forfaits d'atelier, tarifs de location.
# ============================================================================

EXPEDITIONS = "https://expeditionswakefield.com"
# Catégories du site (en anglais) qui correspondent à des types de vélos.
TYPES_EXPEDITIONS = ["Mountain Bikes", "Gravel & Hybrid", "Kids Bikes"]
# Traduction des en-têtes de colonnes du tableau de location.
DUREES = {
    "1-HOUR": "1 heure",
    "3-HOUR": "3 heures",
    "FULL DAY": "1 jour",
    "2-DAY": "2 jours",
    "3-DAY": "3 jours",
    "4-DAY": "4 jours",
    "WEEKLY": "1 semaine",
}


def extraire_expeditions(sp, url_page):
    """Une ligne par carte div.product-grid-item (WooCommerce, thème WoodMart)."""
    lignes = []
    # Parcourt les produits WooCommerce
    for carte in sp.select("div.product-grid-item"):
        # Sélecteur avec espace = descendant : un <a> situé DANS .wd-entities-title.
        lien = carte.select_one(".wd-entities-title a")
        nom = ws.texte(lien)
        # Récupère toutes les catégories associées au produit
        categories = [ws.texte(a) for a in carte.select(".wd-product-cats a")]
        # Gestion des prix barrés (ins = prix actuel, del = ancien prix)
        solde = carte.select_one("span.price ins .woocommerce-Price-amount")
        barre = carte.select_one("span.price del .woocommerce-Price-amount")
        # Récupère tous les montants (parfois plusieurs pour les variations)
        montants = [
            ws.prix_en_nombre(ws.texte(m))
            for m in carte.select("span.price .woocommerce-Price-amount")
        ]
        montants = [m for m in montants if m is not None]
        remarque = ""
        if solde:  # Produit en promotion
            prix = ws.prix_en_nombre(ws.texte(solde))
            prix_regulier = ws.prix_en_nombre(ws.texte(barre))
        else:
            # Si pas de solde, on prend le prix minimum trouvé
            prix = min(montants) if montants else None
            prix_regulier = None
            if len(montants) > 1:
                remarque = "fourchette de prix"
        # Filtre les catégories pour ne garder que celles liées aux types de vélos
        types = [c for c in categories if c in TYPES_EXPEDITIONS]
        lignes.append(
            {
                "entreprise": "Expéditions Wakefield",
                # ", ".join(liste) : ["A", "B"] -> "A, B"
                "categorie": ", ".join(types),
                "marque": "",  # La marque sera extraite du nom
                "nom": nom,
                "prix": prix,
                "prix_regulier": prix_regulier,
                "etat": etat_du_velo(nom + " " + " ".join(categories)),
                # Disponibilité basée sur la classe CSS de WooCommerce.
                # carte["class"] est la LISTE des classes de la balise.
                "disponibilite": (
                    "en stock" if "instock" in carte["class"] else "épuisé"
                ),
                "remarque": remarque,
                "url": lien["href"] if lien else "",
            }
        )
    return lignes


def collecter_velos_expeditions(session):
    url = EXPEDITIONS + "/product-category/bikes/"
    # Cas C : parcourir_pages() suit le lien « page suivante » (/page/2/, ...)
    # et appelle extraire_expeditions() sur chaque page, 5 pages au plus.
    # On passe la FONCTION elle-même (sans parenthèses) : c'est
    # parcourir_pages() qui l'appellera.
    velos = ws.parcourir_pages(session, url, extraire_expeditions, max_pages=5)
    return velos, "délai 1 s ; disponibilité de la boutique en ligne"


def extraire_atelier_expeditions(sp, url_page):
    """Forfaits d'atelier : blocs .rpt_plan (titre, prix, sous-titre)."""
    lignes = []
    # Chaque forfait (mise au point, réparation...) est un bloc .rpt_plan.
    for forfait in sp.select(".rpt_plan"):
        prix_texte = ws.texte(forfait, ".rpt_price")
        # On garde le texte original ET les nombres : le texte sert de preuve.
        prix_min, prix_max = prix_min_max(prix_texte)
        sous_titre = ws.texte(forfait, ".rpt_subtitle")
        detail = ws.texte(forfait, ".rpt_description")
        lignes.append(
            {
                "entreprise": "Expéditions Wakefield",
                "type": "atelier",
                "service": ws.texte(forfait, ".rpt_title"),
                "duree": "",
                "prix_min": prix_min,
                "prix_max": prix_max,
                "prix_texte": prix_texte,
                # strip() retire l'espace en trop si l'une des deux parties est vide.
                "description": (sous_titre + " " + detail).strip(),
                "url": url_page,
            }
        )
    return lignes


def collecter_atelier_expeditions(session):
    # Une seule page à lire (cas A) : pas de boucle, pas de pagination.
    r = ws.obtenir(session, EXPEDITIONS + "/service/")
    if r is None:
        return [], "page introuvable (404)"
    return extraire_atelier_expeditions(ws.soupe(r), r.url), "forfaits en blocs"


def collecter_location_expeditions(session):
    """Cas B : deux tableaux (hiver et été). On garde les lignes de vélos."""
    url = EXPEDITIONS + "/rentals/"
    lignes = []
    # extraire_tableaux() utilise pandas.read_html : chaque <table> de la page
    # devient directement un DataFrame.
    for tableau in ws.extraire_tableaux(session, url):
        # La première colonne n'a pas toujours le même nom : on l'appelle « article ».
        tableau = tableau.rename(columns={tableau.columns[0]: "article"})
        # Retire les lignes sans nom d'article (lignes de séparation).
        tableau = tableau.dropna(subset=["article"])
        # Garde seulement les vélos. « bike|mtb|hybrid » : un des trois mots
        # (le « | » signifie « ou » dans une expression régulière).
        velos = tableau[tableau["article"].str.contains("bike|mtb|hybrid", case=False)]
        # Format long : une ligne par article et par durée.
        # melt() transforme les colonnes de durée (1-HOUR, FULL DAY...) en lignes :
        #   article | 1-HOUR | FULL DAY      article | duree    | prix_texte
        #   MTB     | $30    | $80      ->   MTB     | 1-HOUR   | $30
        #                                    MTB     | FULL DAY | $80
        tarifs = velos.melt(
            id_vars="article", var_name="duree", value_name="prix_texte"
        )
        # iterrows() parcourt le DataFrame ligne par ligne : (index, ligne).
        for _, tarif in tarifs.iterrows():
            # str() : la case peut être un nombre ou NaN, pas forcément un texte.
            prix_min, prix_max = prix_min_max(str(tarif["prix_texte"]))
            lignes.append(
                {
                    "entreprise": "Expéditions Wakefield",
                    "type": "location",
                    "service": tarif["article"],
                    # Traduit la durée ; si elle est inconnue, on la garde telle quelle.
                    "duree": DUREES.get(tarif["duree"], tarif["duree"]),
                    "prix_min": prix_min,
                    "prix_max": prix_max,
                    "prix_texte": tarif["prix_texte"],
                    "description": "",
                    "url": url,
                }
            )
    return lignes, "skis, raquettes, kayaks, canots et planches retirés"


# ============================================================================
# 7. SITE 4 — SAP OUTAOUAIS
#    Sous-catégorie « Vélo » (pagination ?page=2), atelier vélo.
#    Encodage non déclaré : obtenir() le corrige (cas H).
# ============================================================================

SAP = "https://sapoutaouais.com/fr/"
SAP_VELOS = SAP + "sports/velo/?categorie=203385874"  # bouton « Vélo »


def extraire_sap(sp, url_page):
    """Une ligne par carte a.product-card (24 au plus par page)."""
    # Vérification de sécurité pour ne pas sortir de la catégorie "Vélo".
    # Si la pagination menait à une page sans le filtre, on y trouverait
    # aussi des casques, des protections, etc. : on n'en extrait rien.
    if "categorie=" not in url_page:
        return []
    lignes = []
    # Parcourt chaque carte de produit (ici, la carte est elle-même un lien <a>)
    for carte in sp.select("a.product-card"):
        nom = ws.texte(carte, "h3")
        marque = ws.texte(carte, ".product-card-brand")
        lignes.append(
            {
                "entreprise": "SAP Outaouais",
                "categorie": "Vélo",
                "marque": marque,
                "nom": nom,
                # Extrait le prix dans la balise strong
                "prix": ws.prix_en_nombre(ws.texte(carte, "strong.price")),
                "prix_regulier": None,  # le site n'affiche pas de prix barré
                "etat": etat_du_velo(nom),
                "disponibilite": ws.texte(carte, ".product-card-availability"),
                "remarque": "",
                # Reconstruit l'URL complète car le lien est relatif
                "url": urljoin(url_page, carte["href"]),
            }
        )
    return lignes


def collecter_velos_sap(session):
    # Cas C : même fonction de pagination que pour Expéditions Wakefield ;
    # seule la fonction d'extraction change. C'est l'intérêt de la boîte à outils.
    velos = ws.parcourir_pages(session, SAP_VELOS, extraire_sap, max_pages=10)
    return velos, "délai 1 s ; protections et accessoires de vélo exclus"


def extraire_atelier_sap(sp, url_page):
    """Liste de prix « atelier-velo » : un article.price-item par service."""
    lignes = []
    # Cible spécifiquement la liste de prix de l'atelier vélo
    # (la même page contient aussi les prix de l'atelier de ski).
    for service in sp.select("div[data-price-list='atelier-velo'] article.price-item"):
        prix_texte = ws.texte(service, ".service-price")
        # Gère les fourchettes de prix si présentes
        prix_min, prix_max = prix_min_max(prix_texte)
        lignes.append(
            {
                "entreprise": "SAP Outaouais",
                "type": "atelier",
                "service": ws.texte(service, "h3"),
                "duree": "",
                "prix_min": prix_min,
                "prix_max": prix_max,
                "prix_texte": prix_texte,
                "description": "",
                "url": url_page,
            }
        )
    return lignes


def collecter_atelier_sap(session):
    r = ws.obtenir(session, SAP + "atelier/")
    if r is None:
        return [], "page introuvable (404)"
    lignes = extraire_atelier_sap(ws.soupe(r), r.url)
    return lignes, "« Prix à confirmer » gardé, sans nombre"


# ============================================================================
# 8. PROGRAMME PRINCIPAL
# ============================================================================

# (fichier, fonction, URL de départ, cas confirmé, technique)
# Cette liste décrit TOUTES les collectes. Pour ajouter une source, il suffit
# d'écrire sa fonction collecter_...() et d'ajouter un tuple ici :
# main() n'a pas besoin d'être modifié.
# Les fonctions sont rangées dans la liste comme n'importe quelle valeur.
SOURCES = [
    (
        "velos",
        collecter_velos_expeditions,
        EXPEDITIONS + "/product-category/bikes/",
        "C (/page/2/) et A",
        "parcourir_pages() et blocs div.product-grid-item",
    ),
    (
        "atelier",
        collecter_atelier_expeditions,
        EXPEDITIONS + "/service/",
        "A",
        "requests + BeautifulSoup, blocs .rpt_plan",
    ),
    (
        "atelier",
        collecter_location_expeditions,
        EXPEDITIONS + "/rentals/",
        "B",
        "pandas.read_html (extraire_tableaux)",
    ),
    (
        "velos",
        collecter_velos_sap,
        SAP_VELOS,
        "C (?page=2), A et H",
        "parcourir_pages() et blocs a.product-card",
    ),
    (
        "atelier",
        collecter_atelier_sap,
        SAP + "atelier/",
        "A et H",
        "blocs article.price-item",
    ),
    (
        "velos",
        collecter_velozophie,
        VELOZOPHIE + "Velos-c164664755",
        "A ; C impossible (liens javascript:;)",
        "une page par catégorie, blocs div.grid-product__wrap",
    ),
    (
        "velos",
        collecter_primeau,
        PRIMEAU,
        "A ; C interdit par robots.txt",
        "page 1 de chaque sous-catégorie, blocs li.product-item",
    ),
]


def sauvegarder(velos, atelier):
    """Nettoyage final, puis un fichier CSV par type de données."""
    # Création du DataFrame pour les vélos.
    # columns= impose l'ordre des colonnes ; une clé absente d'un dictionnaire
    # (par exemple en_solde) donne une colonne vide, calculée plus tard.
    df = pd.DataFrame(velos, columns=COLONNES_VELOS)
    df["date_collecte"] = AUJOURDHUI  # même valeur copiée sur toutes les lignes
    avant = len(df)
    # Application des règles de nettoyage (marques, doublons, etc.)
    df = nettoyer_velos(df)
    # Sauvegarde dans le fichier CSV final
    ws.sauvegarder_csv(df, FICHIER_VELOS)

    # Création du DataFrame pour l'atelier et la location
    services = pd.DataFrame(atelier, columns=COLONNES_ATELIER)
    services["date_collecte"] = AUJOURDHUI
    ws.sauvegarder_csv(services, FICHIER_ATELIER)

    # Affiche un résumé statistique dans le terminal
    print("\n===== Résumé =====")
    print(
        f"Vélos : {len(df)} (lignes retirées, doublons et pièces : {avant - len(df)})"
    )
    # value_counts() compte le nombre de lignes par entreprise.
    print(df["entreprise"].value_counts().to_string())
    # isna() donne True pour chaque prix vide ; sum() compte les True.
    print(f"Vélos sans prix : {df['prix'].isna().sum()}")
    print(f"Services et tarifs : {len(services)}")
    print(services["entreprise"].value_counts().to_string())


def main():
    # Vérifications de base (environnement et configuration)
    verifier_python()
    # Le cours exige un User-Agent honnête avec un vrai moyen de contact.
    if "exemple.ca" in ws.USER_AGENT:
        raise SystemExit("Mettez votre courriel dans USER_AGENT (projet_scraping.py).")

    # Prépare le dossier de résultats (exist_ok=True : pas d'erreur s'il existe)
    DOSSIER.mkdir(exist_ok=True)
    reparer_entete_journal()

    # Initialise la session HTTP partagée.
    # Une session réutilise la même connexion et les mêmes en-têtes
    # pour toutes les requêtes : plus rapide et plus poli envers les serveurs.
    session = ws.creer_session(ws.USER_AGENT)
    velos, atelier = [], []

    # Boucle sur toutes les sources configurées (vélos et ateliers)
    for fichier, fonction, url, cas, technique in SOURCES:
        # __name__ donne le nom de la fonction, par exemple « collecter_primeau ».
        print(f"\n===== {fonction.__name__} =====")
        try:
            # Exécute la fonction de collecte spécifique
            lignes, remarques = fonction(session)
            statut = 200
        except Exception as e:
            # En cas d'erreur, on logue mais on continue avec la source suivante.
            # Ainsi, un site en panne n'empêche pas de sauvegarder les trois autres.
            lignes, remarques, statut = [], f"ERREUR {type(e).__name__} : {e}", ""
            print(remarques)

        # Accumule les données dans la bonne liste (velos ou atelier).
        # extend() ajoute tous les éléments d'une liste à la fin d'une autre.
        if fichier == "velos":
            velos.extend(lignes)
            # { ... } crée un ensemble (set) : chaque URL n'y est comptée qu'une fois.
            nb = len({ligne["url"] for ligne in lignes})  # Compte les vélos uniques via URL
            remarques = (
                f"{len(lignes)} lignes lues, {nb} vélos différents ; {remarques}"
            )
            chemin = FICHIER_VELOS
        else:
            atelier.extend(lignes)
            nb = len(lignes)
            chemin = FICHIER_ATELIER

        # Ajoute une entrée détaillée dans le journal de bord
        noter(url, statut, cas, technique, nb, str(chemin), remarques)

    # Note spéciale pour Vélozophie (données non collectables techniquement).
    # Documenter ce qu'on n'a PAS pu collecter fait partie du travail.
    noter(
        "https://www.velozophie.ca/entretiens-complets/",
        "",
        "aucune donnée dans le HTML",
        "non collecté",
        0,
        "",
        "tarifs dans une image (MAP-FR-scaled.jpg) et un PDF (MAP-FR.pdf)",
    )

    # Nettoyage et sauvegarde finale (une seule fois, après toutes les collectes)
    sauvegarder(velos, atelier)
    print("\nJournal :", DOSSIER / "journal_tests.csv")


# Ce bloc s'exécute seulement quand on lance « python etape2_collecte.py ».
# Il ne s'exécute PAS quand un autre script fait « import etape2_collecte »
# (c'est le cas d'etape3_nettoyage.py, qui réutilise nettoyer_velos()).
if __name__ == "__main__":
    main()
