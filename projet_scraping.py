"""
projet_scraping.py
Boîte à outils du projet de groupe « Web scraping : du diagnostic à la collecte »
Cours : Collecte et nettoyage des données

Contenu
  0. Configuration et session HTTP
  1. robots.txt et requêtes robustes (cas G)
  2. Diagnostic d'une page : diagnostiquer(url)
  3. Cas A  — HTML statique (citations)
     Cas B  — Tableaux HTML (pandas.read_html)
     Cas C  — Pagination
     Cas D  — Page liste -> pages détail (livres)
     Cas E  — Contenu JavaScript (JSON intégré, API cachée, Playwright)
     Cas F  — Formulaire de connexion (jeton CSRF)
     Cas H  — Encodage et nettoyage du texte
  4. Outils génériques (blocs répétés, export CSV)

Dépendances : pip install requests beautifulsoup4 lxml pandas playwright
              python -m playwright install chromium   (seulement pour le cas E2)
"""

import asyncio
import csv
import json
import logging
import re
import time
import unicodedata
from collections import Counter
from io import StringIO
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 0. Configuration
# ---------------------------------------------------------------------------

# Identifiez-vous honnêtement : nom du projet + moyen de vous joindre.
USER_AGENT = (
    "CoursCollecteDonnees/1.0 (projet etudiant; contact: 2752025@collegelacite.ca)"
)
DELAI = 1.0  # secondes d'attente après chaque requête (politesse)
TIMEOUT = 15  # secondes avant d'abandonner une requête

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraping")


def creer_session(user_agent=USER_AGENT):
    """Session HTTP réutilisable : garde les cookies et les en-têtes entre requêtes."""
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": user_agent,
            "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
        }
    )
    return s


class AccesBloque(Exception):
    """Le site refuse l'accès (401, 402, 403, défi anti-robot). On s'arrête."""


# ---------------------------------------------------------------------------
# 1. robots.txt et requêtes robustes
# ---------------------------------------------------------------------------

_cache_robots = {}


def robots_autorise(url, user_agent=USER_AGENT, session=None):
    """
    Retourne (autorise: bool, raison: str, crawl_delay: float|None).

    On télécharge robots.txt avec NOTRE session (même User-Agent) puis on le
    donne à urllib.robotparser. Règles de la norme RFC 9309 :
      - 2xx      -> on applique les règles du fichier
      - 4xx      -> pas de robots.txt utilisable : tout est permis
      - 5xx/panne -> prudence : on considère tout interdit
    """
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _cache_robots:
        session = session or creer_session(user_agent)
        try:
            r = session.get(base + "/robots.txt", timeout=TIMEOUT)
            if r.status_code == 200:
                rp = robotparser.RobotFileParser()
                rp.parse(r.text.splitlines())
                _cache_robots[base] = ("regles", rp)
            elif 400 <= r.status_code < 500:
                _cache_robots[base] = ("absent", None)
            else:
                _cache_robots[base] = ("erreur", None)
        except requests.RequestException:
            _cache_robots[base] = ("erreur", None)

    etat, rp = _cache_robots[base]
    if etat == "absent":
        return True, "pas de robots.txt (4xx) : accès permis", None
    if etat == "erreur":
        return (
            False,
            "robots.txt inaccessible (5xx ou réseau) : prudence, accès refusé",
            None,
        )
    ok = rp.can_fetch(user_agent, url)
    raison = "autorisé par robots.txt" if ok else "INTERDIT par robots.txt"
    return ok, raison, rp.crawl_delay(user_agent)


