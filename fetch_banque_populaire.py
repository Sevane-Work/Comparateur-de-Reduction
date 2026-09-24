#!/usr/bin/env python3
"""
fetch_banque_populaire.py

Récupère le catalogue public de l'espace avantages Banque Populaire
(Extra+X, avantages-partenaires.banquepopulaire.fr), qui s'appuie en
réalité sur une API tierce publique, ReducFactory, et génère
banque_populaire.json avec le même schéma plat "offers" que igraal.json /
ebuyclub.json / thecorner_offers.json / ulys_offers.json.

AUCUNE CONNEXION PERSONNELLE N'EST NECESSAIRE : cette API a été trouvée et
vérifiée publique (aucun cookie ni jeton) le 2026-09-15, et sa structure
JSON réelle a été confirmée via navigateur le 2026-09-16 (voir le doc de
suivi du projet, section "Détails techniques — API Banque Populaire").

Structure de l'API (API Platform / JSON-LD) :
- Collection paginée : {"member": [...produits...], "view": {"next": "..."}}
- Chaque produit a un champ "type" :
  - "voucher_product" (bon d'achat / carte cadeau, payé directement au prix
    remisé, confirmé par l'utilisateur) : le taux est dans
    offers[0].marginAmount (pourcentage).
  - "cashback_product" / "direct_discount_product" (réduction directe) :
    le taux est un texte libre dans content.fields[], sur le champ dont
    slug == "texte-de-reduction-liste" (ex. "-4%", "-9%"), à parser.
- Le nom "propre" du marchand est referentialProduct.brand.name (plus
  fiable que referentialProduct.name, qui contient parfois un suffixe
  interne comme "Nike (tds)" ou "H&M (tillo)").

Dépendances : uniquement la bibliothèque standard (urllib.request pour le
HTTP), comme fetch_fnac_darty.py / fetch_igraal.py / fetch_ebuyclub.py. Pas
de requirements.txt nécessaire.

Bug corrigé le 2026-09-17 (soir), signalé par l'utilisatrice (Pathé absent des résultats) : les bons d'achat à MONTANT FIXE (type "fixed_amount", ex. billets de cinéma Pathé/CGR/UGC/MK2/Cinéchèque, mais aussi de nombreux parcs/zoos/musées et enseignes retail) étaient entièrement ignorés - le script ne savait lire que le champ marginAmount, propre aux bons à MONTANT VARIABLE ("variable_amount"). Pour un bon à montant fixe, le taux réel se déduit de (facialAmount - saleAmount) / facialAmount. Les deux types sont désormais gérés, en ne retenant que les sous-offres "available": true ET "published": true (certaines sous-offres restent présentes dans l'API mais non publiées - à ne pas afficher).

Usage :
    python fetch_banque_populaire.py [--output banque_populaire.json]
"""

import argparse
import html
import json
import re
import sys
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TENANT_BASE = "https://api.reducfactory.com/1.0/1f01ad95-2d7b-6062-bdf1-2137c7cc0407/"
PLATFORM = "Banque Populaire (Extra+X)"

HEADERS = {
    "accept": "application/json",
    "accept-language": "fr-FR,fr;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
}

PERCENT_RE = re.compile(r"([\d.,]+)\s*%")


def fetch_json(url):
    req = Request(url, headers=HEADERS, method="GET")
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        print(f"  [!] HTTP {e.code} sur {url}", file=sys.stderr)
        return None
    except URLError as e:
        print(f"  [!] Erreur reseau sur {url} : {e.reason}", file=sys.stderr)
        return None


def merchant_name_and_slug(product):
    ref = product.get("referentialProduct") or {}
    brand = ref.get("brand") or {}
    name = brand.get("name") or ref.get("name") or product.get("slug", "")
    slug = brand.get("slug") or product.get("slug", "")
    return name, slug


