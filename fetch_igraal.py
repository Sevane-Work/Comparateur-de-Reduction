#!/usr/bin/env python3
"""
fetch_igraal.py

Recupere le catalogue de cashback public d'iGraal (fr.igraal.com) et genere
igraal.json, avec la meme structure "offers" plate que thecorner_offers.json
et ulys_offers.json (voir le doc de suivi du projet pour la discussion sur
l'harmonisation avec le schema imbrique "merchants" de fnac_darty.json).

AUCUNE CONNEXION PERSONNELLE N'EST NECESSAIRE : le catalogue iGraal est
entierement public (verifie techniquement le 2026-09-15 : les pages
repondent en 200 OK sans aucun cookie de session). Ce script est donc un
scraper HTML classique, au meme titre que fetch_fnac_darty.py, et conforme a
la regle de securite du projet (qui ne concerne que les plateformes
necessitant un compte personnel, comme Boursorama/The Corner ou Le Club
Ulys).

Dependances : uniquement la bibliotheque standard (urllib.request pour le
HTTP, html.parser pour l'extraction HTML), comme fetch_fnac_darty.py. Pas de
requirements.txt necessaire.

Methode :
- iGraal (Next.js App Router / React Server Components) rend tout le contenu
  cote serveur : les pages categorie (/codes-promo/<slug>) contiennent DEJA,
  dans le HTML initial, la totalite des marchands de la categorie (verifie :
  le bouton "Show More" affiche du contenu deja present dans le DOM, sans
  requete reseau supplementaire). Donc une simple requete HTTP GET + parsing
  HTML suffit, sans navigateur/JS.
- On parcourt une liste de slugs de categories/sous-categories
  (CATEGORY_SLUGS, recuperee depuis le menu de navigation du site), on
  recupere chaque page, et on extrait chaque lien marchand
  (<a href="/codes-promo/<slug-marchand>">...</a>) dont le texte contient un
  taux de cashback ("X % de cashback" ou "Jusqu'a X € cashback").
- Les memes marchands apparaissant dans plusieurs categories sont
  dedupliques (on garde la premiere occurrence rencontree).

Limite connue : cette collecte "en masse" par categorie ne recupere que le
taux de cashback affiche sur les cartes de la page categorie. Elle ne visite
PAS les pages marchand individuelles, ni le catalogue separe "Bon d'achat"
du site (fr.igraal.com/bon-achat, ~130 marchands, catalogue a part du
cashback classique) : ces deux sources peuvent proposer une offre "Bon
d'achat" (= carte cadeau payee directement moins cher) distincte du taux de
cashback recupere ici. Un marchand present uniquement sur le catalogue
"Bon d'achat" (ex. Kiabi, signale par l'utilisatrice le 2026-09-17) est donc
absent de ce fichier tant que cette source n'est pas ajoutee separement
(voir doc de suivi du projet, section bugs de donnees).

Toutes les offres de ce fichier sont du cashback credite sur une cagnotte a
retirer plus tard, obtenu en achetant directement chez le marchand via un
lien tracke : AUCUNE carte/bon n'est achetee (a la difference du catalogue
separe "Bon d'achat" ci-dessus, ex. Kiabi, qui reste type="carte_cadeau").
Classees type="direct" (corrige le 2026-09-18 - convention precedente du
2026-09-17 les mettait par erreur en "carte_cadeau" ; seul un mecanisme
necessitant l'achat effectif d'une carte/d'un bon est carte_cadeau, que le
remboursement soit immediat ou differe n'entre pas en compte - voir doc de
suivi du projet).

Usage :
    python fetch_igraal.py [--output igraal.json]
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

BASE_URL = "https://fr.igraal.com"
PLATFORM = "iGraal"

# Slugs de categories et sous-categories, recuperes depuis le menu
# "Categories" du site (fr.igraal.com, menu de navigation, le 2026-09-15).
CATEGORY_SLUGS = [
    "voyageloc", "marchands-etrangers", "high-tech", "modevetements", "maisonjardin",
    "santebeaute", "services-contrats", "sport", "culture-loisirs", "famille-enfants",
    "alimentation", "voiture",
    "voyage", "location", "vols", "locationdevoitures", "trains",
    "informatique", "electromenager", "logiciels-antivirus", "smartphones-tablettes",
    "tv-video", "photos", "jeux-video-console-jeu",
    "vetements", "bijoux-accessoires", "chaussures", "sportswear", "luxe",
    "meubles", "decoration", "maison-bricolage",
    "cosmetiques", "parfums", "sante", "charme",
    "finance", "energie", "telephonietelecom", "internet",
    "cd-dvd-bluray", "fournitures-bureau", "formation-et-apprentissage",
    "equipement-materiel-sport", "fitness-musculation", "ski",
    "cadeaux", "billetsspectacles", "livres", "arts-creatifs-et-diy", "jeux-argent",
    "supermarches", "epicerie-fine", "vins", "restaurants-livraisons-repas",
    "pieces-detachees-auto", "moto", "parkings-et-telepeages",
]

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "fr-FR,fr;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
}

# Une carte marchand affiche un texte comme "4 % de cashback",
# "36,25 % de cashback", "Jusqu'a 30 € cashback", "Jusqu'a 75 € cashback"...
RATE_RE = re.compile(r"(jusqu.?à\s+)?([\d.,]+)\s*(%|€)\s*(de\s+)?cashback", re.IGNORECASE)
PERCENT_ONLY_RE = re.compile(r"^([\d.,]+)\s*%\s*(de\s+)?cashback", re.IGNORECASE)


class MerchantLinkParser(HTMLParser):
    """
    Parseur HTML minimal (stdlib) qui extrait, pour chaque lien
    <a href="/codes-promo/<slug>">...texte...</a>, le href et le texte
    interieur complet (y compris celui des balises imbriquees, comme span).
    Evite la dependance a BeautifulSoup.
    """

    def __init__(self):
        super().__init__()
        self.links = []  # liste de {"href": str, "text": str}
        self._depth = 0
        self._current_href = None
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href.startswith("/codes-promo/"):
                self._depth = 1
                self._current_href = href
                self._current_text = []
                return
        if self._depth > 0:
            self._depth += 1

    def handle_endtag(self, tag):
        if self._depth == 0:
            return
        self._depth -= 1
        if self._depth == 0 and self._current_href is not None:
            text = " ".join("".join(self._current_text).split())
            self.links.append({"href": self._current_href, "text": text})
            self._current_href = None
            self._current_text = []

    def handle_data(self, data):
        if self._depth > 0:
            self._current_text.append(data + " ")


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


def parse_category_html(html, category_slug):
    """Extrait les offres marchand d'une page categorie iGraal deja telechargee."""
    parser = MerchantLinkParser()
    parser.feed(html)

    offers = []
    for link in parser.links:
        merchant_slug = link["href"][len("/codes-promo/"):].strip("/")
        if not merchant_slug or merchant_slug in CATEGORY_SLUGS:
            continue

        text = link["text"]
        m = RATE_RE.search(text)
        if not m:
            continue

        rate_text = m.group(0).strip()
        name = text[: m.start()].strip()
        # nettoyage des badges connus qui precedent parfois le nom
        name = re.sub(r"^(EN HAUSSE|Sponsorisé|New|Nouveau)\s*", "", name, flags=re.IGNORECASE).strip()
        if not name:
            name = merchant_slug.split("/")[0].replace("-", " ").title()

        percent = None
        pm = PERCENT_ONLY_RE.match(rate_text)
        if pm:
            try:
                percent = float(pm.group(1).replace(",", "."))
            except ValueError:
                percent = None

        offers.append({
            "name": name,
            "slug": merchant_slug.split("/")[0],
            "rate_text": rate_text,
            "percent": percent,
            "type_detail_fr": "Cashback",
            # Cashback credite sur une cagnotte a retirer plus tard, mais
            # AUCUNE carte/bon achete (achat direct chez le marchand via
            # lien tracke) : classe "direct", pas "carte_cadeau" (corrige le
            # 2026-09-18 - voir doc de suivi du projet).
            "type": "direct",
            "category_seen": category_slug,
        })
    return offers