def signes_protection(r):
    """Liste des indices montrant qu'un système anti-robot a répondu."""
    signes = []
    serveur = r.headers.get("Server", "").lower()
    if "cf-mitigated" in r.headers:
        signes.append("en-tête cf-mitigated (défi Cloudflare)")
    if r.status_code == 402:
        signes.append("402 Payment Required (accès payant, ex. Pay Per Crawl)")
    if r.status_code in (403, 429, 503) and "cloudflare" in serveur:
        signes.append(f"{r.status_code} renvoyé par Cloudflare")
    debut = r.text[:20000].lower()
    for motif, sens in [
        ("just a moment", "page d'attente Cloudflare"),
        ("challenge-platform", "script de défi Cloudflare"),
        ("cf-chl", "défi Cloudflare"),
        ("attention required", "blocage Cloudflare"),
        ("datadome", "protection DataDome"),
        ("px-captcha", "protection PerimeterX / HUMAN"),
    ]:
        if motif in debut:
            signes.append(sens)
    if r.status_code in (403, 429) and "captcha" in debut:
        signes.append("CAPTCHA demandé")
    return sorted(set(signes))


def _lire_retry_after(r):
    valeur = r.headers.get("Retry-After", "")
    return float(valeur) if valeur.isdigit() else None


def corriger_encodage(r):
    """
    Cas H. Si l'en-tête Content-Type ne précise pas de charset, requests
    suppose ISO-8859-1 pour le texte : « £ » devient « Â£ ».
    On utilise alors l'encodage détecté dans le contenu.
    """
    if "charset" not in r.headers.get("Content-Type", "").lower():
        r.encoding = r.apparent_encoding
    return r


