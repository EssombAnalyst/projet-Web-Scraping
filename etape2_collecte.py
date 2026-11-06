"""
etape2_collecte.py
Projet de groupe : vente et réparation de vélos en Outaouais.
Étape 2 : collecte des vélos et des tarifs d'atelier sur 4 sites.

Fichiers produits dans resultats/ :
  velos_outaouais.csv    un vélo par ligne
  atelier_outaouais.csv  un service d'atelier ou un tarif de location par ligne
  journal_tests.csv      une ligne de plus par source collectée

Lancer (dans le dossier du projet, environnement scraping) :
    python etape2_collecte.py
Durée : environ 10 minutes, à cause du Crawl-delay de 10 s
de Vélozophie et de Primeau Vélo.
"""

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import pandas as pd

import projet_scraping as ws
import scraper  # pour ajouter_au_journal()

DOSSIER = Path("resultats")
FICHIER_VELOS = DOSSIER / "velos_outaouais.csv"
FICHIER_ATELIER = DOSSIER / "atelier_outaouais.csv"
AUJOURDHUI = datetime.now().strftime("%Y-%m-%d")

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
MARQUES = (
    "Santa Cruz, Rocky Mountain, Urban Arrow, We The People, Specialized, "
    "Cannondale, Mondraker, Cervelo, Devinci, Giant, Liv, Trek, Norco, Kona, "
    "Salsa, Ibis, Amflow, Cube, Maui, Marin, Opus, Scott, Orbea, Pivot, Garneau, "
    "Sombrio, Haro, Dabomb, Surly, Gazelle, Benno, Husqvarna, GasGas, Electra, "
    "Shotgun, Reid, Minelli, Genesis, KHS, Felt, Radio, EVO, Neo"
).split(", ")

# Crawl-delay lu à la main dans robots.txt. Chez Vélozophie, la ligne
# « Crawl-delay: 10 » est avant « User-agent: * » : robotparser ne la voit pas.
DELAI_MINIMUM = {"www.velozophie.ca": 10, "www.primeauvelo.com": 10}


# ---------------------------------------------------------------------------
# Outils communs
# ---------------------------------------------------------------------------


def verifier_python():
    """Test du cours (Avant de scraper, 3.6) : robotparser suit-il la RFC 9309 ?"""
    test = RobotFileParser()
    test.parse(["User-agent: *", "Disallow: /docs/", "Allow: /docs/public/"])
    if not test.can_fetch("*", "https://www.exemple.ca/docs/public/guide.html"):
        raise SystemExit("Python trop ancien : utilisez l'environnement scraping.")


def delai_du_site(session, url):
    """Pause entre deux requêtes : le plus grand entre le Crawl-delay lu par
    robotparser, celui de DELAI_MINIMUM et 1 s (règle du cours)."""
    # Récupère l'User-Agent configuré dans la session
    agent = session.headers["User-Agent"]
    # Vérifie les permissions dans robots.txt et récupère le crawl-delay officiel
    _, _, crawl_delay = ws.robots_autorise(url, agent, session)
    # Cherche si on a un délai minimum forcé pour ce domaine (ex: 10s)
    minimum = DELAI_MINIMUM.get(urlparse(url).netloc, 1.0)
    # On prend la valeur la plus restrictive
    return max(1.0, minimum, crawl_delay or 0)


def nombre_annonce(texte):
    """« 1 - 60 sur 366 » -> 366   « Résultats 1 - 12 de 365 » -> 365"""
    m = re.search(r"(?:sur|de|of)\s+(\d+)", texte or "")
    return int(m.group(1)) if m else None


