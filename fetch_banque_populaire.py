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

Limite connue / à valider avant le premier run réel : le champ exact utilisé
pour un bon d'achat à MONTANT FIXE (plutôt que "variable_amount", le seul
type observé dans l'échantillon inspecté) n'a pas été vérifié — si un tel
produit existe dans le catalogue, il sera simplement ignoré (pas de
percent exploitable) plutôt que de faire planter le script.

Usage :
    python fetch_banque_populaire.py [--output banque_populaire.json]
"""

import argparse
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
        percent = None
        for off in product.get("offers") or []:
            margin = off.get("marginAmount")
            if isinstance(margin, (int, float)):
                percent = float(margin)
                break
        if percent is None:
            # Carte a montant fixe ou structure non reconnue : on ignore
            # plutôt que de deviner un taux incorrect (voir limite connue).
            return None
        return {
            "name": name,
            "slug": slug,
            "rate_text": f"{percent:g}%",
            "percent": percent,
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
        m = PERCENT_RE.search(rate_text)
        percent = float(m.group(1).replace(",", ".")) if m else None
        return {
            "name": name,
            "slug": slug,
            "rate_text": rate_text.strip(),
            "percent": percent,
            "type_detail_fr": "Cashback" if ptype == "cashback_product" else "Réduction directe",
            "type": "direct",
            "category_seen": None,
        }

    return None


def fetch_all_products():
    """Parcourt toute la collection paginée et renvoie la liste brute des produits.

    L'API ReducFactory (API Platform) peut répondre sous deux formes selon
    la négociation de contenu : soit une simple liste JSON (observé en
    conditions réelles avec l'en-tête Accept: application/json), soit une
    collection JSON-LD/Hydra enveloppée ({"member"/"hydra:member": [...],
    "view"/"hydra:view": {"next"/"hydra:next": "..."}}), comme documenté
    initialement. On gère les deux pour rester robuste aux deux formats.
    """
    products = []
    url = f"{TENANT_BASE}products?page=1&itemsPerPage=300"
    seen_urls = set()
    while url and url not in seen_urls:
        seen_urls.add(url)
        data = fetch_json(url)
        if data is None:
            break
        if isinstance(data, list):
            # Réponse "plate" : pas d'enveloppe de pagination disponible.
            # Le catalogue (127 produits) tient dans une seule page de 300,
            # donc on s'arrête ici plutôt que de deviner une URL suivante.
            products.extend(data)
            break
        member = data.get("member") or data.get("hydra:member") or []
        products.extend(member)
        view = data.get("view") or data.get("hydra:view") or {}
        next_path = view.get("next") or view.get("hydra:next")
        if not next_path:
            break
        # "next" est un chemin relatif de type "/1.0/<tenant>/products?..."
        url = "https://api.reducfactory.com" + next_path
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
        key = (offer["type"], offer["slug"])
        if key in seen_slugs:
            continue
        seen_slugs.add(key)
        offers.append(offer)

    result = {
        "platform": PLATFORM,
        "captured_at": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Catalogue public de l'espace avantages Banque Populaire (Extra+X), "
            "servi par l'API tierce publique ReducFactory (aucune connexion "
            "necessaire, verifie techniquement). 'carte_cadeau' = bon d'achat "
            "paye directement au prix remise (pas de cashback a cumuler, "
            "confirme par l'utilisateur). 'direct' = reduction/cashback affiche "
            "en texte libre sur la fiche produit, taux extrait par recherche du "
            "premier pourcentage dans ce texte."
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