def obtenir(session, url, *, tentatives=4, delai=DELAI, verifier_robots=True, **kwargs):
    """
    GET robuste (cas G).
      200            -> retourne la réponse (encodage corrigé)
      404            -> retourne None
      429 / 5xx      -> attend (Retry-After ou 2, 4, 8 s…) puis réessaie
      401/402/403    -> lève AccesBloque : on ne contourne pas
    """
    if verifier_robots:
        ok, raison, _ = robots_autorise(
            url, session.headers.get("User-Agent", USER_AGENT), session
        )
        if not ok:
            raise AccesBloque(f"{raison} : {url}")

    timeout = kwargs.pop("timeout", TIMEOUT)
    for essai in range(1, tentatives + 1):
        try:
            r = session.get(url, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as e:
            attente = 2**essai
            log.warning(
                "Erreur réseau (%s) — nouvel essai dans %ss", type(e).__name__, attente
            )
            time.sleep(attente)
            continue

        log.info("%s  %s  (%.2fs)", r.status_code, url, r.elapsed.total_seconds())
        time.sleep(delai)

        if r.status_code == 200:
            return corriger_encodage(r)
        if r.status_code == 404:
            log.warning("Page introuvable : %s", url)
            return None
        signes = signes_protection(r)
        if r.status_code in (401, 402, 403) or signes:
            raise AccesBloque(f"{r.status_code} {', '.join(signes)} : {url}")
        if r.status_code == 429 or r.status_code >= 500:
            attente = min(_lire_retry_after(r) or 2**essai, 60)
            log.warning(
                "%s reçu — pause de %ss (essai %s/%s)",
                r.status_code,
                attente,
                essai,
                tentatives,
            )
            time.sleep(attente)
            continue
        r.raise_for_status()
        return r

    raise RuntimeError(f"Échec après {tentatives} tentatives : {url}")


def soupe(r_ou_html):
    """Construit un objet BeautifulSoup à partir d'une réponse ou d'une chaîne HTML."""
    html = r_ou_html.text if hasattr(r_ou_html, "text") else r_ou_html
    return BeautifulSoup(html, "lxml")


def texte(el, selecteur=None, defaut=""):
    """Texte nettoyé d'un élément (ou d'un sous-élément), sans planter s'il est absent."""
    if el is not None and selecteur:
        el = el.select_one(selecteur)
    return el.get_text(" ", strip=True) if el is not None else defaut


# ---------------------------------------------------------------------------
# 2. Diagnostic
# ---------------------------------------------------------------------------

_MOTIFS_PAGINATION = re.compile(
    r"([?&](page|p|pg|page_num|offset|start)=\d+)|(/page[-/]?\d+)", re.I
)
_TEXTE_SUIVANT = re.compile(r"^\s*(?:(?:suivante?|next)(?!\w)|›|»|>)", re.I)


def texte_visible(sp):
    copie = BeautifulSoup(str(sp), "lxml")
    for balise in copie(["script", "style", "noscript", "template", "svg"]):
        balise.decompose()
    return re.sub(r"\s+", " ", copie.get_text(" ", strip=True))


def trouver_page_suivante(sp, url_courante):
    """Cherche le lien « page suivante » par les conventions les plus courantes."""
    lien = (
        sp.select_one("li.next a[href]")
        or sp.select_one("a[rel~=next][href]")
        or sp.select_one("link[rel~=next][href]")
        or sp.select_one(
            "a.next[href], a.pagination-next[href], [aria-label*=next i][href], [aria-label*=suivant i][href]"
        )
        or sp.find("a", href=True, string=_TEXTE_SUIVANT)
    )
    return urljoin(url_courante, lien["href"]) if lien else None


def diagnostiquer(url, session=None, afficher=True):
    """
    Analyse une page et recommande une technique.
    Retourne un dictionnaire (utile pour remplir un journal de tests).
    """
    session = session or creer_session()
    d = {"url": url, "cas": [], "alertes": []}

    ok, raison, crawl_delay = robots_autorise(
        url, session.headers["User-Agent"], session
    )
    d.update(robots_autorise=ok, robots_raison=raison, crawl_delay=crawl_delay)

    try:
        debut = time.time()
        r = session.get(url, timeout=TIMEOUT)
        d["duree_s"] = round(time.time() - debut, 2)
    except requests.RequestException as e:
        d.update(statut=None, recommandation=f"Échec réseau : {type(e).__name__}")
        if afficher:
            afficher_diagnostic(d)
        return d

    ctype = r.headers.get("Content-Type", "")
    d.update(
        statut=r.status_code,
        url_finale=r.url,
        content_type=ctype.split(";")[0].strip(),
        charset_declare=(re.search(r"charset=([\w-]+)", ctype, re.I) or [None, None])[
            1
        ],
        encodage_requests=r.encoding,
        encodage_detecte=r.apparent_encoding,
        taille_ko=round(len(r.content) / 1024, 1),
        serveur=r.headers.get("Server", ""),
    )
    d["protection"] = signes_protection(r)

    # --- Réponse JSON directe : c'est une API, pas une page
    if "json" in d["content_type"]:
        d["cas"].append("E1 (réponse JSON : traiter comme une API)")

    corriger_encodage(r)
    sp = soupe(r)
    visible = texte_visible(sp)
    scripts = sp.find_all("script")
    d.update(
        titre=texte(sp.title),
        longueur_texte_visible=len(visible),
        nb_scripts=len(scripts),
        nb_tableaux=len(sp.find_all("table")),
        nb_liens=len(sp.find_all("a", href=True)),
        formulaire_connexion=bool(sp.select_one("input[type=password]")),
        meta_robots=(
            sp.find("meta", attrs={"name": re.compile("^robots$", re.I)}) or {}
        ).get("content", ""),
    )

    # --- Signes de rendu JavaScript
    signes_js = []
    for sel in ["#root", "#app", "#__next", "#__nuxt", "[ng-app]", "[data-reactroot]"]:
        el = sp.select_one(sel)
        if el is not None and len(el.get_text(strip=True)) < 50:
            signes_js.append(f"conteneur {sel} vide")
    if sp.find("noscript", string=re.compile("javascript", re.I)):
        signes_js.append("<noscript> demande JavaScript")
    if len(visible) < 500 and len(scripts) >= 3:
        signes_js.append("très peu de texte visible, beaucoup de scripts")
    d["signes_js"] = signes_js

    # --- Données JSON intégrées dans la page
    json_integre = []
    if sp.select_one("script#__NEXT_DATA__"):
        json_integre.append("__NEXT_DATA__ (Next.js)")
    if sp.select("script[type='application/ld+json']"):
        json_integre.append("JSON-LD (schema.org)")
    if sp.select("script[type='application/json']"):
        json_integre.append("script application/json")
    for s in scripts:
        contenu = s.string or ""
        if (
            re.search(r"(var|let|const)\s+\w+\s*=\s*[\[{]", contenu)
            and len(contenu) > 500
        ):
            json_integre.append("variable JavaScript contenant des données")
            break
    d["json_integre"] = sorted(set(json_integre))

    # --- Pagination
    suivante = trouver_page_suivante(sp, r.url)
    motifs = {
        m.group(0)
        for a in sp.find_all("a", href=True)
        for m in [_MOTIFS_PAGINATION.search(a["href"])]
        if m
    }
    d["page_suivante"] = suivante
    d["pagination"] = bool(suivante or len(motifs) >= 2)

    # --- Blocs répétés (fiches produits, articles, citations…)
    blocs = detecter_blocs_repetes(sp)
    d["blocs_repetes"] = f"{blocs['selecteur']} x{blocs['nombre']}" if blocs else ""

    # --- Arbre de décision
    if not ok:
        d["cas"].insert(0, "STOP — robots.txt")
        d["recommandation"] = (
            "Ne pas collecter. Chercher une API officielle ou un jeu de données ouvert."
        )
    elif r.status_code in (401, 402, 403) or d["protection"]:
        d["cas"].insert(0, "G — accès bloqué")
        d["recommandation"] = (
            "Ne pas contourner la protection. Chercher une API officielle, "
            "un jeu de données ouvert, ou demander l'autorisation au site."
        )
    elif r.status_code == 429 or r.status_code >= 500:
        d["cas"].insert(0, "G — erreur temporaire")
        d["recommandation"] = (
            "Réessayer plus tard avec obtenir() (pause + nouvelles tentatives)."
        )
    elif r.status_code != 200:
        d["cas"].insert(0, f"G — statut {r.status_code}")
        d["recommandation"] = (
            "Vérifier l'URL (redirection, page supprimée, paramètre manquant)."
        )
    else:
        if d["formulaire_connexion"]:
            d["cas"].append("F — formulaire de connexion")
        if signes_js or (json_integre and len(visible) < 1500):
            d["cas"].append("E — contenu JavaScript")
        if d["nb_tableaux"]:
            d["cas"].append("B — tableau(x) HTML")
        if d["pagination"]:
            d["cas"].append("C — pagination")
        if blocs and blocs.get("avec_liens"):
            d["cas"].append("D — liste vers pages détail (à vérifier)")
        if not d["charset_declare"] and (d["encodage_detecte"] or "").lower() not in (
            "ascii",
            "iso-8859-1",
            "windows-1252",
        ):
            d["cas"].append("H — encodage non déclaré")
        if not d["cas"]:
            d["cas"].append("A — HTML statique")
        principal = d["cas"][0][0]
        d["recommandation"] = {
            "F": "Se connecter avec une Session (cas F) — seulement avec votre propre compte et si les conditions d'utilisation le permettent.",
            "E": "Chercher d'abord les données JSON (page source, onglet Réseau) ; sinon Playwright (cas E2).",
            "B": "pandas.read_html sur le HTML téléchargé (cas B).",
            "C": "Boucle de pagination avec délai et condition d'arrêt (cas C).",
            "D": "Collecter la liste puis chaque page détail (cas D).",
            "H": "Corriger l'encodage avant d'extraire (cas H).",
            "A": "requests + BeautifulSoup avec sélecteurs CSS (cas A).",
        }[principal]
        if d["meta_robots"] and re.search(
            r"noindex|noai|noimageai", d["meta_robots"], re.I
        ):
            d["alertes"].append(
                f"meta robots = « {d['meta_robots']} » : l'éditeur limite la réutilisation"
            )
    if crawl_delay:
        d["alertes"].append(f"robots.txt demande un Crawl-delay de {crawl_delay} s")

    if afficher:
        afficher_diagnostic(d)
    return d


def afficher_diagnostic(d):
    ligne = "─" * 70
    print(ligne)
    print(f"DIAGNOSTIC : {d['url']}")
    print(ligne)
    champs = [
        ("robots.txt", d.get("robots_raison")),
        ("Statut HTTP", d.get("statut")),
        ("Type de contenu", d.get("content_type")),
        (
            "Encodage",
            f"déclaré={d.get('charset_declare')}  requests={d.get('encodage_requests')}  détecté={d.get('encodage_detecte')}",
        ),
        ("Taille", f"{d.get('taille_ko')} Ko en {d.get('duree_s')} s"),
        ("Serveur", d.get("serveur")),
        ("Titre", d.get("titre")),
        (
            "Texte visible",
            f"{d.get('longueur_texte_visible')} caractères, {d.get('nb_scripts')} scripts",
        ),
        ("Tableaux / liens", f"{d.get('nb_tableaux')} / {d.get('nb_liens')}"),
        ("Blocs répétés", d.get("blocs_repetes") or "aucun"),
        (
            "Pagination",
            d.get("page_suivante")
            or ("oui (paramètres d'URL)" if d.get("pagination") else "non"),
        ),
        (
            "Connexion",
            "formulaire avec mot de passe" if d.get("formulaire_connexion") else "non",
        ),
        ("Signes JS", ", ".join(d.get("signes_js", [])) or "aucun"),
        ("JSON intégré", ", ".join(d.get("json_integre", [])) or "aucun"),
        ("Protection", ", ".join(d.get("protection", [])) or "aucune détectée"),
    ]
    for nom, valeur in champs:
        if valeur not in (None, ""):
            print(f"  {nom:<17}: {valeur}")
    for a in d.get("alertes", []):
        print(f"  ⚠ {a}")
    print(f"\n  CAS DÉTECTÉS   : {' | '.join(d.get('cas', [])) or '—'}")
    print(f"  RECOMMANDATION : {d.get('recommandation')}")
    print(ligne)


# ---------------------------------------------------------------------------
# 3. Les cas
# ---------------------------------------------------------------------------
# Convention : chaque extracteur reçoit (soupe, url_de_la_page) et retourne
# une liste de dictionnaires (une ligne par élément collecté).

# --- Cas A : HTML statique ---------------------------------------------------


def extraire_citations(sp, url_page):
    lignes = []
    for bloc in sp.select("div.quote"):
        lien_auteur = bloc.select_one("a[href*='/author/']")
        lignes.append(
            {
                "texte": texte(bloc, "span.text").strip('“”"'),
                "auteur": texte(bloc, "small.author"),
                "etiquettes": ";".join(
                    a.get_text(strip=True) for a in bloc.select("a.tag")
                ),
                "url_auteur": (
                    urljoin(url_page, lien_auteur["href"]) if lien_auteur else ""
                ),
            }
        )
    return lignes


# --- Cas B : tableaux HTML ---------------------------------------------------


def extraire_tableaux(session, url, match=".+", attrs=None):
    """
    Télécharge la page AVEC notre session (User-Agent), puis laisse pandas
    lire les <table>. Passer par StringIO évite l'avertissement de pandas ≥ 2.1.
    """
    r = obtenir(session, url)
    if r is None:
        return []
    try:
        return pd.read_html(StringIO(r.text), match=match, attrs=attrs)
    except ValueError:  # « No tables found »
        return []


def nettoyer_tableau(df):
    """Aplatit les en-têtes multiples et retire les appels de note [1], [a]…"""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            " / ".join(dict.fromkeys(str(n) for n in col if "Unnamed" not in str(n)))
            for col in df.columns
        ]
    df.columns = [re.sub(r"\[[^\]]*\]", "", str(c)).strip() for c in df.columns]
    return df.replace(r"\[[^\]]*\]", "", regex=True)


