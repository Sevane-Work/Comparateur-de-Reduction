#!/usr/bin/env python3
"""
fetch_igraal_bon_achat.py

Recupere le catalogue "Bon d'achat" d'iGraal (fr.igraal.com/bon-achat),
distinct du catalogue cashback general deja couvert par fetch_igraal.py
(voir la limite documentee dans ce dernier : "ne visite PAS ... le
catalogue separe 'Bon d'achat' du site"). Genere igraal_bon_achat.json,
meme structure plate "offers" que les autres fichiers du projet.

AUCUNE CONNEXION PERSONNELLE N'EST NECESSAIRE : la page est entierement
publique (verifie techniquement le 2026-09-17, requete sans cookie).

Methode :
- La page (Next.js App Router / React Server Components) livre tout son
  contenu dans le HTML initial, sous forme d'un flux "RSC" : un gros bloc
  JSON echappe (guillemets prece
  des d'un backslash) inclus tel quel dans
  le HTML, PAS un DOM a executer. Une simple requete HTTP GET suffit, sans
  navigateur/JS (verifie le 2026-09-17 : le HTML brut recupere sans cookie
  contient deja tous les marchands).
- Chaque marchand apparait dans un bloc au format repere :
  {\"retailer\":{\"name\":\"<Nom affiche>\", ... \"userCashbackPercentValue\":<taux>,
  ... \"providerMerchantName\":\"<slug>\" ...}
  Les caracteres speciaux du nom (ex. "&") sont echappes en \\uXXXX dans le
  flux et doivent etre decodes.
- 151 marchands recuperes lors de la verification du 2026-09-17 (a
  comparer au prochain run : une chute importante indiquerait un
  changement de structure de page a diagnostiquer avant de publier).

Depend uniquement de la bibliotheque standard (urllib.request, re, json),
comme les autres scripts fetch_*.py du projet.
"""
import json
import re
import unicodedata
import urllib.request
from datetime import datetime, timezone

URL = "https://fr.igraal.com/bon-achat"
PLATFORM = "iGraal"
OUT_PATH = "igraal_bon_achat.json"

CATEGORY_PATTERNS = [
    ("cinema", re.compile(r"mk2|gaumont|path[ée]|\bugc\b|cin[ée]|\bcgr\b|kinepolis", re.IGNORECASE)),
]


def tag_category(offers):
    # Decision produit (utilisatrice, 2026-09-19) : dans l'onglet "Loisirs",
    # on ne parle pas de "carte cadeau" mais de billet/pass a prix reduit -
    # meme quand le mecanisme technique reel est un achat de bon a l'avance
    # (ce qui, selon la regle generale du site, vaudrait carte_cadeau).
    # Exception volontaire, reservee aux offres category-taguees (voir
    # fetch_banque_populaire.py / fetch_ebuyclub.py / fetch_fnac_darty.py,
    # meme convention).
    for offer in offers:
        for category, pattern in CATEGORY_PATTERNS:
            if pattern.search(offer.get("name", "")):
                offer["category"] = category
                offer["type"] = "direct"
                break
    return offers


def slugify(name):
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = n.lower()
    n = re.sub(r"[^a-z0-9]+", "-", n)
    return n.strip("-")


def decode_escaped_unicode(s):
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


NAME_RE = r'(?:[^\\"]|\\u[0-9a-fA-F]{4}|\\.)+?'
BLOCK_RE = re.compile(
    r'\{\\"retailer\\":\{\\"name\\":\\"(' + NAME_RE + r')\\"'
    r'[\s\S]{0,600}?\\"userCashbackPercentValue\\":([0-9.]+)'
)


HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "fr-FR,fr;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "referer": "https://fr.igraal.com/",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "upgrade-insecure-requests": "1",
}


def fetch_html():
    # En-tetes alignes sur ceux de fetch_igraal.py (2026-09-17, corrige apres
    # un HTTP 403 en conditions reelles sur GitHub Actions) : le simple
    # "User-Agent: Mozilla/5.0" utilise jusque-la ne suffisait plus, la page
    # etant sur le meme domaine que fetch_igraal.py, qui n'a jamais echoue
    # avec ces en-tetes plus complets.
    req = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_offers(html):
    offers = []
    seen_names = set()
    for m in BLOCK_RE.finditer(html):
        raw_name, percent_str = m.group(1), m.group(2)
        name = decode_escaped_unicode(raw_name)
        if name in seen_names:
            continue
        seen_names.add(name)
        percent = float(percent_str)
        if percent <= 0:
            continue
        offers.append({
            "name": name,
            "slug": slugify(name),
            "rate_text": f"{percent:g}%",
            "percent": percent,
            "type_detail_fr": "Bon d'achat / carte cadeau (prix remisé payé directement)",
            "type": "carte_cadeau",
            "category_seen": None,
        })
    return offers


def main():
    html = fetch_html()
    offers = parse_offers(html)
    if len(offers) < 100:
        raise SystemExit(
            f"Seulement {len(offers)} offres trouvees (attendu ~151) - "
            "la structure de la page a probablement change, a diagnostiquer "
            "avant de publier un fichier incomplet."
        )
    offers = tag_category(offers)

    result = {
        "platform": PLATFORM,
        # Lien de secours affiche au clic sur le nom de l'enseigne quand aucune
        # offre n'a de lien precis (demande de l'utilisatrice le 2026-09-24).
        "platform_url": "https://fr.igraal.com/bon-achat",
        "captured_at": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Catalogue 'Bon d'achat' d'iGraal (fr.igraal.com/bon-achat), "
            "distinct du catalogue cashback general (igraal.json). Page "
            "publique, aucune connexion requise. Taux = pourcentage de "
            "reduction sur le prix facial de la carte cadeau."
        ),
        "offers": offers,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"OK - {len(offers)} offres ecrites dans {OUT_PATH}")


if __name__ == "__main__":
    main()