def prix_min_max(texte):
    """« $260.00- $330.00 » -> (260.0, 330.0)   « 59,99 $ » -> (59.99, 59.99)"""
    # Sépare le texte s'il y a un tiret ou le mot "à" (fourchette de prix)
    morceaux = re.split(r"\s*-\s*|\s+à\s+", texte or "")
    # Convertit chaque morceau en nombre flottant
    nombres = [ws.prix_en_nombre(m) for m in morceaux]
    # Filtre les valeurs None (e.g. si un texte n'était pas un prix)
    nombres = [n for n in nombres if n is not None]
    if not nombres:
        return None, None  # par exemple « Prix à confirmer »
    # Retourne la borne basse et la borne haute
    return min(nombres), max(nombres)


def trouver_marque(texte, marque_du_site=""):
    """Marque de la liste MARQUES trouvée dans le texte ; sinon celle du site."""
    for marque in MARQUES:
        if re.search(r"\b" + re.escape(marque) + r"\b", texte, re.IGNORECASE):
            return marque
    return marque_du_site


def etat_du_velo(texte):
    """« occasion » ou « démo » quand le site l'indique ; vide sinon."""
    texte = texte.lower()
    if "occasion" in texte or "usagé" in texte:
        return "occasion"
    if "démo" in texte or "demo" in texte:
        return "démo"
    return ""


TEXTES = "entreprise categorie marque nom etat disponibilite remarque url".split()


def nettoyer_velos(df):
    """Nettoyage des vélos (cas H et suivants). Ne contacte aucun site :
    etape3_nettoyage.py peut le refaire sur un CSV déjà collecté."""
    df = df.copy()
    # Nettoyage des espaces et caractères spéciaux dans les colonnes textuelles
    for colonne in TEXTES:
        df[colonne] = df[colonne].fillna("").astype(str).map(ws.nettoyer_chaine)
    
    # Supprime le tiret final souvent présent dans les noms
    df["nom"] = df["nom"].str.rstrip(" -")  # « Felt VR 60 - » -> « Felt VR 60 »
    
    # Enrichit la colonne marque en cherchant dans le nom si elle est vide
    df["marque"] = [
        trouver_marque(marque + " " + nom, marque)
        for marque, nom in zip(df["marque"], df["nom"])
    ]
    
    # Filtrage spécifique pour SAP : exclure les accessoires qui ne sont pas des vélos
    # SAP range parfois des roues dans la catégorie vélos.
    nom = df["nom"].str.lower()
    piece = (
        (df["entreprise"] == "SAP Outaouais")
        & nom.str.contains("roue")
        & ~nom.str.contains("vélo")
    )
    df = df[~piece]
    
    # Supprime les doublons basés sur l'URL (même produit vu dans plusieurs catégories)
    df = df.drop_duplicates(subset=["entreprise", "url"])
    
    # Conversion des prix en nombres pour permettre les calculs
    df["prix"] = pd.to_numeric(df["prix"], errors="coerce")
    df["prix_regulier"] = pd.to_numeric(df["prix_regulier"], errors="coerce")
    # Calcule si le produit est en promotion
    df["en_solde"] = df["prix_regulier"] > df["prix"]
    
    # Trie par entreprise, puis catégorie, puis prix croissant
    return df.sort_values(["entreprise", "categorie", "prix"])[COLONNES_VELOS]


ENTETE_JOURNAL = (
    "date,url,statut,robots,cas,recommandation,nb_lignes,fichiers,remarques"
)


def reparer_entete_journal():
    """Remet la ligne d'en-tête si journal_tests.csv avait été créé vide à la main."""
    journal = DOSSIER / "journal_tests.csv"
    if journal.exists():
        texte = journal.read_text(encoding="utf-8-sig")
        if texte and not texte.startswith("date,"):
            journal.write_text(ENTETE_JOURNAL + "\n" + texte, encoding="utf-8-sig")
            print("Ligne d'en-tête ajoutée au début du journal.")


def noter(url, statut, cas, technique, nb_lignes, fichier, remarques):
    """Une ligne au journal, avec les mêmes colonnes que scraper.py."""
    ligne = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "url": url,
        "statut": statut,
        "robots": "autorisé par robots.txt" if statut else "",
        "cas": cas,
        "recommandation": technique,
        "nb_lignes": nb_lignes,
        "fichiers": fichier,
        "remarques": remarques,
    }
    scraper.ajouter_au_journal(ligne, DOSSIER)