def extract_offer(product):
    """Renvoie un dict d'offre (schéma plat) ou None si pas de taux exploitable."""
    ptype = product.get("type")
    name, slug = merchant_name_and_slug(product)
    if not name:
        return None

    if ptype == "voucher_product":
        best_percent = None
        for off in product.get("offers") or []:
            # Sous-offres non publiees ou indisponibles : ne jamais les
            # afficher, meme si elles ont un taux exploitable (corrige le
            # 2026-09-17 soir, cas Pathe ou seules 2 des 5 sous-offres
            # etaient reellement publiees).
            if off.get("available") is not True or off.get("published") is not True:
                continue
            percent = None
            off_type = off.get("type")
            if off_type == "fixed_amount":
                sale = off.get("saleAmount")
                facial = off.get("facialAmount")
                if isinstance(sale, (int, float)) and isinstance(facial, (int, float)) and facial > 0:
                    percent = (facial - sale) / facial * 100.0
            else:
                margin = off.get("marginAmount")
                if isinstance(margin, (int, float)) and margin > 0:
                    percent = float(margin)
            if percent is not None and percent > 0:
                if best_percent is None or percent > best_percent:
                    best_percent = percent
        if best_percent is None:
            # Aucune sous-offre publiee/disponible avec un taux exploitable
            # (regle projet : pas d'offre sans reduction reelle).
            return None
        rounded = round(best_percent, 1)
        return {
            "name": name,
            "slug": slug,
            "rate_text": f"{rounded:g}%",
            "percent": rounded,
            "type_detail_fr": "Bon d'achat / carte cadeau (prix remisé payé directement)",
            "type": "carte_cadeau",
            "category_seen": None,
        }

    if ptype in ("cashback_product", "direct_discount_product"):
        fields = (product.get("content") or {}).get("fields") or []
        rate_text = None
        for f in fields:
            if f.get("slug") == "texte-de-reduction-liste":
                rate_text = f.get("content")
                break
        if not rate_text:
            return None
        # Le champ peut contenir du HTML brut (balises <span>, entites HTML
        # non decodees comme &#39; ou &agrave;) : on nettoie avant affichage.
        clean_text = html.unescape(re.sub(r"<[^>]+>", "", rate_text)).strip()
        m = PERCENT_RE.search(clean_text)
        percent = float(m.group(1).replace(",", ".")) if m else None
        # "cashback_product" cumule sur une cagnotte a retirer plus tard,
        # mais AUCUNE carte/bon n'est achete (reduction sur un achat fait
        # directement chez le marchand) : classe "direct", comme
        # "direct_discount_product" - seul un mecanisme necessitant l'achat
        # effectif d'une carte/d'un bon est "carte_cadeau" (corrige le
        # 2026-09-18, memes conventions que iGraal - voir doc de suivi).
        site_type = "direct"
        return {
            "name": name,
            "slug": slug,
            "rate_text": clean_text,
            "percent": percent,
            "type_detail_fr": "Cashback" if ptype == "cashback_product" else "Réduction directe",
            "type": site_type,
            "category_seen": None,
        }

    return None


import re as _re

CATEGORY_PATTERNS = [
    ("cinema", _re.compile(r"mk2|gaumont|path[ée]|\bugc\b|cin[ée]|\bcgr\b|kinepolis", _re.IGNORECASE)),
]


def tag_category(offers):
    """Ajoute un champ 'category' (ex. 'cinema') aux offres dont le nom
    correspond a une categorie loisirs connue - voir la vue "Loisirs" du
    site et le doc de suivi de projet (2026-09-17).

    Decision produit (utilisatrice, 2026-09-19) : dans l'onglet "Loisirs",
    on ne parle pas de "carte cadeau" mais de billet/pass a prix reduit -
    meme quand le mecanisme technique reel est un achat de bon a l'avance
    (ce qui, selon la regle generale du site, vaudrait carte_cadeau). Toute
    offre taguee d'une categorie loisirs est donc forcee en type="direct",
    y compris quand elle apparait hors de l'onglet Loisirs (ex. recherche
    "Pathe" dans l'onglet Magasins) : c'est la meme offre, la categorie ne
    depend pas de l'onglet affiche. Exception volontaire a la regle
    generale carte_cadeau/direct, reservee aux offres category-taguees."""
    for offer in offers:
        for category, pattern in CATEGORY_PATTERNS:
            if pattern.search(offer.get("name", "")):
                offer["category"] = category
                offer["type"] = "direct"
                break
    return offers


