#!/usr/bin/env python3
"""Calcule, pour chaque enseigne, le meilleur % du jour toutes plateformes
confondues, et l'ajoute a l'historique persistant (historique_meilleurs_taux.json).

Ajoute le 2026-09-30 a la demande de l'utilisatrice ("un historique des
meilleurs % pour chaque site") : une courbe par enseigne, quel que soit la
plateforme qui detient le meilleur taux ce jour-la.

Tourne en dernier dans le run automatique quotidien (.github/workflows/
update-data.yml), une fois que tous les fetch_*.py ont mis a jour les
fichiers de donnees. Reutilise exactement la meme logique de regroupement
d'alias et de gestion des offres boostees que le site (comparateur-
reductions.html), via le fichier partage merchant_groups.json — pour ne
jamais desynchroniser les deux (voir commentaire dans le HTML).

Detection des fichiers de donnees : tout fichier *.json du dossier qui a
une cle top-level "offers" est traite comme un fichier de donnees (pas
besoin de maintenir une seconde liste DATA_FILES en Python en plus de
celle du JS — un nouveau fichier de plateforme est pris en compte
automatiquement).

Idempotent : si le script tourne plusieurs fois le meme jour, la date du
jour est simplement remplacee dans l'historique de chaque enseigne (pas de
doublon).
"""
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
MERCHANT_GROUPS_FILE = REPO_DIR / "merchant_groups.json"
HISTORY_FILE = REPO_DIR / "historique_meilleurs_taux.json"
# Fichiers a ignorer meme s'ils sont en .json dans le dossier (pas des
# sources d'offres).
NON_DATA_FILES = {MERCHANT_GROUPS_FILE.name, HISTORY_FILE.name}


def normalize(s):
    """Equivalent Python de normalize() dans comparateur-reductions.html :
    minuscule + suppression des accents (NFD puis retrait des marques
    diacritiques)."""
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s


def load_merchant_groups():
    with open(MERCHANT_GROUPS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("aliases", {}), data.get("display_names", {})


def group_key_for(name, aliases):
    n = normalize(name).strip()
    return aliases.get(n, n)


def find_data_files():
    files = []
    for path in sorted(REPO_DIR.glob("*.json")):
        if path.name in NON_DATA_FILES:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and "offers" in data:
            files.append((path.name, data))
    return files


def is_boost_active(offer, now):
    if not offer.get("boosted"):
        return False
    expires_at = offer.get("expires_at")
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry > now


def apply_boost_overrides(offers, now):
    """Meme regle que applyBoostOverrides() dans le HTML : une offre
    boostee active remplace toute autre offre de la meme plateforme pour
    cette enseigne."""
    active_boost_platforms = {
        o["platform"] for o in offers if is_boost_active(o, now)
    }
    kept = []
    for o in offers:
        if o.get("boosted"):
            if is_boost_active(o, now):
                kept.append(o)
        elif o["platform"] not in active_boost_platforms:
            kept.append(o)
    return kept


def main():
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    aliases, display_names = load_merchant_groups()
    data_files = find_data_files()
    if not data_files:
        print("Aucun fichier de donnees trouve, rien a faire.", file=sys.stderr)
        return 1

    # Regroupe toutes les offres de tous les fichiers par cle canonique.
    groups = {}  # key -> {"raw_name": str, "offers": [...]}
    for filename, data in data_files:
        platform = data.get("platform") or filename
        for o in data.get("offers", []):
            raw_name = (o.get("name") or "").strip()
            if not raw_name:
                continue
            key = group_key_for(raw_name, aliases)
            if key not in groups:
                groups[key] = {"raw_name": raw_name, "offers": []}
            percent = o.get("percent")
            groups[key]["offers"].append({
                "platform": platform,
                "type": o.get("type"),
                "percent": percent if isinstance(percent, (int, float)) else None,
                "boosted": o.get("boosted") is True,
                "expires_at": o.get("expires_at"),
            })

    # Charge l'historique existant (vide au tout premier run).
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    else:
        history = {}

    updated = 0
    for key, group in groups.items():
        offers = apply_boost_overrides(group["offers"], now)
        with_percent = [o for o in offers if o["percent"] is not None]
        if not with_percent:
            # Rien de chiffrable aujourd'hui pour cette enseigne (ex. carte
            # cadeau a montant fixe uniquement) : pas de point ajoute, mais
            # on ne touche pas non plus a son historique existant.
            continue
        best = max(with_percent, key=lambda o: o["percent"])
        display_name = display_names.get(key, group["raw_name"])

        entry = history.setdefault(key, {"name": display_name, "history": []})
        entry["name"] = display_name  # toujours le plus recent connu
        points = entry["history"]
        # Idempotent : remplace le point du jour s'il existe deja (re-run
        # manuel le meme jour), sinon en ajoute un nouveau a la fin.
        if points and points[-1]["date"] == today:
            points[-1] = {
                "date": today,
                "percent": best["percent"],
                "type": best["type"],
                "platform": best["platform"],
            }
        else:
            points.append({
                "date": today,
                "percent": best["percent"],
                "type": best["type"],
                "platform": best["platform"],
            })
        updated += 1

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    print(f"Historique mis a jour pour {updated} enseignes ({today}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
