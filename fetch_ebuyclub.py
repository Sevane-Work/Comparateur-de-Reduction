#!/usr/bin/env python3
"""
fetch_ebuyclub.py

Recupere DEUX catalogues publics eBuyClub (www.ebuyclub.com) et genere
ebuyclub.json, avec la meme structure "offers" plate que igraal.json,
thecorner_offers.json et ulys_offers.json.

Les deux catalogues :
  1. Cashback en ligne (type="direct") : www.ebuyclub.com/cashback +
     pages categorie www.ebuyclub.com/cashback/categorie/<slug>.
     Mecanisme : remboursement credite dans le solde du compte eBuyClub
     (comme iGraal), retirable "sans montant minimum" par virement ou
     Paypal d'apres la FAQ du site (verifie le 2026-09-16) -- contrairement
     a iGraal qui impose un minimum de 20€.
  2. Bons d'achat / cartes cadeaux (type="carte_cadeau") :
     www.ebuyclub.com/bons-d-achat (page unique, pas de pagination
     detectee : les 276 marchands partenaires sont tous presents dans le
     HTML initial). Meme mecanisme que le catalogue cashback : le bon
     d'achat est credite en cashback instantane dans le solde du compte
     (PAS un prix remise paye directement, contrairement a Fnac
     Darty/Ulys/Banque Populaire -- voir le doc de suivi du projet).
     Certains marchands (ex : FNAC) affichent EN PLUS un taux de cashback
     "classique" cumulable ("Jusqu'a X% de cashback") : ce taux
     supplementaire est capture dans le champ "stackable_cashback_rate_text"
     de l'offre, sans creer une offre separee.

AUCUNE CONNEXION PERSONNELLE N'EST NECESSAIRE : les deux catalogues sont
entierement publics (verifie via navigateur le 2026-09-16, pages
accessibles sans compte). Ce script est donc un scraper HTML classique,
conforme a la regle de securite du projet.

Dependances : uniquement la bibliotheque standard (urllib.request,
html.parser), comme fetch_igraal.py. Pas de requirements.txt necessaire.

Limite connue / a valider avant premiere execution reelle : ce script n'a
PAS pu etre teste bout-en-bout dans l'environnement de developpement
(ebuyclub.com est bloque par la liste blanche reseau du bac a sable, aussi
bien depuis le conteneur cloud que depuis le PC). La structure HTML des
cartes marchand a ete deduite de captures de texte de page (navigateur),
PAS du HTML brut : le parseur ci-dessous est ecrit pour etre tolerant
(regex larges, filtrage des lignes "Pas de CashBack en ce moment") mais
merite une verification sur un vrai run (ex : GitHub Actions) avant d'etre
considere fiable a 100%.

Usage :
    python fetch_ebuyclub.py [--output ebuyclub.json]
"""

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://www.ebuyclub.com"
PLATFORM = "eBuyClub"

# Slugs des categories du catalogue "cashback en ligne", recuperes depuis
# le menu / pied de page des pages categorie (verifie le 2026-09-16).
CASHBACK_CATEGORY_SLUGS = [
    "achats-professionnels",
    "alimentation-boissons",
    "auto-moto",
    "banque-assurances",
    "beaute-sante",
    "culture",
    "enfant-puericulture",
    "fleurs-cadeaux",
    "high-tech",
    "habitat-deco",
    "mode-accessoires",
    "sports-loisirs",
    "voyages",
]

# Page speciale "Marchands belges", separee des categories France.
CASHBACK_BELGIUM_URL = f"{BASE_URL}/cashback/belgique"

# Catalogue bons d'achat / cartes cadeaux : une seule page (pas de
# pagination detectee, les 276 marchands sont tous dans le HTML initial).
BONS_DACHAT_URL = f"{BASE_URL}/bons-d-achat"

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "fr-FR,fr;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
}

NO_OFFER_MARKER = "pas de cashback en ce moment"

