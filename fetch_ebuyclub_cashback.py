#!/usr/bin/env python3
"""
Récupère le catalogue "CashBack en ligne" public d'eBuyClub (ebuyclub.com/cashback),
distinct du catalogue "Bon d'achat" déjà couvert par fetch_ebuyclub.py (ebuyclub.com/bons-d-achat).

Mécanisme confirmé le 2026-09-18 : contrairement au catalogue "Bon d'achat" (on achète un
bon/une carte, prix plein ou remisé, cf. usage_location "UNIQUEMENT EN MAGASIN"), ce
catalogue "CashBack en ligne" ne nécessite l'achat d'aucune carte : on paie directement sur
le site du marchand via un lien tracké, et le pourcentage est recrédité en cashback sur le
solde eBuyClub. Décision actée avec l'utilisatrice : ce mécanisme (aucune carte à acheter,
même si l'argent revient différé plutôt qu'instantanément) est classé type="direct" sur le
site, jamais "carte_cadeau" (réservé aux offres nécessitant l'achat effectif d'un bon/carte).
Même règle appliquée à iGraal (fetch_igraal.py) et déjà en place pour Banque Populaire/The
Corner (voir claude/suivi-projet-comparateur-reductions.md).

Source technique : endpoint HTML interne (pas de JSON), utilisé par le composant JS de la
page annuaire. Chaque marchand est une ligne <tr data-id data-partnername data-cashbackname
data-cashbacktype data-cashbackcategory>. data-cashbacktype vaut soit "En ligne" (aucune
carte, ce script) soit "Magasin" (bon d'achat, déjà couvert ailleurs). Un seul appel avec
un "size" volontairement énorme (10000) suffit à tout récupérer en une fois (4271 lignes
pour ~4508 marchands annoncés ; l'écart vient de quelques doublons de data-id, dédupliqués
ci-dessous) — pas de pagination réelle nécessaire, confirmé le 2026-09-18 via inspection
directe du DOM dans un navigateur (le réseau sandbox de cet environnement Claude n'a pas
accès à ebuyclub.com, comme pour iGraal/ReducFactory ; seul GitHub Actions peut exécuter ce
script en production).

Règle de qualité du projet : on exclut les marchands "Pas de CashBack en ce moment" (environ
2737 sur 4271 au 2026-09-18 — largement majoritaires, le catalogue affiché liste TOUS les
partenaires même inactifs).
"""

import json
import re
import sys
import urllib.request
from html import unescape