# --- Cas C : pagination ------------------------------------------------------


def parcourir_pages(session, url_depart, extraire, max_pages=10):
    """
    Suit les liens « suivant » tant qu'il y en a.
    Conditions d'arrêt : plus de lien, page vide, page déjà vue, max_pages.
    """
    url, vues, resultats = url_depart, set(), []
    while url and len(vues) < max_pages:
        if url in vues:
            log.warning("Boucle détectée sur %s — arrêt", url)
            break
        vues.add(url)
        r = obtenir(session, url)
        if r is None:
            break
        sp = soupe(r)
        nouvelles = extraire(sp, r.url)
        if not nouvelles:
            log.info("Page sans données — arrêt")
            break
        resultats.extend(nouvelles)
        log.info(
            "Page %s : %s éléments (total %s)",
            len(vues),
            len(nouvelles),
            len(resultats),
        )
        url = trouver_page_suivante(sp, r.url)
    return resultats


def parcourir_par_parametre(session, modele_url, extraire, debut=1, max_pages=10):
    """Variante quand l'URL contient le numéro : https://site/liste?page={page}"""
    resultats = []
    for page in range(debut, debut + max_pages):
        r = obtenir(session, modele_url.format(page=page))
        if r is None:
            break
        nouvelles = extraire(soupe(r), r.url)
        if not nouvelles:
            break
        resultats.extend(nouvelles)
    return resultats


