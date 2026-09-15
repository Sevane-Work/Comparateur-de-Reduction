#!/usr/bin/env python3
"""
Récupère la liste des marchands et taux de cashback depuis l'API publique
Fnac Darty Pass, et l'enregistre dans un fichier JSON propre pour le
comparateur de réductions.

Usage :
    python fetch_fnac_darty.py [--output data/fnac_darty.json]

À planifier une fois par jour (cron sur Linux/Mac, Planificateur de tâches
sur Windows) pour garder les taux à jour.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

API_URL = "https://www.passfnacdarty.com/api/partner"

# Ces en-têtes suffisent en général pour une API publique. Si le site
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


def fetch_raw():
    """Appelle l'API et renvoie le JSON brut (dict)."""
    req = Request(API_URL, headers=HEADERS, method="GET")
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        print(f"Erreur HTTP {e.code} : l'API a peut-être changé ses exigences "
              f"(headers/cookies). Recapturez un cURL frais si ça persiste.",
              file=sys.stderr)
        raise
    except URLError as e:
        print(f"Erreur réseau : {e.reason}", file=sys.stderr)
        raise


def normalize(raw):
    """Transforme le payload brut en une liste simple et triée par taux."""
    payload = raw.get("payload", {})
    merchants = []

    for entry in payload.values():
        discount = entry.get("mostDisplayableDiscountOnline")
        percent = discount["percent"] if discount else None

        merchants.append({
            "id": entry.get("id"),
            "name": entry.get("name"),
            "percent": percent,
            "logo_file": entry.get("logo"),
            "coupon_count": entry.get("couponCount", 0),
            "has_offline": entry.get("hasOffline", False),
            "offer_link": entry.get("qwertysLink"),
        })

    # Marchands avec une offre active d'abord, triés par % décroissant
    merchants.sort(
        key=lambda m: (m["percent"] is None, -(m["percent"] or 0))
    )
    return merchants


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="fnac_darty.json",
        help="Chemin du fichier JSON de sortie (défaut : fnac_darty.json)"
    )
    args = parser.parse_args()

    raw = fetch_raw()
    merchants = normalize(raw)

    output = {
        "source": "Fnac Darty Pass",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "merchant_count": len(merchants),
        "merchants": merchants,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"OK — {len(merchants)} marchands enregistrés dans {args.output}")


if __name__ == "__main__":
    main()