# ---------------------------------------------------------------------------
# 1. Vélozophie : une page par catégorie (60 vélos au plus par page).
#    La pagination est en JavaScript (liens javascript:;) : on ne la suit pas.
# ---------------------------------------------------------------------------

VELOZOPHIE = "https://www.velozophie.ca/store/"
# Catégories précises d'abord : un vélo vu deux fois garde la plus précise.
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
    lignes = []
    # Parcourt chaque bloc de produit dans la grille
    for carte in sp.select("div.grid-product__wrap"):
        lien = carte.select_one("a.grid-product__title")
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
                "prix": ws.prix_en_nombre(prix),
                "prix_regulier": ws.prix_en_nombre(prix_barre),
                # Détecte si c'est un vélo de démo ou d'occasion via le nom ou l'étiquette
                "etat": etat_du_velo(nom + " " + etiquette),
                # Marque comme épuisé si l'étiquette contient "puisé"
                "disponibilite": "épuisé" if "puisé" in etiquette else "",
                "remarque": "",
                "url": lien["href"] if lien else "",
            }
        )
    return lignes


def collecter_velozophie(session):
    # Calcule le délai nécessaire pour respecter robots.txt (10s pour ce site)
    delai = delai_du_site(session, VELOZOPHIE)
    velos, total = [], None
    # Parcourt chaque catégorie définie dans CATEGORIES_VELOZOPHIE
    for categorie, fin in CATEGORIES_VELOZOPHIE:
        r = ws.obtenir(session, VELOZOPHIE + fin, delai=delai)
        if r is None:  # Saute si la page est introuvable (ex: 404)
            continue
        sp = ws.soupe(r)
        # Extrait les vélos de la page courante
        for ligne in extraire_velozophie(sp, r.url):
            ligne["categorie"] = categorie
            velos.append(ligne)
        # Récupère le nombre total de vélos annoncé par le site (pour le journal)
        if categorie == "Vélos":
            total = nombre_annonce(ws.texte(sp, ".pager__count-pages"))
            
    remarques = (
        f"{len(CATEGORIES_VELOZOPHIE)} catégories, délai {delai:.0f} s ; "
        f"le site annonce {total} vélos ; pagination en JavaScript non suivie"
    )
    return velos, remarques


# ---------------------------------------------------------------------------
# 2. Primeau Vélo : page 1 de chaque sous-catégorie (12 vélos au plus).
#    robots.txt interdit toute URL avec un paramètre (Disallow: *?*=*).
# ---------------------------------------------------------------------------

PRIMEAU = "https://www.primeauvelo.com/fr_ca/velos"
SOUS_CATEGORIE = re.compile(r"https://www\.primeauvelo\.com/fr_ca/velos/[\w-]+/[\w-]+")


def sous_categories_primeau(sp):
    """(nom, url) des liens du menu à deux niveaux sous /velos/, sans « ? »."""
    noms = {}
    # Cherche tous les liens vers les catégories de vélos
    for a in sp.select("a[href^='https://www.primeauvelo.com/fr_ca/velos/']"):
        url = a["href"]
        # Nettoie le nom (ex: enlève les nombres à la fin comme "Enduro 27")
        nom = re.sub(r"\s*\d+$", "", ws.texte(a))
        # On évite les URLs avec des paramètres "?" (interdites par robots.txt)
        if "?" not in url and len(nom) > len(noms.get(url, "")):
            noms[url] = nom
    resultat = []
    # Construit le chemin complet de la catégorie (Parent / Enfant)
    for url, nom in noms.items():
        if SOUS_CATEGORIE.fullmatch(url):
            parent = noms.get(url.rsplit("/", 1)[0], "")
            resultat.append((f"{parent} / {nom}", url))
    return resultat