# --- Cas D : liste -> détail ------------------------------------------------

NOTES = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def extraire_livres_liste(sp, url_page):
    livres = []
    for art in sp.select("article.product_pod"):
        a = art.select_one("h3 a")
        classes = (
            art.select_one("p.star-rating")["class"]
            if art.select_one("p.star-rating")
            else []
        )
        note = next((NOTES[c] for c in classes if c in NOTES), None)
        livres.append(
            {
                "titre": a.get("title", a.get_text(strip=True)),
                "prix_texte": texte(art, "p.price_color"),
                "note": note,
                "url_detail": urljoin(url_page, a["href"]),
            }
        )
    return livres


def extraire_detail_livre(sp, url_page):
    infos = {
        texte(tr, "th"): texte(tr, "td") for tr in sp.select("table.table-striped tr")
    }
    fil = sp.select("ul.breadcrumb li a")
    return {
        "upc": infos.get("UPC", ""),
        "categorie": fil[-1].get_text(strip=True) if len(fil) >= 3 else "",
        "disponibilite": infos.get("Availability", texte(sp, "p.availability")),
        "nb_avis": infos.get("Number of reviews", ""),
        "description": texte(sp, "#product_description ~ p"),
    }


def collecter_liste_et_details(
    session, url_depart, extraire_liste, extraire_detail, max_pages=2, max_details=None
):
    """Niveau 1 : pages de liste. Niveau 2 : une requête par fiche détail."""
    lignes = parcourir_pages(session, url_depart, extraire_liste, max_pages=max_pages)
    for i, ligne in enumerate(lignes[:max_details] if max_details else lignes, 1):
        r = obtenir(session, ligne["url_detail"])
        if r is not None:
            ligne.update(extraire_detail(soupe(r), r.url))
        if i % 10 == 0:
            log.info("%s fiches détail collectées", i)
    return lignes


