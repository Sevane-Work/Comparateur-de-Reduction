#!/usr/bin/env python3
"""
Récupère la liste des marchands et taux de réduction depuis les API publiques
Fnac Darty Pass (cashback direct + remises carte cadeau / Ebon), et les
enregistre dans un fichier JSON structuré pour le comparateur de réductions.

Structure de sortie : schéma plat commun à toutes les plateformes du projet
({platform, captured_at, generated_at, note, offers: [...]}) — un marchand
avec plusieurs offres (ex. cashback direct + carte cadeau) apparaît comme
plusieurs entrées dans "offers" partageant le même "name", exactement comme
pour les autres plateformes (iGraal, eBuyClub, Banque Populaire...). Chaque
offre porte en plus deux champs propres à Fnac Darty Pass : "cumulative_with"
(indique si elle est cumulable avec une autre offre du même marchand sur
cette même plateforme) et "link" (lien d'affiliation direct, quand connu).

Usage :
    python fetch_fnac_darty.py [--output fnac_darty.json]

À planifier une fois par jour (cron, Planificateur de tâches Windows, ou
GitHub Actions) pour garder les taux à jour.
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone
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


def build_offers(cashback_raw, ebon_raw):
    """
    Construit la liste plate des offres (une entrée par offre, un même
    marchand pouvant apparaître plusieurs fois) en combinant les deux flux
    Fnac Darty Pass.
    """
    offers = []

    # --- Cashback direct ---
    for entry in cashback_raw.get("payload", {}).values():
        discount = entry.get("mostDisplayableDiscountOnline")
        percent = discount["percent"] if discount else None
        if percent is None or percent == 0.0:
            continue
        offers.append({
            "name": entry.get("name"),
            "rate_text": None,
            "percent": percent,
            "type_detail_fr": "Réduction directe",
            "type": "direct",
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
        offers.append({
            "name": entry.get("name"),
            "rate_text": None,
            "percent": percent,
            "type_detail_fr": "Carte cadeau",
            "type": "carte_cadeau",
            # Indique si cette offre carte cadeau se cumule avec l'offre
            # cashback direct du même marchand sur la même plateforme.
            "cumulative_with": "direct" if ebon.get("cumulativeWithCashbackDiscount") else None,
            "link": None,
        })

    return offers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="fnac_darty.json",
        help="Chemin du fichier JSON de sortie (défaut : fnac_darty.json)"
    )
    args = parser.parse_args()

    cashback_raw = fetch_json(CASHBACK_URL)
    ebon_raw = fetch_json(EBON_URL)

    offers = build_offers(cashback_raw, ebon_raw)
    offers.sort(key=lambda o: -o["percent"])

    output = {
        "platform": PLATFORM,
        "captured_at": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Catalogue public (aucune connexion necessaire), deux API : cashback direct "
            "et remise carte cadeau (Ebon). Un marchand avec les deux types d'offres "
            "apparait comme deux entrees distinctes partageant le meme 'name'. Le champ "
            "'cumulative_with' indique, pour une offre carte cadeau, si elle est cumulable "
            "avec l'offre cashback direct du meme marchand sur Fnac Darty Pass (aucune "
            "info de cumul disponible dans l'autre sens)."
        ),
        "offers": offers,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    merchant_count = len({o["name"] for o in offers})
    print(f"OK — {len(offers)} offres ({merchant_count} marchands distincts) "
          f"enregistrées dans {args.output}")


if __name__ == "__main__":
    main()