def extraire_primeau(sp, url_page):
    """Une ligne par li.product-item. Les prix sont des nombres (data-price-amount)."""
    lignes = []
    # Parcourt chaque élément de produit dans la liste
    for carte in sp.select("li.product-item"):
        lien = carte.select_one("a.product-item-link")
        # Les prix sont stockés dans des attributs data-price-amount
        prix = carte.select_one("[data-price-type=finalPrice]")
        ancien = carte.select_one("[data-price-type=oldPrice]")
        nom = ws.texte(lien)
        marque = ws.texte(carte, ".product-item-manufacturer")
        # Certains prix sont indicatifs ("à partir de")
        a_partir_de = carte.select_one(".pdp-tile-starting-at") is not None
        lignes.append(
            {
                "entreprise": "Primeau Vélo",
                "marque": marque,
                "nom": nom,
                "prix": float(prix["data-price-amount"]) if prix else None,
                "prix_regulier": float(ancien["data-price-amount"]) if ancien else None,
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
    # Complète avec la page de départ (contient les nouveaux arrivages par ex)
    for ligne in extraire_primeau(sp_depart, PRIMEAU):
        ligne["categorie"] = "Vélos"
        velos.append(ligne)
    remarques = (
        f"page 1 de {len(sous_categories)} sous-catégories, délai {delai:.0f} s ; "
        f"le site annonce {total} vélos ; ?p=2 interdit par robots.txt"
    )
    return velos, remarques


# ---------------------------------------------------------------------------
# 3. Expéditions Wakefield : vélos (pagination /page/2/), atelier, location
# ---------------------------------------------------------------------------

EXPEDITIONS = "https://expeditionswakefield.com"
TYPES_EXPEDITIONS = ["Mountain Bikes", "Gravel & Hybrid", "Kids Bikes"]
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
                "categorie": ", ".join(types),
                "marque": "",  # La marque sera extraite du nom
                "nom": nom,
                "prix": prix,
                "prix_regulier": prix_regulier,
                "etat": etat_du_velo(nom + " " + " ".join(categories)),
                # Disponibilité basée sur la classe CSS de WooCommerce
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
    velos = ws.parcourir_pages(session, url, extraire_expeditions, max_pages=5)
    return velos, "délai 1 s ; disponibilité de la boutique en ligne"


def extraire_atelier_expeditions(sp, url_page):
    """Forfaits d'atelier : blocs .rpt_plan (titre, prix, sous-titre)."""
    lignes = []
    for forfait in sp.select(".rpt_plan"):
        prix_texte = ws.texte(forfait, ".rpt_price")
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
                "description": (sous_titre + " " + detail).strip(),
                "url": url_page,
            }
        )
    return lignes


def collecter_atelier_expeditions(session):
    r = ws.obtenir(session, EXPEDITIONS + "/service/")
    if r is None:
        return [], "page introuvable (404)"
    return extraire_atelier_expeditions(ws.soupe(r), r.url), "forfaits en blocs"


def collecter_location_expeditions(session):
    """Cas B : deux tableaux (hiver et été). On garde les lignes de vélos."""
    url = EXPEDITIONS + "/rentals/"
    lignes = []
    for tableau in ws.extraire_tableaux(session, url):
        tableau = tableau.rename(columns={tableau.columns[0]: "article"})
        tableau = tableau.dropna(subset=["article"])
        velos = tableau[tableau["article"].str.contains("bike|mtb|hybrid", case=False)]
        # Format long : une ligne par article et par durée
        tarifs = velos.melt(
            id_vars="article", var_name="duree", value_name="prix_texte"
        )
        for _, tarif in tarifs.iterrows():
            prix_min, prix_max = prix_min_max(str(tarif["prix_texte"]))
            lignes.append(
                {
                    "entreprise": "Expéditions Wakefield",
                    "type": "location",
                    "service": tarif["article"],
                    "duree": DUREES.get(tarif["duree"], tarif["duree"]),
                    "prix_min": prix_min,
                    "prix_max": prix_max,
                    "prix_texte": tarif["prix_texte"],
                    "description": "",
                    "url": url,
                }
            )
    return lignes, "skis, raquettes, kayaks, canots et planches retirés"