# --- Cas E : JavaScript ------------------------------------------------------


def extraire_json_integre(html, nom_variable=None):
    """
    E1a — Les données sont souvent déjà dans la page source :
      • <script id="__NEXT_DATA__"> (sites Next.js)
      • <script type="application/ld+json"> (schema.org)
      • var data = [...];   (variable JavaScript)
    Retourne une liste d'objets Python trouvés.
    """
    sp = soupe(html)
    trouves = []
    for s in sp.select(
        "script#__NEXT_DATA__, script[type='application/json'], script[type='application/ld+json']"
    ):
        try:
            trouves.append(json.loads(s.string or ""))
        except json.JSONDecodeError:
            pass
    nom = re.escape(nom_variable) if nom_variable else r"\w+"
    for m in re.finditer(
        rf"(?:var|let|const)\s+{nom}\s*=\s*(\[.*?\]|\{{.*?\}})\s*;", html, re.S
    ):
        brut = re.sub(
            r",\s*([\]}])", r"\1", m.group(1)
        )  # virgules finales tolérées par JS, pas par JSON
        try:
            trouves.append(json.loads(brut))
        except json.JSONDecodeError:
            pass
    return trouves


def collecter_api_cachee(session, modele_url, cle_liste, cle_suite=None, max_pages=10):
    """
    E1b — L'onglet Réseau (F12 > Réseau > Fetch/XHR) révèle l'URL qu'appelle la page.
    On appelle directement cette API JSON, page par page.
    modele_url : ex. "https://quotes.toscrape.com/api/quotes?page={page}"
    """
    resultats = []
    for page in range(1, max_pages + 1):
        r = obtenir(
            session,
            modele_url.format(page=page),
            headers={"Accept": "application/json"},
        )
        if r is None:
            break
        donnees = r.json()
        elements = donnees.get(cle_liste, [])
        if not elements:
            break
        resultats.extend(elements)
        if cle_suite and not donnees.get(cle_suite):
            break
    return resultats


