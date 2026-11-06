# Web scraping : vélos et réparation de vélos en Outaouais

Projet de groupe du cours **Collecte et nettoyage des données**.

Le projet recueille les vélos à vendre et les tarifs d'atelier (réparation, mise au point, location) de commerces de l'Outaouais. Les données sont publiques. La collecte suit une démarche responsable : on diagnostique chaque site d'abord, puis on collecte, puis on nettoie.

---

## 1. Sites étudiés

| Entreprise | Données collectées | Cas du cours | Particularité |
|---|---|---|---|
| Vélozophie | Vélos (24 catégories) | A | `Crawl-delay: 10` ; pagination en JavaScript, donc non suivie |
| Primeau Vélo | Vélos (page 1 de chaque sous-catégorie) | A | `Crawl-delay: 10` ; `robots.txt` interdit les URL avec paramètres (`?p=2`) |
| Expéditions Wakefield | Vélos, forfaits d'atelier, location | A, B, C | Pagination `/page/2/` ; tarifs de location dans des tableaux HTML |
| SAP Outaouais | Vélos, atelier vélo | A, C, H | Pagination `?page=2` ; encodage non déclaré, corrigé à la lecture |

**Sites exclus** (jamais contactés par les scripts de collecte) : Polo-Vélo (conditions d'utilisation), Vélo la Shop (aucune donnée), Cycle Bertrand, Rack à Bécik, Quantum eBikes, Pace E-bike, Centre Vorlage (interdits par `robots.txt`).

**Donnée non collectée** : les tarifs d'entretien de Vélozophie sont seulement dans une image et un PDF. Le journal le mentionne.

### Rappel des cas du cours

| Cas | Situation | Technique |
|---|---|---|
| A | HTML statique | `requests` + `BeautifulSoup` et sélecteurs CSS |
| B | Tableaux HTML | `pandas.read_html` |
| C | Pagination | Boucle « page suivante » avec un délai et une condition d'arrêt |
| G | Requêtes robustes | Respect de `robots.txt`, délais, reprises en cas d'erreur |
| H | Encodage et texte | Correction de l'encodage, nettoyage des chaînes |

---

## 2. Organisation des fichiers

```
UA2_1/
├── projet_scraping.py      Boîte à outils du cours (session, robots.txt, obtenir(),
│                           diagnostiquer(), parcourir_pages(), nettoyage, export CSV)
├── scraper.py              Pipeline générique : diagnostic -> extraction -> journal
├── etape1_diagnostic.py    Étape 1 : diagnostic des pages avant la collecte
├── etape2_collecte.py      Étape 2 : collecte des vélos et des tarifs (4 sites)
├── etape3_nettoyage.py     Étape 3 : refait le nettoyage sans contacter les sites
├── requirements.txt        Versions des bibliothèques
└── resultats/
    ├── diagnostics.json        Rapports détaillés de l'étape 1
    ├── pages_html/             Copies locales des pages diagnostiquées
    ├── velos_outaouais.csv     Un vélo par ligne
    ├── atelier_outaouais.csv   Un service d'atelier ou un tarif de location par ligne
    └── journal_tests.csv       Journal de bord : une ligne par page ou source traitée
```

---

## 3. Installation

Il faut Python 3.13 ou plus récent. Le script vérifie que `robotparser` respecte la norme RFC 9309 et s'arrête si Python est trop ancien.

```powershell
# Créer et activer l'environnement virtuel (Windows)
python -m venv .venv
.venv\Scripts\activate

# Installer les dépendances
pip install -r requirements.txt
```

Principales bibliothèques : `requests`, `beautifulsoup4`, `lxml`, `pandas`.

**Avant la première exécution**, ouvrez `projet_scraping.py` et mettez une adresse de contact réelle dans `USER_AGENT`. Le script refuse de démarrer si l'adresse contient encore `exemple.ca`.

---

## 4. Exécution

Lancez les scripts **depuis le dossier du projet**, dans l'ordre :

```powershell
python etape1_diagnostic.py   # diagnostic : robots.txt, type de page, copie HTML
python etape2_collecte.py     # collecte : environ 10 minutes
python etape3_nettoyage.py    # facultatif : nettoie de nouveau le CSV des vélos
```

### Étape 1 : diagnostic (`etape1_diagnostic.py`)
Pour chaque page étudiée, le script :
- vérifie que `robots.txt` autorise l'accès ;
- détecte le cas du cours (A, B, C, H...) ;
- garde une copie HTML dans `resultats/pages_html/` ;
- ajoute une ligne au journal.

Les sites exclus sont inscrits au journal avec la raison de l'exclusion.

### Étape 2 : collecte (`etape2_collecte.py`)
Chaque site a deux fonctions :
- `extraire_...(sp, url_page)` lit les cartes produits d'une page et renvoie une liste de dictionnaires ;
- `collecter_...(session)` télécharge les pages (en respectant les délais), appelle l'extracteur et renvoie `(lignes, remarques)`.

La liste `SOURCES` réunit toutes les collectes. La fonction `main()` les exécute l'une après l'autre. Si un site provoque une erreur, l'erreur est notée au journal et le script passe au site suivant. À la fin, `sauvegarder()` nettoie les données et écrit les deux CSV.

Le script prend environ 10 minutes parce qu'il attend 10 secondes entre deux requêtes chez Vélozophie et Primeau Vélo, comme le demande leur `Crawl-delay`.

### Étape 3 : nettoyage (`etape3_nettoyage.py`)
Le script relit `velos_outaouais.csv` et applique de nouveau `nettoyer_velos()`, **sans aucune requête réseau**. Utilisez-le après avoir amélioré une règle de nettoyage : vous n'avez pas à refaire la collecte.

---

## 5. Données produites

### `velos_outaouais.csv`

| Colonne | Description |
|---|---|
| `entreprise` | Nom du commerce |
| `categorie` | Catégorie du site (ex. « Montagne / Hardtail ») |
| `marque` | Marque normalisée (liste `MARQUES`), ou celle du site |
| `nom` | Nom du modèle |
| `prix` | Prix actuel (nombre) |
| `prix_regulier` | Prix barré, s'il existe |
| `en_solde` | `True` si `prix_regulier` > `prix` |
| `etat` | `occasion`, `démo` ou vide (neuf) |
| `disponibilite` | Stock indiqué par le site |
| `remarque` | Ex. « prix à partir de », « fourchette de prix » |
| `url` | Page du produit |
| `date_collecte` | Date de la collecte (AAAA-MM-JJ) |

### `atelier_outaouais.csv`

| Colonne | Description |
|---|---|
| `entreprise` | Nom du commerce |
| `type` | `atelier` ou `location` |
| `service` | Nom du service ou du vélo loué |
| `duree` | Durée de location (ex. « 1 jour »), vide pour l'atelier |
| `prix_min`, `prix_max` | Bornes du prix (égales si le prix est unique) |
| `prix_texte` | Prix tel qu'affiché (ex. « Prix à confirmer ») |
| `description` | Détails du forfait |
| `url` | Page source |
| `date_collecte` | Date de la collecte |

### `journal_tests.csv`
Une ligne par page diagnostiquée, source collectée ou nettoyage. Colonnes : `date`, `url`, `statut`, `robots`, `cas`, `recommandation`, `nb_lignes`, `fichiers`, `remarques`.

---

## 6. Règles de nettoyage (`nettoyer_velos`)

1. Nettoyage du texte : espaces en trop et caractères invisibles (cas H).
2. Suppression du tiret final dans les noms (« Felt VR 60 - » devient « Felt VR 60 »).
3. Normalisation des marques à partir du nom. Les marques de plusieurs mots sont cherchées en premier, puis les mots courts (EVO, Neo).
4. Exclusion des roues vendues dans la catégorie vélos de SAP.
5. Suppression des doublons (même entreprise et même URL). La première catégorie trouvée est gardée : c'est la plus précise.
6. Conversion des prix en nombres et calcul de `en_solde`.
7. Tri par entreprise, catégorie et prix.

---

## 7. Bonnes pratiques respectées

- `robots.txt` est vérifié avant chaque requête, par `ws.obtenir()`.
- Le `Crawl-delay` est respecté, avec au moins 1 seconde entre deux requêtes. Le délai de Vélozophie est fixé à la main, car `robotparser` ne lit pas sa ligne `Crawl-delay`.
- Le `User-Agent` est honnête et contient une adresse de contact.
- Le script ne contourne jamais une interdiction : par exemple, il ne lit pas la page `?p=2` chez Primeau Vélo.
- Les sites dont les conditions d'utilisation interdisent le scraping sont exclus.
- Chaque décision est documentée dans `journal_tests.csv`, y compris les données non collectées.

---

## 8. Limites connues

- **Vélozophie** : les catégories comptent au plus 60 vélos par page. Les vélos au-delà de la première page de chaque catégorie peuvent manquer.
- **Primeau Vélo** : seulement les 12 premiers vélos de chaque sous-catégorie.
- Les sélecteurs CSS dépendent de la structure actuelle des sites. Si un site change son HTML, il faudra mettre à jour la fonction `extraire_...()` correspondante.