ANNUAIRE_URL = (
    "https://www.ebuyclub.com/ajax/annuaire"
    "?reset&sizeTable=50&size=10000&colonne=1&ordre=6&lettre=&triBase=5"
    "&idRubOrCat=&rubOrCategorie=&idMoLangue="
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

ROW_RE = re.compile(
    r'<tr\s+data-id="(?P<id>[^"]*)"\s+data-partnername="(?P<name>[^"]*)"'
    r'\s+data-cashbackname="(?P<cashbackname>[^"]*)"'
    r'\s+data-cashbacktype="(?P<cashbacktype>[^"]*)"'
    r'\s+data-cashbackcategory="(?P<category>[^"]*)"',
    re.IGNORECASE,
)

HREF_RE = re.compile(r'href="([^"]*reduction-[^"]*)"')

# "Jusqu'à 12,5% remboursés" / "12,5% remboursés" / "Jusqu'à 3€ remboursés" / "3€ remboursés"
RATE_RE = re.compile(
    r"(?P<jusqua>Jusqu.\s?à\s+)?(?P<value>[\d]+(?:[.,]\d+)?)\s?(?P<unit>%|€)\s*rembours",
    re.IGNORECASE,
)


def fetch_raw_html(url: str = ANNUAIRE_URL) -> str:
    # Referer obligatoire (confirme le 2026-09-19 via tests navigateur en
    # direct) : cet endpoint ajax renvoie HTTP 404 sans le header Referer
    # pointant vers la page annuaire/cashback qui l'appelle normalement.
    # C'est la cause racine du run GitHub Actions du 2026-09-18 qui n'a
    # silencieusement rien mis a jour (continue-on-error masquait l'echec) -
    # voir doc de suivi du projet.
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.ebuyclub.com/cashback",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_offers(raw_html: str) -> list:
    offers = []
    seen_ids = set()
    for row_match in ROW_RE.finditer(raw_html):
        row_id = row_match.group("id")
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)

        cashback_type = unescape(row_match.group("cashbacktype")).strip()
        if cashback_type != "En ligne":
            continue  # "Magasin" = bon d'achat, déjà couvert par fetch_ebuyclub.py

        cashback_name = unescape(row_match.group("cashbackname")).strip()
        if not cashback_name or "pas de cashback" in cashback_name.lower():
            continue  # règle qualité du projet : pas d'offre inactive

        name = unescape(row_match.group("name")).strip()
        if not name:
            continue

        category = unescape(row_match.group("category")).strip() or None

        # lien de la fiche marchand, cherché dans le HTML de la ligne (jusqu'à la ligne suivante)
        row_end = row_match.end()
        next_row = raw_html.find("<tr data-id=", row_end)
        row_html = raw_html[row_end : next_row if next_row != -1 else row_end + 2000]
        href_match = HREF_RE.search(row_html)
        link = href_match.group(1) if href_match else None
        if link and link.startswith("/"):
            link = "https://www.ebuyclub.com" + link

        rate_match = RATE_RE.search(cashback_name)
        percent = None
        if rate_match and rate_match.group("unit") == "%" and not rate_match.group("jusqua"):
            # Taux garanti (pas de "jusqu'à") : seul cas où on retient un percent numérique
            # sûr, même convention que fetch_igraal.py (TEMU etc. laissent percent=null
            # quand le taux affiché est variable/plafonné "jusqu'à").
            try:
                percent = float(rate_match.group("value").replace(",", "."))
            except ValueError:
                percent = None

        offers.append(
            {
                "name": name,
                "slug": row_id,
                "rate_text": cashback_name,
                "percent": percent,
                "type_detail_fr": "Cashback en ligne",
                "type": "direct",
                "category_seen": category,
                "link": link,
            }
        )
    return offers


def main():
    if len(sys.argv) > 1:
        # Mode test/local : lire depuis un fichier HTML déjà téléchargé (le réseau de cet
        # environnement peut ne pas avoir accès à ebuyclub.com ; GitHub Actions, lui, l'a).
        with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as f:
            raw_html = f.read()
    else:
        raw_html = fetch_raw_html()

    offers = parse_offers(raw_html)

    # Garde-fou qualite (ajoute le 2026-09-19, suite au bug du Referer
    # manquant qui faisait echouer silencieusement le run automatique) :
    # le catalogue en compte plusieurs centaines en temps normal (1534 lors
    # de la premiere capture le 2026-09-18) ; un chiffre nettement plus bas
    # signale presque surement une reponse d'erreur (404, page de
    # connexion...) plutot qu'un vrai catalogue, a ne jamais publier tel
    # quel.
    if len(offers) < 200:
        raise SystemExit(
            f"Seulement {len(offers)} offres eBuyClub (cashback en ligne) "
            "trouvees (attendu > 200) - la reponse recue n'est "
            "probablement pas le bon catalogue (404, page de connexion...), "
            "a diagnostiquer avant de publier un fichier incomplet."
        )

    offers.sort(key=lambda o: o["name"].lower())

    from datetime import datetime, timezone

    output = {
        "platform": "eBuyClub",
        # Lien de secours affiche au clic sur le nom de l'enseigne quand aucune
        # offre n'a de lien precis (demande de l'utilisatrice le 2026-09-24).
        "platform_url": "https://www.ebuyclub.com/cashback",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Catalogue public ebuyclub.com/cashback, filtre data-cashbacktype='En ligne' "
            "uniquement (aucune carte a acheter, cashback credite sur le solde eBuyClub "
            "apres achat direct chez le marchand). Distinct de ebuyclub.json (catalogue "
            "'Bon d'achat', data-cashbacktype='Magasin', achat reel d'un bon/carte). "
            "Offres 'Pas de CashBack en ce moment' exclues."
        ),
        "offers": offers,
    }

    with open("ebuyclub_cashback.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"{len(offers)} offres eBuyClub (cashback en ligne) écrites dans ebuyclub_cashback.json")


if __name__ == "__main__":
    main()