def rendre_page(url, attendre=None, defilements=0, pause_ms=1000):
    """
    E2 — Dernier recours : un vrai navigateur sans interface exécute le JavaScript.
    À utiliser dans un script .py. Dans Jupyter, utiliser rendre_page_async.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        navigateur = p.chromium.launch(headless=True)
        page = navigateur.new_page(user_agent=USER_AGENT)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if attendre:
            page.wait_for_selector(attendre, timeout=15000)
        for _ in range(defilements):
            page.mouse.wheel(0, 20000)
            page.wait_for_timeout(pause_ms)
        html = page.content()
        navigateur.close()
    return html


async def rendre_page_async(url, attendre=None, defilements=0, pause_ms=1000):
    """Version Jupyter :  html = await rendre_page_async(url, attendre='div.quote')"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        navigateur = await p.chromium.launch(headless=True)
        page = await navigateur.new_page(user_agent=USER_AGENT)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if attendre:
            await page.wait_for_selector(attendre, timeout=15000)
        for _ in range(defilements):
            await page.mouse.wheel(0, 20000)
            await page.wait_for_timeout(pause_ms)
        html = await page.content()
        await navigateur.close()
    return html


# --- Cas F : connexion -------------------------------------------------------


def se_connecter(session, url_connexion, identifiants):
    """
    1) GET du formulaire : on récupère TOUS les champs, y compris cachés (jeton CSRF)
    2) on remplit identifiant / mot de passe
    3) POST avec la même session : les cookies de connexion y restent
    identifiants : ex. {"username": "...", "password": "..."}
    """
    r = obtenir(session, url_connexion)
    sp = soupe(r)
    champ_mdp = sp.select_one("input[type=password]")
    formulaire = champ_mdp.find_parent("form") if champ_mdp else sp.find("form")
    donnees = {
        i["name"]: i.get("value", "")
        for i in formulaire.select("input[name]")
        if i.get("type") not in ("submit", "button", "checkbox")
    }
    donnees.update(identifiants)
    cible = urljoin(r.url, formulaire.get("action") or r.url)
    reponse = session.post(cible, data=donnees, timeout=TIMEOUT)
    time.sleep(DELAI)
    connecte = bool(
        re.search(r"logout|log out|déconnexion|se déconnecter", reponse.text, re.I)
    )
    log.info(
        "Connexion %s (jeton caché envoyé : %s)",
        "réussie" if connecte else "ÉCHOUÉE",
        [k for k in donnees if "csrf" in k.lower() or "token" in k.lower()],
    )
    return connecte, reponse