def fetch_all_products():
    """Parcourt toute la collection paginée et renvoie la liste brute des produits.

    L'API ReducFactory (API Platform) peut répondre sous deux formes selon
    la négociation de contenu : soit une simple liste JSON, soit une
    collection JSON-LD/Hydra enveloppée ({"member"/"hydra:member": [...],
    "view"/"hydra:view": {"next"/"hydra:next": "..."}}). On gère les deux.

    Bug corrigé le 2026-09-17 (soir), signalé par l'utilisateur (Carrefour
    absent des résultats malgré une offre réelle -5% "Bon d'achat" sur le
    site) : en réponse "plate" (liste JSON simple), l'API PLAFONNE en
    réalité à 100 produits par page quel que soit itemsPerPage demandé
    (confirmé : itemsPerPage=300 renvoie quand même 100 items sur 127
    réels) - l'ancien code prenait cette page tronquée pour le catalogue
    complet et s'arrêtait après la 1ère page, faisant disparaître ~27
    produits (dont Carrefour) de tous les runs automatisés. On boucle
    désormais sur page=1,2,3... et on s'arrête seulement quand une page
    renvoie moins de 100 résultats (page finale) ou est vide.
    """
    products = []
    seen_urls = set()

    page = 1
    while True:
        url = f"{TENANT_BASE}products?page={page}&itemsPerPage=300"
        if url in seen_urls or page > 20:  # garde-fou anti-boucle infinie
            break
        seen_urls.add(url)
        data = fetch_json(url)
        if data is None:
            break
        if isinstance(data, list):
            if not data:
                break
            products.extend(data)
            if len(data) < 100:
                break  # dernière page (page pleine = 100, comme observé)
            page += 1
            continue
        # Forme JSON-LD/Hydra : suit l'URL "next" fournie par l'API.
        member = data.get("member") or data.get("hydra:member") or []
        products.extend(member)
        view = data.get("view") or data.get("hydra:view") or {}
        next_path = view.get("next") or view.get("hydra:next")
        if not next_path:
            break
        next_url = "https://api.reducfactory.com" + next_path
        if next_url in seen_urls:
            break
        seen_urls.add(next_url)
        data2 = fetch_json(next_url)
        if data2 is None:
            break
        member2 = data2.get("member") or data2.get("hydra:member") or []
        products.extend(member2)
        break

    return products


def main():
    argp = argparse.ArgumentParser()
    argp.add_argument("--output", default="banque_populaire.json")
    args = argp.parse_args()

    products = fetch_all_products()
    print(f"{len(products)} produits recuperes depuis l'API ReducFactory", file=sys.stderr)

    offers = []
    seen_slugs = set()
    for product in products:
        offer = extract_offer(product)
        if offer is None:
            continue
        key = (product.get("type"), offer["slug"])
        if key in seen_slugs:
            continue
        seen_slugs.add(key)
        offers.append(offer)

    offers = tag_category(offers)

    result = {
        "platform": PLATFORM,
        # Lien de secours affiche au clic sur le nom de l'enseigne quand aucune
        # offre n'a de lien precis (demande de l'utilisatrice le 2026-09-24).
        "platform_url": "https://www.banquepopulaire.fr/comptes-cartes/avantages-extrax/",
        "captured_at": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Catalogue public de l'espace avantages Banque Populaire (Extra+X), "
            "servi par l'API tierce publique ReducFactory (aucune connexion "
            "necessaire, verifie techniquement). 'carte_cadeau' = bon d'achat "
            "achete et paye directement au prix remise (voucher_product), "
            "seul mecanisme necessitant l'achat effectif d'une carte/d'un "
            "bon. 'direct' = reduction sur un achat fait directement chez le "
            "marchand, que le remboursement soit immediat "
            "(direct_discount_product) ou differe sur une cagnotte "
            "(cashback_product) - corrige le 2026-09-18, memes conventions "
            "que iGraal/eBuyClub, voir doc de suivi."
        ),
        "offers": offers,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    n_direct = sum(1 for o in offers if o["type"] == "direct")
    n_cadeau = sum(1 for o in offers if o["type"] == "carte_cadeau")
    print(
        f"\n{len(offers)} offres ecrites dans {args.output} "
        f"({n_direct} directes, {n_cadeau} cartes cadeaux)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