def fetch_category(slug):
    html = fetch_url(f"{BASE_URL}/codes-promo/{slug}")
    if html is None:
        return []
    return parse_category_html(html, slug)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="igraal.json")
    args = parser.parse_args()

    all_offers = {}  # slug -> offer (dedup, on garde la premiere occurrence)

    for i, slug in enumerate(CATEGORY_SLUGS, 1):
        print(f"[{i}/{len(CATEGORY_SLUGS)}] {slug} ...", file=sys.stderr)
        offers = fetch_category(slug)
        for o in offers:
            if o["slug"] not in all_offers:
                all_offers[o["slug"]] = o
        time.sleep(0.5)  # politesse envers le serveur

    result = {
        "platform": PLATFORM,
        "captured_at": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Catalogue public (aucune connexion necessaire, verifie techniquement). "
            "Collecte en masse via les pages categories (taux de cashback affiche sur "
            "les cartes marchand). Toutes les offres sont de type 'direct' (cashback) : "
            "cette collecte ne visite pas les pages marchand individuelles, qui peuvent "
            "aussi proposer une offre 'Bon d'achat' (carte cadeau) ou des codes promo en "
            "plus du cashback."
        ),
        "offers": list(all_offers.values()),
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n{len(result['offers'])} marchands uniques ecrits dans {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
