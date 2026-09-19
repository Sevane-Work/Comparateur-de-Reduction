#!/usr/bin/env python3
"""
fetch_igraal_selection.py

Recupere la "Selection du jour" d'iGraal (fr.igraal.com/selection/) :
une dizaine d'offres a taux relance / booste, distinctes du catalogue
cashback general (igraal.json) et du catalogue "Bon d'achat"
(igraal_bon_achat.json). Genere igraal_selection_du_jour.json.

AUCUNE CONNEXION PERSONNELLE N'EST NECESSAIRE : page publique, verifiee
techniquement le 2026-09-17 (requete sans cookie, HTTP 200, contenu
identique a ce qui s'affiche dans le navigateur - rendu cote serveur, pas
besoin de JavaScript/navigateur pour recuperer les donnees).

Contexte / decision produit (utilisateur, 2026-09-17, precise le 2026-09-18) :
la page affiche une dizaine de cartes "Sponsorise", mais UNE SEULE porte
reellement le badge "BOOST DU JOUR" (verifie via inspection live le
2026-09-18 : 11 bannercards trouvees, 1 seule avec le badge, ex. adidas
13% vs 2,5% normal). Les ~10 autres cartes n'ont ni taux reellement
booste ni vraie date d'expiration - avant correction, elles etaient
TOUTES marquees boosted=true avec une fausse expiration "minuit Paris",
ce qui affichait des enseignes perimees (ex. Lidl, offre de la veille)
dans le cadre "Boost du jour" du site meme quand leur taux n'avait pas
change. Decision (corrigee le 2026-09-18) : on ne garde QUE la ou les
cartes portant litteralement "BOOST DU JOUR", platform="iGraal" (fusion
avec le filtre iGraal existant, PAS de filtre separe). Chaque offre
retenue porte un flag "boosted": true et un champ "expires_at" (ISO)
calcule a la capture :
- si un minuteur "EXPIRE DANS HH:MM:SS" est visible sur la fiche, on
  ajoute cette duree a l'heure de capture ;
- sinon, on prend par defaut minuit (Europe/Paris) suivant la capture,
  toute la page etant intitulee "Selection du jour" (reinitialisation
  quotidienne, confirme par l'utilisateur : "en general jusqu'a minuit").
Le site (comparateur-reductions.html) doit IGNORER a l'affichage toute
offre dont "expires_at" est deja passee, meme si ce fichier n'a pas
encore ete rafraichi ce jour-la (retour automatique au taux normal
iGraal, sans jamais afficher un boost perime).

Structure HTML (verifiee le 2026-09-17, cote serveur, sans JS) : chaque
offre est un bloc <div class="nalu bannercard ...">...</div> contenant,
dans l'ordre, le nom de l'enseigne, le texte de la recompense ("Jusqu'a
X % de cashback...", ou un montant fixe "100 € de cashback"), la base
"Au lieu de X", eventuellement "BOOST DU JOUR" et "EXPIRE DANS
HH:MM:SS", puis "Sponsorise" et "Activer le cashback" (bruit ignore).

Non teste en conditions reelles (acces reseau a fr.igraal.com bloque
depuis l'environnement Claude au moment de l'ecriture - structure
verifiee via un navigateur reel a la place, meme methode que pour
fetch_ebuyclub.py a l'origine) : a verifier au premier run GitHub
Actions.

Depend uniquement de la bibliotheque standard (urllib.request, re,
html.parser, zoneinfo), comme les autres scripts fetch_*.py du projet.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
    PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover - fallback tres improbable (stdlib complete requise)
    PARIS_TZ = timezone.utc

URL = "https://fr.igraal.com/selection/"
PLATFORM = "iGraal"
OUT_PATH = "igraal_selection_du_jour.json"

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "fr-FR,fr;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
}

PERCENT_RE = re.compile(r"([\d.,]+)\s*%")
# Deux formats observes le 2026-09-17 : un compte a rebours fin (heures,
# ex. "EXPIRE DANS 02:41:53", pour les offres qui se terminent le jour
# meme) et un format en jours pour les offres plus longues (ex. "EXPIRE
# DANS 6 JOURS", vu sur ENGIE - pas une offre "du jour" au sens strict).
EXPIRE_HMS_RE = re.compile(r"EXPIRE DANS\s*(\d{1,2}):(\d{2}):(\d{2})")
EXPIRE_DAYS_RE = re.compile(r"EXPIRE DANS\s*(\d+)\s*JOURS?", re.IGNORECASE)
AU_LIEU_RE = re.compile(r"Au lieu de\s*([\d.,]+\s*(?:%|\u20ac))", re.IGNORECASE)

# Mise en avant "conso du quotidien" demandee par l'utilisateur le
# 2026-09-17 (cote gauche du site, cadre dedie) : uniquement alimentaire /
# habillement / parfumerie-beaute - pas les abonnements/services annuels
# (assurance, energie, telecom, voyage...) meme quand ils sont boostes.
# Liste de mots-cles par enseigne, non exhaustive par construction (comme
# CATEGORY_PATTERNS ailleurs dans le projet) : a completer au fil de l'eau
# si une enseigne frequente manque.
HIGHLIGHT_CATEGORY_PATTERNS = [
    ("alimentaire", re.compile(
        r"carrefour|monoprix|auchan|leclerc|intermarch[ée]|casino|franprix|"
        r"naturalia|biocoop|picard|grand frais|lidl|aldi|uber eats|"
        r"deliveroo|the ?fork|netto|cora|spar|g20|bio ?c ?bon",
        re.IGNORECASE,
    )),
    ("habillement", re.compile(
        r"uniqlo|\bzara\b|\bh ?& ?m\b|\bc ?& ?a\b|kiabi|celio|\bjules\b|"
        r"zalando|\basos\b|\bnike\b|adidas|new balance|decathlon|"
        r"foot locker|\bcourir\b|sarenza|spartoo|promod|cama[iï]eu|"
        r"\betam\b|undiz|devred|g[ée]mo|the kooples|sandro|maje|"
        r"pull ?& ?bear|bershka|primark",
        re.IGNORECASE,
    )),
    ("parfumerie", re.compile(
        r"nocib[ée]|s[ée]phora|marionnaud|yves rocher|douglas|"
        r"beauty success|origines parfums|l.occitane|clarins|kiko|"
        r"parfumerie|origines|nyx",
        re.IGNORECASE,
    )),
]


def highlight_category_for(name):
    for category, pattern in HIGHLIGHT_CATEGORY_PATTERNS:
        if pattern.search(name):
            return category
    return None


class BannerCardParser(HTMLParser):
    """
    Parseur HTML minimal (stdlib) qui isole chaque bloc
    <div class="... bannercard ...">...</div> et en extrait le texte
    complet (toutes balises confondues), un segment par offre — meme
    principe que CardSegmentParser dans fetch_ebuyclub.py, adapte a un
    marqueur de classe plutot qu'a un prefixe de href (ces cartes n'ont
    pas de lien texte exploitable, la navigation se fait en JavaScript).
    """

    def __init__(self):
        super().__init__()
        self.cards = []  # liste de textes de carte
        self._depth = 0        # profondeur DANS une bannercard (0 = hors carte)
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "") or ""
        is_card_start = self._depth == 0 and "bannercard" in classes.split()
        if is_card_start:
            self._depth = 1
            self._buffer = []
            return
        if self._depth > 0:
            self._depth += 1

    def handle_endtag(self, tag):
        if self._depth == 0:
            return
        self._depth -= 1
        if self._depth == 0:
            text = " | ".join(" ".join(self._buffer).split() for _ in [0]) if False else None
            # Reconstruit le texte en respectant les sauts entre noeuds de
            # texte contigus (evite de coller deux mots de blocs voisins).
            joined = " ".join(self._buffer)
            joined = re.sub(r"\s+", " ", joined).strip()
            self.cards.append(joined)
            self._buffer = []

    def handle_data(self, data):
        if self._depth > 0:
            stripped = data.strip()
            if stripped:
                self._buffer.append(stripped)


def fetch_html():
    req = Request(URL, headers=HEADERS, method="GET")
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_card(text, now):
    """Transforme le texte brut d'une carte en offre (dict) ou None si
    ininterpretable (pas de nom exploitable)."""
    # Le nom est le tout premier "mot" (avant que le texte de recompense,
    # qui contient toujours "cashback", ne commence).
    m_cashback = re.search("cashback", text, re.IGNORECASE)
    if not m_cashback:
        return None
    name = text[: m_cashback.start()]
    # Le nom se termine juste avant le debut du texte de recompense, qui
    # commence generalement par un chiffre ou "Jusqu'a" - on coupe au
    # dernier caractere alpha avant cette zone numerique/"Jusqu'a".
    m_name_end = re.search(r"(Jusqu.?à|\d)", name)
    if m_name_end:
        name = name[: m_name_end.start()]
    name = name.strip(" |")
    if not name:
        return None

    reward_and_rest = text[len(name):]

    # Texte de recompense : du debut de reward_and_rest jusqu'a la premiere
    # occurrence de "cashback" incluse (le mot reapparait plus loin dans le
    # texte du bouton "Activer le cashback", qu'on ne veut pas inclure).
    m_first_cashback = re.search(r"cashback", reward_and_rest, re.IGNORECASE)
    if m_first_cashback:
        reward_text = reward_and_rest[: m_first_cashback.end()].strip(" |")
    else:
        reward_text = reward_and_rest.strip(" |")

    percent = None
    m_pct = PERCENT_RE.search(reward_text)
    if m_pct:
        try:
            percent = float(m_pct.group(1).replace(",", "."))
        except ValueError:
            percent = None

    m_au_lieu = AU_LIEU_RE.search(text)
    normal_rate_text = m_au_lieu.group(1).strip(" |") if m_au_lieu else None

    is_boost_du_jour = "BOOST DU JOUR" in text

    m_expire_hms = EXPIRE_HMS_RE.search(text)
    m_expire_days = EXPIRE_DAYS_RE.search(text)
    if m_expire_hms:
        h, mi, s = (int(x) for x in m_expire_hms.groups())
        expires_at = now + timedelta(hours=h, minutes=mi, seconds=s)
    elif m_expire_days:
        expires_at = now + timedelta(days=int(m_expire_days.group(1)))
    else:
        # Pas de minuteur affiche sur cette fiche : par defaut, minuit
        # (Europe/Paris) suivant la capture - toute la page etant une
        # "Selection du jour" qui se renouvelle quotidiennement.
        now_paris = now.astimezone(PARIS_TZ)
        next_midnight_paris = (now_paris + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        expires_at = next_midnight_paris.astimezone(timezone.utc)

    return {
        "name": name,
        "rate_text": reward_text or (f"{percent:g}%" if percent is not None else None),
        "percent": percent,
        "type_detail_fr": "Cashback booste (offre du jour, temporaire)",
        # Meme mecanisme que le catalogue general iGraal (igraal.json,
        # corrige le 2026-09-18) : cashback credite sur la cagnotte,
        # aucune carte achetee -> "direct", jamais "carte_cadeau".
        "type": "direct",
        "category_seen": None,
        "boosted": True,
        "boost_du_jour": is_boost_du_jour,
        "normal_rate_text": normal_rate_text,
        "expires_at": expires_at.isoformat(),
        "highlight_category": highlight_category_for(name),
    }


def main():
    argp = argparse.ArgumentParser()
    argp.add_argument("--output", default=OUT_PATH)
    argp.add_argument(
        "--input-html",
        default=None,
        help=(
            "Mode test/local : lire depuis un fichier HTML deja telecharge "
            "au lieu d'interroger le reseau (le reseau de cet environnement "
            "Claude n'a pas acces a fr.igraal.com ; GitHub Actions, lui, l'a)."
        ),
    )
    args = argp.parse_args()

    now = datetime.now(timezone.utc)

    if args.input_html:
        with open(args.input_html, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
    else:
        try:
            html = fetch_html()
        except (HTTPError, URLError) as e:
            print(f"[!] Erreur reseau sur {URL} : {e}", file=sys.stderr)
            raise SystemExit(1)

    parser = BannerCardParser()
    parser.feed(html)

    if len(parser.cards) < 5:
        raise SystemExit(
            f"Seulement {len(parser.cards)} bannercards trouvees sur la page "
            "(attendu ~10-15) - la structure de la page a probablement "
            "change, a diagnostiquer avant de publier un fichier incomplet."
        )

    offers = []
    seen_names = set()
    for card_text in parser.cards:
        if "BOOST DU JOUR" not in card_text:
            # Carte "Sponsorise" sans le badge "BOOST DU JOUR" : pas de taux
            # reellement booste ni de vraie expiration (corrige le
            # 2026-09-18, voir doc de suivi - ~10 des ~11 cartes de la page
            # sont dans ce cas chaque jour). Son taux normal reste visible
            # via le catalogue general igraal.json, pas ici.
            continue
        offer = parse_card(card_text, now)
        if offer is None:
            continue
        if offer["name"] in seen_names:
            continue
        seen_names.add(offer["name"])
        offers.append(offer)

    result = {
        "platform": PLATFORM,
        "captured_at": None,
        "generated_at": now.isoformat(),
        "note": (
            "Selection du jour d'iGraal (fr.igraal.com/selection/), taux "
            "relances/boostes pour la journee en cours, distincte du "
            "catalogue cashback general (igraal.json). Chaque offre porte "
            "'expires_at' (ISO) : le site doit ignorer a l'affichage toute "
            "offre dont la date est depassee, meme si ce fichier n' a pas "
            "encore ete rafraichi (retour automatique au taux normal "
            "iGraal). Page publique, aucune connexion requise."
        ),
        "offers": offers,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    n_boost = sum(1 for o in offers if o["boost_du_jour"])
    print(f"OK - {len(offers)} offres ecrites dans {args.output} ({n_boost} 'BOOST DU JOUR')")


if __name__ == "__main__":
    main()
