"""
etape3_nettoyage.py
Projet de groupe : vente et réparation de vélos en Outaouais.
Étape 3 : refait le nettoyage de resultats/velos_outaouais.csv
sans contacter aucun site (même règles que nettoyer_velos() de l'étape 2).

Utile quand on améliore une règle de nettoyage après la collecte :
on ne retélécharge pas les pages.

Lancer : python etape3_nettoyage.py
"""

import pandas as pd

import etape2_collecte as e2
import projet_scraping as ws


def main():
    df = pd.read_csv(e2.FICHIER_VELOS, encoding="utf-8-sig")
    avant = len(df)
    vides_avant = df["marque"].isna().sum()
    df = e2.nettoyer_velos(df)
    ws.sauvegarder_csv(df, e2.FICHIER_VELOS)

    retirees = avant - len(df)
    print(f"Vélos : {avant} avant, {len(df)} après ({retirees} lignes retirées)")
    print(df["entreprise"].value_counts().to_string())
    print(f"Marques vides : {vides_avant} avant, {(df['marque'] == '').sum()} après")
    e2.noter(
        "",
        "",
        "nettoyage",
        "etape3_nettoyage.py, sans aucune requête",
        len(df),
        str(e2.FICHIER_VELOS),
        f"{retirees} lignes retirées ; marques complétées ; « - » final retiré",
    )


if __name__ == "__main__":
    main()