# "Jusqu'a 2,5% remboursés", "2% remboursés", "40€ remboursés",
# "Jusqu'a 48€ remboursés", "5,40€ remboursés"...
RATE_RE = re.compile(
    r"(jusqu.?à\s+)?([\d.,]+)\s*(%|€)\s*rembours", re.IGNORECASE
)
PERCENT_ONLY_RE = re.compile(r"^([\d.,]+)\s*%\s*rembours", re.IGNORECASE)

# Taux de cashback en ligne cumulable affiche sur certaines fiches
# "bons d'achat" (ex : "Jusqu'a 4,1%de cashback").
STACKABLE_RE = re.compile(
    r"jusqu.?à\s+([\d.,]+)\s*%\s*de\s*cashback", re.IGNORECASE
)

LOCATION_LABELS = [
    "UNIQUEMENT EN LIGNE",
    "UNIQUEMENT EN MAGASIN",
    "EN LIGNE",
    "EN MAGASIN",
]


class CardSegmentParser(HTMLParser):
    """
    Parseur HTML minimal (stdlib) qui decoupe le flux en "segments", un par
    marchand, identifies par le PREMIER lien <a href="<prefix>...">
    rencontre. Contrairement a iGraal (une carte = un seul <a> englobant
    tout le contenu), les cartes eBuyClub peuvent contenir PLUSIEURS <a>
    distincts pointant vers le meme marchand (image, nom, bouton "J'achete"),
    avec le texte utile (taux, zone de validite...) place en dehors de ces
    liens, en texte frere. On accumule donc tout le texte rencontre entre
    le debut d'un href et le debut du PROCHAIN href DIFFERENT, ce qui
    capture l'integralite du contenu textuel d'une carte quelle que soit
    sa structure exacte.
    """

    def __init__(self, href_prefix):
        super().__init__()
        self.href_prefix = href_prefix
        self.segments = []  # liste de {"href": str, "text": str}
        self._current_href = None
        self._buffer = []

    def _flush(self):
        if self._current_href is not None:
            text = " ".join("".join(self._buffer).split())
            self.segments.append({"href": self._current_href, "text": text})
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "") or ""
            if href.startswith(self.href_prefix) and href != self._current_href:
                self._flush()
                self._current_href = href
        if self._current_href is not None:
            self._buffer.append(" ")

    def handle_data(self, data):
        if self._current_href is not None:
            self._buffer.append(data + " ")

    def close(self):
        super().close()
        self._flush()


def fetch_url(url):
    req = Request(url, headers=HEADERS, method="GET")
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        print(f"  [!] HTTP {e.code} sur {url}", file=sys.stderr)
        return None
    except URLError as e:
        print(f"  [!] Erreur reseau sur {url} : {e.reason}", file=sys.stderr)
        return None


def slug_to_name(slug):
    # ex: "carrefour-10310" -> "Carrefour" ; "pc-componentes-4821" -> "Pc Componentes"
    base = re.sub(r"-\d+$", "", slug)
    return base.replace("-", " ").strip().title()


def parse_offers(html, href_prefix, offer_type, category_seen):
    """
    Extrait les offres marchand d'une page eBuyClub deja telechargee
    (catalogue cashback en ligne OU catalogue bons d'achat, selon
    href_prefix/offer_type passes par l'appelant).
    """
    parser = CardSegmentParser(href_prefix)
    parser.feed(html)
    parser.close()  # indispensable : sinon le tout dernier marchand de la
    # page est bufferise mais jamais ajoute a parser.segments (bug corrige
    # le 2026-09-16 grace a un auto-test avant livraison).

    offers = []
    seen_slugs = set()
    for seg in parser.segments:
        href = seg["href"]
        slug_with_id = href[len(href_prefix):].strip("/")
        if not slug_with_id:
            continue
        slug = slug_with_id.split("/")[0]
        if not slug or slug in seen_slugs:
            continue

        text = seg["text"]
        if NO_OFFER_MARKER in text.lower():
            # Marchand liste mais sans offre active : on l'exclut, comme
            # convenu (marchands "Pas de CashBack en ce moment").
            continue

        m = RATE_RE.search(text)
        if not m:
            continue

        rate_text = m.group(0).strip()
        percent = None
        pm = PERCENT_ONLY_RE.match(rate_text)
        if pm:
            try:
                percent = float(pm.group(1).replace(",", "."))
            except ValueError:
                percent = None

        offer = {
            "name": slug_to_name(slug),
            "slug": slug,
            "rate_text": rate_text,
            "percent": percent,
            "type_detail_fr": "Bon d'achat / carte cadeau" if offer_type == "carte_cadeau" else "Cashback",
            "type": offer_type,
            "category_seen": category_seen,
        }

        if offer_type == "carte_cadeau":
            locations = [loc for loc in LOCATION_LABELS if loc in text.upper()]
            # "UNIQUEMENT EN LIGNE"/"UNIQUEMENT EN MAGASIN" sont plus
            # specifiques que "EN LIGNE"/"EN MAGASIN" : si l'un des labels
            # UNIQUEMENT est present, on ne garde que celui-la.
            uniquement = [l for l in locations if l.startswith("UNIQUEMENT")]
            offer["usage_location"] = uniquement[0] if uniquement else (
                " + ".join(locations) if locations else None
            )
            sm = STACKABLE_RE.search(text)
            offer["stackable_cashback_rate_text"] = sm.group(0).strip() if sm else None

        offers.append(offer)
        seen_slugs.add(slug)
    return offers