# ---------------------------------------------------------------------------
# 4. SAP Outaouais : sous-catégorie « Vélo » (pagination ?page=2), atelier vélo.
#    Encodage non déclaré : obtenir() le corrige (cas H).
# ---------------------------------------------------------------------------

SAP = "https://sapoutaouais.com/fr/"
SAP_VELOS = SAP + "sports/velo/?categorie=203385874"  # bouton « Vélo »


def extraire_sap(sp, url_page):
    """Une ligne par carte a.product-card (24 au plus par page)."""
    # Vérification de sécurité pour ne pas sortir de la catégorie "Vélo"
    if "categorie=" not in url_page:
        return []
    lignes = []
    # Parcourt chaque carte de produit
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
                "prix_regulier": None,
                "etat": etat_du_velo(nom),
                "disponibilite": ws.texte(carte, ".product-card-availability"),
                "remarque": "",
                # Reconstruit l'URL complète car le lien est relatif
                "url": urljoin(url_page, carte["href"]),
            }
        )
    return lignes


def collecter_velos_sap(session):
    velos = ws.parcourir_pages(session, SAP_VELOS, extraire_sap, max_pages=10)
    return velos, "délai 1 s ; protections et accessoires de vélo exclus"


def extraire_atelier_sap(sp, url_page):
    """Liste de prix « atelier-velo » : un article.price-item par service."""
    lignes = []
    # Cible spécifiquement la liste de prix de l'atelier vélo
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


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

# (fichier, fonction, URL de départ, cas confirmé, technique)
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
    # Création du DataFrame pour les vélos
    df = pd.DataFrame(velos, columns=COLONNES_VELOS)
    df["date_collecte"] = AUJOURDHUI
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
    print(df["entreprise"].value_counts().to_string())
    print(f"Vélos sans prix : {df['prix'].isna().sum()}")
    print(f"Services et tarifs : {len(services)}")
    print(services["entreprise"].value_counts().to_string())


def main():
    # Vérifications de base (environnement et configuration)
    verifier_python()
    if "exemple.ca" in ws.USER_AGENT:
        raise SystemExit("Mettez votre courriel dans USER_AGENT (projet_scraping.py).")
    
    # Prépare le dossier de résultats
    DOSSIER.mkdir(exist_ok=True)
    reparer_entete_journal()
    
    # Initialise la session HTTP partagée
    session = ws.creer_session(ws.USER_AGENT)
    velos, atelier = [], []

    # Boucle sur toutes les sources configurées (vélos et ateliers)
    for fichier, fonction, url, cas, technique in SOURCES:
        print(f"\n===== {fonction.__name__} =====")
        try:
            # Exécute la fonction de collecte spécifique
            lignes, remarques = fonction(session)
            statut = 200
        except Exception as e:
            # En cas d'erreur, on logue mais on continue avec la source suivante
            lignes, remarques, statut = [], f"ERREUR {type(e).__name__} : {e}", ""
            print(remarques)
            
        # Accumule les données dans la bonne liste (velos ou atelier)
        if fichier == "velos":
            velos.extend(lignes)
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

    # Note spéciale pour Vélozophie (données non collectables techniquement)
    noter(
        "https://www.velozophie.ca/entretiens-complets/",
        "",
        "aucune donnée dans le HTML",
        "non collecté",
        0,
        "",
        "tarifs dans une image (MAP-FR-scaled.jpg) et un PDF (MAP-FR.pdf)",
    )
    
    # Nettoyage et sauvegarde finale
    sauvegarder(velos, atelier)
    print("\nJournal :", DOSSIER / "journal_tests.csv")


if __name__ == "__main__":
    main()