# --- Cas H : nettoyage -------------------------------------------------------


def nettoyer_chaine(s):
    """Normalise Unicode, remplace les espaces insécables, réduit les blancs."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    return re.sub(r"\s+", " ", s).strip()


def prix_en_nombre(s):
    """
    '£51.77' -> 51.77   '1 234,56 $' -> 1234.56   '$1,299.00' -> 1299.0
    Retourne None si aucun nombre n'est trouvé.
    """
    if not s:
        return None
    t = re.sub(r"[^\d,.\-]", "", nettoyer_chaine(s).replace(" ", ""))
    if not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        t = (
            t.replace(".", "").replace(",", ".")
            if t.rfind(",") > t.rfind(".")
            else t.replace(",", "")
        )
    elif "," in t:
        t = (
            t.replace(",", ".")
            if len(t.split(",")[-1]) in (1, 2)
            else t.replace(",", "")
        )
    try:
        return float(t)
    except ValueError:
        return None


def entier_dans(s):
    """'In stock (22 available)' -> 22"""
    m = re.search(r"\d[\d\s,]*", s or "")
    return int(re.sub(r"\D", "", m.group(0))) if m else None


# ---------------------------------------------------------------------------
# 4. Outils génériques
# ---------------------------------------------------------------------------

_IGNORER = {
    "html",
    "body",
    "head",
    "script",
    "style",
    "svg",
    "path",
    "i",
    "span",
    "a",
    "img",
    "br",
    "strong",
    "em",
    "b",
    "small",
    "option",
    "td",
    "th",
    "tr",
    "meta",
    "link",
}


def detecter_blocs_repetes(sp, min_repetitions=4):
    """
    Heuristique : les fiches d'une liste (produits, articles, citations) sont des
    éléments qui partagent la même balise + classe et contiennent du texte.
    Retourne {'selecteur', 'nombre', 'avec_liens'} ou None.
    """
    groupes = {}
    for el in sp.find_all(True, class_=True):
        if el.name in _IGNORER:
            continue
        cle = el.name + "." + ".".join(sorted(el.get("class", [])))
        groupes.setdefault(cle, []).append(el)

    meilleur, meilleur_score = None, 0
    for cle, elements in groupes.items():
        n = len(elements)
        if n < min_repetitions:
            continue
        longueurs = sorted(len(e.get_text(" ", strip=True)) for e in elements)
        mediane = longueurs[n // 2]
        if not 20 <= mediane <= 3000:
            continue
        score = n * min(mediane, 400)
        if score > meilleur_score:
            avec_liens = sum(1 for e in elements if e.find("a", href=True)) >= n * 0.8
            meilleur, meilleur_score = {
                "selecteur": cle,
                "nombre": n,
                "avec_liens": avec_liens,
            }, score
    return meilleur


def extraire_blocs_generiques(sp, url_page, selecteur):
    """Une ligne par bloc répété : texte + premier lien + première image."""
    lignes = []
    for el in sp.select(selecteur):
        lien = el.find("a", href=True)
        img = el.find("img", src=True)
        lignes.append(
            {
                "texte": nettoyer_chaine(el.get_text(" | ", strip=True))[:1000],
                "lien": urljoin(url_page, lien["href"]) if lien else "",
                "image": urljoin(url_page, img["src"]) if img else "",
            }
        )
    return lignes


def sauvegarder_csv(lignes, chemin):
    """Écrit une liste de dictionnaires en CSV UTF-8 (lisible dans Excel)."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(lignes, pd.DataFrame):
        lignes.to_csv(chemin, index=False, encoding="utf-8-sig")
        n = len(lignes)
    else:
        pd.DataFrame(lignes).to_csv(chemin, index=False, encoding="utf-8-sig")
        n = len(lignes)
    log.info("%s lignes -> %s", n, chemin)
    return chemin