def fetch_cashback_category(slug_or_url):
    if slug_or_url.startswith("http"):
        url = slug_or_url
        category_seen = "belgique"
    else:
        url = f"{BASE_URL}/cashback/categorie/{slug_or_url}"
        category_seen = slug_or_url
    html = fetch_url(url)
    if html is None:
        return []
    return parse_offers(html, "/reduction-", "direct", category_seen)


def fetch_bons_dachat():
    html = fetch_url(BONS_DACHAT_URL)
    if html is None:
        return []
    return parse_offers(html, "/selection-bons-d-achat/", "carte_cadeau", "bons-d-achat")



CATEGORY_PATTERNS = [
    ("cinema", re.compile(r"mk2|gaumont|path[ée]|\bugc\b|cin[ée]|\bcgr\b|kinepolis", re.IGNORECASE)),
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

def main():
    argp = argparse.ArgumentParser()
    argp.add_argument("--output", default="ebuyclub.json")
    args = argp.parse_args()

    all_offers = {}  # (type, slug) -> offer, dedup en gardant la 1ere occurrence

    targets = list(CASHBACK_CATEGORY_SLUGS) + [CASHBACK_BELGIUM_URL]
    for i, slug in enumerate(targets, 1):
        label = slug if not slug.startswith("http") else "belgique"
        print(f"[cashback {i}/{len(targets)}] {label} ...", file=sys.stderr)
        for o in fetch_cashback_category(slug):
            key = (o["type"], o["slug"])
            if key not in all_offers:
                all_offers[key] = o
        time.sleep(0.5)

    print("[bons-d-achat] ...", file=sys.stderr)
    for o in fetch_bons_dachat():
        key = (o["type"], o["slug"])
        if key not in all_offers:
            all_offers[key] = o

    tagged_offers = tag_category(list(all_offers.values()))

    result = {
        "platform": PLATFORM,
        "captured_at": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Deux catalogues publics eBuyClub combines : 'direct' = cashback en "
            "ligne classique (www.ebuyclub.com/cashback, credite sur le solde du "
            "compte, retirable sans montant minimum d'apres la FAQ du site) et "
            "'carte_cadeau' = bons d'achat (www.ebuyclub.com/bons-d-achat, meme "
            "mecanisme de credit en solde qu'iGraal, PAS un prix remise paye "
            "directement). Les marchands affichant 'Pas de CashBack en ce moment' "
            "sont exclus. Certaines offres carte_cadeau ont un taux de cashback "
            "en ligne cumulable, capture dans 'stackable_cashback_rate_text'."
        ),
        "offers": tagged_offers,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    n_direct = sum(1 for o in result["offers"] if o["type"] == "direct")
    n_cadeau = sum(1 for o in result["offers"] if o["type"] == "carte_cadeau")
    print(
        f"\n{len(result['offers'])} offres ecrites dans {args.output} "
        f"({n_direct} cashback en ligne, {n_cadeau} bons d'achat)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
