#!/usr/bin/env python3
"""
Récupère la liste des marchands et taux de réduction depuis les API publiques
Fnac Darty Pass (cashback direct + remises carte cadeau / Ebon), et les
enregistre dans un fichier JSON structuré pour le comparateur de réductions.

Structure de sortie pensée pour accueillir d'autres plateformes plus tard :
chaque marchand a une liste "offers", chacune avec sa plateforme, son type
("direct" ou "carte_cadeau"), son taux, et si elle est cumulable avec les
autres offres du même marchand.

Usage :
    python fetch_fnac_darty.py [--output fnac_darty.json]

À planifier une fois par jour (cron, Planificateur de tâches Windows, ou
GitHub Actions) pour garder les taux à jour.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

PLATFORM = "Fnac Darty Pass"
CASHBACK_URL = "https://www.passfnacdarty.com/api/partner"
EBON_URL = "https://www.passfnacdarty.com/api/ebon/partner"

# Ces en-têtes suffisent en général pour ces API publiques. Si le site
# bloque la requête (erreur 403), il faudra ajouter le cookie 'referer'
# ou d'autres en-têtes copiés depuis un cURL frais (voir README).
HEADERS = {
    "accept": "*/*",
    "accept-language": "fr-FR,fr;q=0.9",
    "content-type": "application/json;charset=UTF-8",
    "referer": "https://www.passfnacdarty.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
}


def fetch_json(url):
    """Appelle une API et renvoie le JSON brut (dict)."""
    req = Request(url, headers=HEADERS, method="GET")
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        print(f"Erreur HTTP {e.code} sur {url} : l'API a peut-être changé ses "
              f"exigences (headers/cookies). Recapturez un cURL frais si ça persiste.",
              file=sys.stderr)
        raise
    except URLError as e:
        print(f"Erreur réseau sur {url} : {e.reason}", file=sys.stderr)
        raise


def build_merchants(cashback_raw, ebon_raw):
    """
    Construit un dict {id: {name, logo_file, offers: [...]}} en combinant
    les deux flux Fnac Darty Pass.
    """
    merchants = {}

    def get_or_create(mid, name, logo_file=None):
        if mid not in merchants:
            merchants[mid] = {"id": mid, "name": name, "logo_file": logo_file, "offers": []}
        return merchants[mid]

    # --- Cashback direct ---
    for entry in cashback_raw.get("payload", {}).values():
        discount = entry.get("mostDisplayableDiscountOnline")
        percent = discount["percent"] if discount else None
        if percent is None or percent == 0.0:
            continue
        m = get_or_create(entry["id"], entry.get("name"), entry.get("logo"))
        m["offers"].append({
            "platform": PLATFORM,
            "type": "direct",
            "percent": percent,
            "cumulative_with": None,  # pas d'info de cumul côté direct
            "link": entry.get("qwertysLink"),
        })

    # --- Remise carte cadeau (Ebon) ---
    for entry in ebon_raw.get("payload", []):
        ebon = entry.get("ebonDiscount")
        if not ebon:
            continue
        percent = ebon.get("cashback", {}).get("percent", {}).get("value")
        if percent is None or percent == 0.0:
            continue
        m = get_or_create(entry["id"], entry.get("name"), entry.get("logo"))
        m["offers"].append({
            "platform": PLATFORM,
            "type": "carte_cadeau",
            "percent": percent,
            # Indique si cette offre carte cadeau se cumule avec l'offre
            # cashback direct du même marchand sur la même plateforme.
            "cumulative_with": "direct" if ebon.get("cumulativeWithCashbackDiscount") else None,
            "link": None,
        })

    return merchants


def finalize(merchants):
    """Ajoute un résumé par marchand (meilleure offre, total cumulable)."""
    result = []
    for m in merchants.values():
        if not m["offers"]:
            continue

        best_offer = max(m["offers"], key=lambda o: o["percent"])

        # Total si toutes les offres cumulables entre elles sont additionnées
        cumulable_types = {o["type"] for o in m["offers"] if o["cumulative_with"]}
        cumulable_total = sum(
            o["percent"] for o in m["offers"]
            if o["type"] in cumulable_types or o["cumulative_with"]
        ) if cumulable_types else None

        result.append({
            "id": m["id"],
            "name": m["name"],
            "logo_file": m["logo_file"],
            "offers": m["offers"],
            "best_percent": best_offer["percent"],
            "best_type": best_offer["type"],
            "cumulable_total": cumulable_total,
        })

    result.sort(key=lambda m: -m["best_percent"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="fnac_darty.json",
        help="Chemin du fichier JSON de sortie (défaut : fnac_darty.json)"
    )
    args = parser.parse_args()

    cashback_raw = fetch_json(CASHBACK_URL)
    ebon_raw = fetch_json(EBON_URL)

    merchants = build_merchants(cashback_raw, ebon_raw)
    merchants = finalize(merchants)

    output = {
        "sources": [PLATFORM],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "merchant_count": len(merchants),
        "merchants": merchants,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"OK — {len(merchants)} marchands avec au moins une offre active "
          f"enregistrés dans {args.output}")


if __name__ == "__main__":
    main()
