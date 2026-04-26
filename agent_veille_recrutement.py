"""
AGENT DE VEILLE RECRUTEMENT - BÉNIN & AFRIQUE FRANCOPHONE v4
=============================================================
Sources :
  - cDiscussion.com
  - emploibenin.com
  - emploibenin.net
  - novojob.com (Bénin)
  - bjemploi.com
  - jobbenin.com
  - concours.sn
  - Senjob.com
  - AfricaWork
  - Jobart Talent
  - ReliefWeb API
  - UNjobs
  - UNDP Jobs
"""

import os
import json
import hashlib
import logging
import argparse
import time
from datetime import datetime, date
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import anthropic
import schedule
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR  = Path("./site")
DATA_DIR    = Path("./data")
EXPORT_DIR  = Path("./exports")
LOG_FILE    = Path("./logs/veille.log")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

SECTEURS = [
    "Agriculture & Agri-business",
    "Santé & Nutrition",
    "ONG & Développement",
    "Finance & Microfinance",
    "Education & Formation",
    "Infrastructure & BTP",
    "Gouvernance & Institutions",
    "Environnement & Climat",
    "Numérique & Télécoms",
    "Autre",
]

SECTEUR_COLORS = {
    "Agriculture & Agri-business": "#1D9E75",
    "Santé & Nutrition":           "#D85A30",
    "ONG & Développement":         "#378ADD",
    "Finance & Microfinance":      "#EF9F27",
    "Education & Formation":       "#7F77DD",
    "Infrastructure & BTP":        "#888780",
    "Gouvernance & Institutions":  "#D4537E",
    "Environnement & Climat":      "#639922",
    "Numérique & Télécoms":        "#0F6E56",
    "Autre":                       "#5F5E5A",
}

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("veille")


# ──────────────────────────────────────────────
# HELPER GÉNÉRIQUE
# ──────────────────────────────────────────────

def generic_scrape(url, source_name, pays, selectors, base_url=""):
    """Scraper générique réutilisable pour tous les sites."""
    offers = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for sel in selectors:
            items = soup.select(sel)
            if items:
                for item in items[:30]:
                    titre_el = item.select_one("h2 a, h3 a, h4 a, a.title, .title a, a") or item
                    titre = titre_el.get_text(strip=True)[:200]
                    lien  = titre_el.get("href","") if titre_el.name == "a" else (item.select_one("a") or {}).get("href","")
                    if lien and not lien.startswith("http") and base_url:
                        lien = base_url + lien
                    org_el = item.select_one(".company, .employer, .org, .entreprise, .recruteur")
                    org = org_el.get_text(strip=True)[:100] if org_el else "Non précisé"
                    if titre and len(titre) > 5:
                        offers.append({"titre":titre,"org":org,"pays":pays,
                                       "url":lien or "","source":source_name,
                                       "raw_text":f"{titre} {org}"})
                break
    except Exception as e:
        log.warning(f"  {source_name}: {e}")
    return offers


# ──────────────────────────────────────────────
# SOURCES
# ──────────────────────────────────────────────

def scrape_cdiscussion() -> list[dict]:
    offers = []
    base = "https://www.cdiscussion.com"
    try:
        resp = requests.get(f"{base}/offre-d-emploi/", headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for h5 in soup.select("h5 a[href*='details-job']"):
            titre = h5.get_text(strip=True)
            lien  = h5.get("href","")
            if not lien.startswith("http"):
                lien = base + lien
            parent = h5.find_parent()
            for _ in range(5):
                if parent is None: break
                if any(x in parent.get_text() for x in ["Bénin","Togo","Cotonou"]): break
                parent = parent.find_parent()
            context = parent.get_text(" ", strip=True) if parent else ""
            lines = [l.strip() for l in context.split("\n") if l.strip()]
            org  = next((l for l in lines if l and l != titre and len(l)>3 and "Voir l'offre" not in l), "")
            pays = "Togo" if "Togo" in context else "Bénin"
            if titre and len(titre) > 5:
                offers.append({"titre":titre,"org":org or "Non précisé","pays":pays,
                                "url":lien,"source":"cDiscussion.com","raw_text":f"{titre} {org}"})
        log.info(f"  cDiscussion.com: {len(offers)} offres")
    except Exception as e:
        log.warning(f"  cDiscussion: {e}")
    return offers


def scrape_emploibenin_com() -> list[dict]:
    """emploibenin.com — principal site d'emploi béninois."""
    offers = []
    try:
        resp = requests.get("https://www.emploibenin.com/recherche-jobs-benin",
                            headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Cherche les blocs d'offres
        for item in soup.select(".job, .offer, .annonce, article, .item-job")[:30]:
            titre_el = item.select_one("h2, h3, h4, .title, a")
            if not titre_el: continue
            titre = titre_el.get_text(strip=True)[:200]
            lien_el = item.select_one("a")
            lien = lien_el.get("href","") if lien_el else ""
            if lien and not lien.startswith("http"):
                lien = "https://www.emploibenin.com" + lien
            org_el = item.select_one(".company, .employer, .recruteur, .org")
            org = org_el.get_text(strip=True) if org_el else "Non précisé"
            if titre and len(titre) > 8:
                offers.append({"titre":titre,"org":org,"pays":"Bénin",
                               "url":lien,"source":"EmploiBenin.com",
                               "raw_text":f"{titre} {org}"})
        # Fallback : cherche les liens contenant des mots-clés emploi
        if not offers:
            for a in soup.select("a[href*='emploi'], a[href*='job'], a[href*='recrutement']")[:30]:
                titre = a.get_text(strip=True)[:200]
                lien  = a.get("href","")
                if not lien.startswith("http"):
                    lien = "https://www.emploibenin.com" + lien
                if titre and len(titre) > 8:
                    offers.append({"titre":titre,"org":"Non précisé","pays":"Bénin",
                                   "url":lien,"source":"EmploiBenin.com","raw_text":titre})
        log.info(f"  EmploiBenin.com: {len(offers)} offres")
    except Exception as e:
        log.warning(f"  EmploiBenin.com: {e}")
    return offers


def scrape_emploibenin_net() -> list[dict]:
    """emploibenin.net — offres et concours Bénin."""
    offers = []
    try:
        resp = requests.get("https://emploibenin.net/", headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select("article, .post, .entry, .job, li.item")[:30]:
            titre_el = item.select_one("h2 a, h3 a, h4 a, .title a, a")
            if not titre_el: continue
            titre = titre_el.get_text(strip=True)[:200]
            lien  = titre_el.get("href","")
            if not lien.startswith("http"):
                lien = "https://emploibenin.net" + lien
            if titre and len(titre) > 8:
                offers.append({"titre":titre,"org":"Non précisé","pays":"Bénin",
                               "url":lien,"source":"EmploiBenin.net","raw_text":titre})
        log.info(f"  EmploiBenin.net: {len(offers)} offres")
    except Exception as e:
        log.warning(f"  EmploiBenin.net: {e}")
    return offers


def scrape_novojob() -> list[dict]:
    """novojob.com — offres Bénin et Côte d'Ivoire."""
    offers = []
    for url, pays in [
        ("https://www.novojob.com/benin/", "Bénin"),
        ("https://www.novojob.com/cote-d-ivoire/", "Côte d'Ivoire"),
    ]:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for item in soup.select(".job-item, .offer, article, .listing-item, li")[:20]:
                titre_el = item.select_one("h2 a, h3 a, a.title, .title, a")
                if not titre_el: continue
                titre = titre_el.get_text(strip=True)[:200]
                lien  = titre_el.get("href","") if titre_el.name == "a" else (item.select_one("a") or {}).get("href","")
                if lien and not lien.startswith("http"):
                    lien = "https://www.novojob.com" + lien
                org_el = item.select_one(".company, .employer")
                org = org_el.get_text(strip=True) if org_el else "Non précisé"
                if titre and len(titre) > 8:
                    offers.append({"titre":titre,"org":org,"pays":pays,
                                   "url":lien,"source":"Novojob.com","raw_text":f"{titre} {org}"})
            time.sleep(1)
        except Exception as e:
            log.warning(f"  Novojob {pays}: {e}")
    log.info(f"  Novojob.com: {len(offers)} offres")
    return offers


def scrape_bjemploi() -> list[dict]:
    """bjemploi.com — site béninois depuis 2008."""
    offers = []
    try:
        resp = requests.get("https://www.bjemploi.com/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select("article, .annonce, .job, .post, li.item")[:25]:
            titre_el = item.select_one("h2 a, h3 a, a.title, a")
            if not titre_el: continue
            titre = titre_el.get_text(strip=True)[:200]
            lien  = titre_el.get("href","")
            if lien and not lien.startswith("http"):
                lien = "https://www.bjemploi.com" + lien
            if titre and len(titre) > 8:
                offers.append({"titre":titre,"org":"Non précisé","pays":"Bénin",
                               "url":lien,"source":"BJEmploi.com","raw_text":titre})
        log.info(f"  BJEmploi.com: {len(offers)} offres")
    except Exception as e:
        log.warning(f"  BJEmploi: {e}")
    return offers


def scrape_jobbenin() -> list[dict]:
    """jobbenin.com — nouveau site béninois."""
    offers = []
    try:
        resp = requests.get("https://jobbenin.com/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".job, .offer, article, .card, .listing")[:25]:
            titre_el = item.select_one("h2, h3, h4, .title, a")
            if not titre_el: continue
            titre = titre_el.get_text(strip=True)[:200]
            lien_el = item.select_one("a")
            lien = lien_el.get("href","") if lien_el else ""
            if lien and not lien.startswith("http"):
                lien = "https://jobbenin.com" + lien
            org_el = item.select_one(".company, .employer, .org")
            org = org_el.get_text(strip=True) if org_el else "Non précisé"
            if titre and len(titre) > 8:
                offers.append({"titre":titre,"org":org,"pays":"Bénin",
                               "url":lien,"source":"JobBenin.com","raw_text":f"{titre} {org}"})
        log.info(f"  JobBenin.com: {len(offers)} offres")
    except Exception as e:
        log.warning(f"  JobBenin: {e}")
    return offers


def scrape_concours_sn() -> list[dict]:
    """concours.sn — concours, bourses et emplois Sénégal/Afrique."""
    offers = []
    try:
        resp = requests.get("https://www.concours.sn/offres-emploi/",
                            headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select("article, .post, .job, li.item")[:25]:
            titre_el = item.select_one("h2 a, h3 a, h4 a, a.title, a")
            if not titre_el: continue
            titre = titre_el.get_text(strip=True)[:200]
            lien  = titre_el.get("href","")
            if lien and not lien.startswith("http"):
                lien = "https://www.concours.sn" + lien
            if titre and len(titre) > 8:
                offers.append({"titre":titre,"org":"Non précisé","pays":"Sénégal",
                               "url":lien,"source":"Concours.sn","raw_text":titre})
        log.info(f"  Concours.sn: {len(offers)} offres")
    except Exception as e:
        log.warning(f"  Concours.sn: {e}")
    return offers


def scrape_senjob() -> list[dict]:
    """Senjob.com — Afrique de l'Ouest francophone."""
    offers = []
    try:
        resp = requests.get("https://senjob.com/offres-d-emploi.php",
                            headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".job-listing, .offre, article, .job_listing, li")[:25]:
            titre_el = item.select_one("h2, h3, .job-title, a")
            if not titre_el: continue
            titre = titre_el.get_text(strip=True)[:200]
            lien_el = item.select_one("a")
            lien = lien_el.get("href","") if lien_el else ""
            if lien and not lien.startswith("http"):
                lien = "https://senjob.com" + lien
            org_el = item.select_one(".company, .org, .entreprise")
            org = org_el.get_text(strip=True) if org_el else "Non précisé"
            pays_el = item.select_one(".location, .pays")
            pays = pays_el.get_text(strip=True) if pays_el else "Sénégal"
            if titre and len(titre) > 5:
                offers.append({"titre":titre,"org":org,"pays":pays,
                                "url":lien,"source":"Senjob.com","raw_text":f"{titre} {org}"})
        log.info(f"  Senjob.com: {len(offers)} offres")
    except Exception as e:
        log.warning(f"  Senjob: {e}")
    return offers


def scrape_africawork() -> list[dict]:
    """AfricaWork — pan-africain francophone."""
    offers = []
    pages = [
        ("https://www.africawork.com/offres-emploi-benin", "Bénin"),
        ("https://www.africawork.com/offres-emploi-togo", "Togo"),
        ("https://www.africawork.com/offres-emploi-senegal", "Sénégal"),
        ("https://www.africawork.com/offres-emploi-cote-ivoire", "Côte d'Ivoire"),
        ("https://www.africawork.com/offres-emploi-burkina-faso", "Burkina Faso"),
    ]
    for url, pays in pages:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            for item in soup.select(".job-item, .offer-item, article.job, .job_listing")[:15]:
                titre_el = item.select_one("h2, h3, .title, a")
                if not titre_el: continue
                titre = titre_el.get_text(strip=True)[:200]
                lien_el = item.select_one("a")
                lien = lien_el.get("href","") if lien_el else ""
                if lien and not lien.startswith("http"):
                    lien = "https://www.africawork.com" + lien
                org_el = item.select_one(".company, .employer")
                org = org_el.get_text(strip=True) if org_el else "Non précisé"
                if titre and len(titre) > 5:
                    offers.append({"titre":titre,"org":org,"pays":pays,
                                   "url":lien,"source":"AfricaWork","raw_text":f"{titre} {org}"})
            time.sleep(1)
        except Exception as e:
            log.warning(f"  AfricaWork {pays}: {e}")
    log.info(f"  AfricaWork: {len(offers)} offres")
    return offers


def scrape_jobartalent() -> list[dict]:
    """Jobart Talent — multi-pays africains."""
    offers = []
    try:
        resp = requests.get("https://www.jobartalent.com/offres-emploi",
                            headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".job-item, .offer, article, .listing")[:25]:
            titre_el = item.select_one("h2, h3, .title, a")
            if not titre_el: continue
            titre = titre_el.get_text(strip=True)[:200]
            lien_el = item.select_one("a")
            lien = lien_el.get("href","") if lien_el else ""
            if lien and not lien.startswith("http"):
                lien = "https://www.jobartalent.com" + lien
            org_el  = item.select_one(".company, .org")
            org     = org_el.get_text(strip=True) if org_el else "Non précisé"
            pays_el = item.select_one(".location, .country")
            pays    = pays_el.get_text(strip=True) if pays_el else "Afrique"
            if titre and len(titre) > 5:
                offers.append({"titre":titre,"org":org,"pays":pays,
                               "url":lien,"source":"Jobart Talent","raw_text":f"{titre} {org}"})
        log.info(f"  Jobart Talent: {len(offers)} offres")
    except Exception as e:
        log.warning(f"  Jobart Talent: {e}")
    return offers


def scrape_reliefweb() -> list[dict]:
    """ReliefWeb API — offres humanitaires/ONG."""
    offers = []
    try:
        resp = requests.get(
            "https://api.reliefweb.int/v1/jobs",
            params={"appname":"veille-emploi-afrique","limit":50,"sort[]":"date:desc",
                    "fields[include][]":["title","body","source","country","date","url"]},
            headers=HEADERS, timeout=20
        )
        resp.raise_for_status()
        items = resp.json().get("data",[])
        log.info(f"  ReliefWeb API: {len(items)} offres")
        for item in items:
            f = item.get("fields",{})
            titre = f.get("title","")
            if not titre: continue
            sources   = f.get("source",[{}])
            org       = sources[0].get("name","Non précisé") if sources else "Non précisé"
            pays_list = f.get("country",[{}])
            pays      = pays_list[0].get("name","Afrique") if pays_list else "Afrique"
            offers.append({"titre":titre,"org":org,"pays":pays,
                           "url":f.get("url",""),"source":"ReliefWeb",
                           "raw_text":f"{titre} {org} {f.get('body','')[:200]}"})
    except Exception as e:
        log.error(f"  ReliefWeb: {e}")
    return offers


def scrape_all() -> list[dict]:
    """Lance toutes les sources."""
    log.info("=== Début de la collecte ===")
    all_offers = []

    sources = [
        ("cDiscussion.com",    scrape_cdiscussion),
        ("EmploiBenin.com",    scrape_emploibenin_com),
        ("EmploiBenin.net",    scrape_emploibenin_net),
        ("Novojob.com",        scrape_novojob),
        ("BJEmploi.com",       scrape_bjemploi),
        ("JobBenin.com",       scrape_jobbenin),
        ("Concours.sn",        scrape_concours_sn),
        ("Senjob.com",         scrape_senjob),
        ("AfricaWork",         scrape_africawork),
        ("Jobart Talent",      scrape_jobartalent),
        ("ReliefWeb API",      scrape_reliefweb),
    ]

    for nom, fn in sources:
        log.info(f"Scraping : {nom}")
        try:
            results = fn()
            all_offers.extend(results)
        except Exception as e:
            log.warning(f"  {nom} échoué: {e}")
        time.sleep(2)

    # Déduplique par titre similaire
    seen_titres = set()
    unique = []
    for o in all_offers:
        key = o["titre"][:50].lower().strip()
        if key not in seen_titres:
            seen_titres.add(key)
            unique.append(o)

    log.info(f"Total brut : {len(all_offers)} offres → {len(unique)} après déduplication titres")
    return unique


# ──────────────────────────────────────────────
# CLASSIFICATION IA
# ──────────────────────────────────────────────

def classify_and_enrich_with_claude(offers: list[dict]) -> list[dict]:
    if not offers: return []
    client   = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY",""))
    enriched = []

    for i in range(0, len(offers), 20):
        batch = offers[i:i+20]
        items_json = json.dumps([
            {"id":idx,"titre":o["titre"],"org":o.get("org",""),"pays":o.get("pays","")}
            for idx,o in enumerate(batch)
        ], ensure_ascii=False)
        prompt = f"""Classe ces offres d'emploi Afrique francophone.
{items_json}
Pour chaque offre JSON :
- id, secteur (parmi {json.dumps(SECTEURS)}),
  type_contrat (CDI/CDD/Consultance/Stage/Inconnu),
  pays_detecte, resume (max 15 mots fr), pertinence_score (1-5)
Réponds UNIQUEMENT tableau JSON valide."""
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-5", max_tokens=2000,
                messages=[{"role":"user","content":prompt}]
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"): raw = raw[4:]
            for item in json.loads(raw):
                idx = item.get("id",0)
                if idx < len(batch):
                    o = dict(batch[idx])
                    o.update({
                        "secteur":          item.get("secteur","Autre"),
                        "type_contrat":     item.get("type_contrat","Inconnu"),
                        "pays":             item.get("pays_detecte", o.get("pays","")),
                        "resume":           item.get("resume",""),
                        "pertinence_score": item.get("pertinence_score",3),
                        "id":               hashlib.md5((o["titre"]+o.get("org","")).encode()).hexdigest()[:8],
                        "date_collecte":    date.today().isoformat(),
                    })
                    enriched.append(o)
            log.info(f"  Batch {i//20+1} classifié ({len(batch)} offres)")
            time.sleep(0.5)
        except Exception as e:
            log.error(f"Erreur Claude batch {i//20+1}: {e}")
            for o in batch:
                o.update({"secteur":"Autre","type_contrat":"Inconnu",
                           "resume":o["titre"][:80],
                           "id":hashlib.md5(o["titre"].encode()).hexdigest()[:8],
                           "date_collecte":date.today().isoformat(),
                           "pertinence_score":2})
                enriched.append(o)

    enriched = [o for o in enriched if o.get("pertinence_score",0) >= 2]
    log.info(f"Après filtre qualité : {len(enriched)} offres retenues")
    return enriched


# ──────────────────────────────────────────────
# DÉDUPLICATION
# ──────────────────────────────────────────────

def deduplicate(offers, history_file):
    history = {}
    if history_file.exists():
        history = json.loads(history_file.read_text(encoding="utf-8"))
    new_offers = [o for o in offers if o["id"] not in history]
    cutoff = date.today().isoformat()[:7]
    new_history = {k:v for k,v in history.items() if v.get("date","")[:7] >= cutoff}
    for o in offers:
        new_history[o["id"]] = {"titre":o["titre"][:60],"date":o["date_collecte"]}
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(json.dumps(new_history,ensure_ascii=False,indent=2),encoding="utf-8")
    log.info(f"{len(new_offers)} nouvelles offres (sur {len(offers)})")
    return offers, new_offers


# ──────────────────────────────────────────────
# RÉSUMÉ ÉDITORIAL
# ──────────────────────────────────────────────

def generate_daily_summary(offers):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY",""))
    by_sector = {}
    for o in offers:
        by_sector.setdefault(o.get("secteur","Autre"),[]).append(o)
    sector_summary = {s:[o["titre"] for o in lst[:5]] for s,lst in by_sector.items()}
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=400,
            messages=[{"role":"user","content":
                f"Résumé éditorial 3-5 phrases bulletin veille emploi Afrique francophone. "
                f"Offres: {json.dumps(sector_summary,ensure_ascii=False)}. "
                f"Cite secteurs actifs, organisations, tendance. Français uniquement."}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"Erreur résumé: {e}")
        return f"Bulletin du {date.today().strftime('%d/%m/%Y')} — {len(offers)} offres collectées."


# ──────────────────────────────────────────────
# GÉNÉRATION HTML
# ──────────────────────────────────────────────

def generate_html_site(offers, new_ids, summary):
    by_sector = {}
    for o in offers:
        by_sector.setdefault(o.get("secteur","Autre"),[]).append(o)
    today_str  = datetime.now().strftime("%d %B %Y à %H:%M")
    nb_sources = len(set(o.get("source","") for o in offers))

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VeilleEmploi Afrique — {date.today().strftime('%d/%m/%Y')}</title>
<style>
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:#F8F7F2;color:#2C2C2A;line-height:1.6}}
  a{{color:inherit;text-decoration:none}}
  .site-header{{background:#fff;border-bottom:1px solid #E0DED8;padding:1rem 2rem;position:sticky;top:0;z-index:100;display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap}}
  .logo{{font-size:1.4rem;font-weight:600;color:#0F6E56}}.logo span{{color:#2C2C2A}}
  .update-badge{{font-size:.78rem;background:#EAF3DE;color:#3B6D11;padding:4px 12px;border-radius:20px}}
  .hero{{background:linear-gradient(135deg,#0F6E56 0%,#1D9E75 100%);color:white;padding:2.5rem 2rem 2rem}}
  .hero h1{{font-size:1.8rem;font-weight:700;margin-bottom:.5rem}}
  .hero p{{opacity:.9;font-size:1rem;max-width:600px}}
  .stats{{display:flex;gap:1rem;margin-top:1.5rem;flex-wrap:wrap}}
  .stat{{background:rgba(255,255,255,.15);border-radius:10px;padding:.75rem 1.25rem;min-width:110px}}
  .stat-n{{font-size:1.8rem;font-weight:700}}.stat-l{{font-size:.8rem;opacity:.85}}
  .abed-banner{{background:#fff;margin:1.5rem 2rem;padding:1rem 1.5rem;border-radius:12px;border:1px solid #1D9E75;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}}
  .abed-banner p{{font-size:.9rem;color:#0F6E56;font-weight:500}}
  .abed-btn{{background:#0F6E56;color:white;padding:8px 20px;border-radius:6px;font-size:.85rem;font-weight:600}}
  .abed-btn:hover{{background:#1D9E75}}
  .summary-box{{background:#fff;margin:0 2rem 1.5rem;padding:1.25rem 1.5rem;border-radius:12px;border-left:4px solid #1D9E75;font-size:.95rem;line-height:1.7}}
  .summary-box strong{{color:#0F6E56}}
  .main{{max-width:1100px;margin:0 auto;padding:0 1.5rem 3rem}}
  .sector-section{{margin:2rem 0}}
  .sector-header{{display:flex;align-items:center;gap:10px;margin-bottom:1rem}}
  .sector-dot{{width:12px;height:12px;border-radius:50%;flex-shrink:0}}
  .sector-name{{font-size:1rem;font-weight:600}}
  .sector-count{{font-size:.8rem;background:#F1EFE8;color:#5F5E5A;padding:2px 8px;border-radius:10px}}
  .offers-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}}
  .offer-card{{background:#fff;border:1px solid #E0DED8;border-radius:12px;padding:1rem 1.25rem;transition:border-color .2s,box-shadow .2s;display:flex;flex-direction:column}}
  .offer-card:hover{{border-color:#1D9E75;box-shadow:0 2px 12px rgba(29,158,117,.1)}}
  .offer-card.is-new{{border-left:3px solid #1D9E75}}
  .offer-title{{font-size:.95rem;font-weight:600;color:#1a1a18;margin-bottom:4px}}
  .offer-org{{font-size:.85rem;color:#5F5E5A;margin-bottom:6px}}
  .offer-resume{{font-size:.82rem;color:#555;margin-bottom:8px;font-style:italic}}
  .tags{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}}
  .tag{{font-size:.72rem;padding:2px 8px;border-radius:4px}}
  .tag-pays{{background:#E6F1FB;color:#185FA5}}
  .tag-type{{background:#FAEEDA;color:#854F0B}}
  .tag-new{{background:#EAF3DE;color:#3B6D11;font-weight:600}}
  .offer-footer{{display:flex;align-items:center;justify-content:space-between;margin-top:auto;padding-top:10px;border-top:1px solid #F1EFE8;flex-wrap:wrap;gap:6px}}
  .offer-source{{font-size:.72rem;color:#aaa}}
  .btn-voir{{display:inline-block;font-size:.82rem;font-weight:600;color:#0F6E56;background:#E1F5EE;padding:5px 14px;border-radius:6px;border:1px solid #1D9E75;transition:background .2s}}
  .btn-voir:hover{{background:#1D9E75;color:white}}
  .btn-voir-off{{font-size:.78rem;color:#ccc;font-style:italic}}
  .footer{{text-align:center;padding:2rem;font-size:.8rem;color:#888;border-top:1px solid #E0DED8;background:#fff;margin-top:2rem}}
  .footer a{{color:#0F6E56;text-decoration:underline}}
  @media(max-width:600px){{
    .hero h1{{font-size:1.3rem}}
    .summary-box,.abed-banner{{margin:1rem}}
    .offers-grid{{grid-template-columns:1fr}}
    .site-header{{padding:.75rem 1rem}}
  }}
</style>
</head>
<body>
<header class="site-header">
  <div class="logo">Veille<span>Emploi</span></div>
  <div class="update-badge">Mis à jour le {today_str}</div>
</header>
<div class="hero">
  <h1>Appels à recrutement — Afrique francophone</h1>
  <p>Bénin, Togo, Sénégal, Côte d'Ivoire et plus — offres classées par secteur</p>
  <div class="stats">
    <div class="stat"><div class="stat-n">{len(offers)}</div><div class="stat-l">Offres du jour</div></div>
    <div class="stat"><div class="stat-n">{len(new_ids)}</div><div class="stat-l">Nouvelles</div></div>
    <div class="stat"><div class="stat-n">{len(by_sector)}</div><div class="stat-l">Secteurs</div></div>
    <div class="stat"><div class="stat-n">{len(set(o.get('pays','') for o in offers))}</div><div class="stat-l">Pays</div></div>
    <div class="stat"><div class="stat-n">{nb_sources}</div><div class="stat-l">Sources</div></div>
  </div>
</div>
<div class="abed-banner">
  <p>🎓 Prépare-toi aux postes du marché avec <strong>ABED Academy</strong> — Formations professionnelles au Bénin</p>
  <a href="https://academy.abedong.org" target="_blank" rel="noopener" class="abed-btn">Découvrir les formations →</a>
</div>
<div class="summary-box">
  <strong>Résumé du {date.today().strftime('%d/%m/%Y')} :</strong> {summary}
</div>
<main class="main">
"""

    for sector, sector_offers in sorted(by_sector.items(), key=lambda x: -len(x[1])):
        color = SECTEUR_COLORS.get(sector,"#888")
        html += f"""
  <section class="sector-section">
    <div class="sector-header">
      <div class="sector-dot" style="background:{color}"></div>
      <div class="sector-name">{sector}</div>
      <div class="sector-count">{len(sector_offers)} offre(s)</div>
    </div>
    <div class="offers-grid">
"""
        for o in sector_offers:
            is_new  = "is-new" if o["id"] in new_ids else ""
            new_tag = '<span class="tag tag-new">Nouveau</span>' if o["id"] in new_ids else ""
            url     = o.get("url","") or ""
            btn     = (f'<a href="{url}" target="_blank" rel="noopener" class="btn-voir">Voir l\'offre complète →</a>'
                       if url and url != "#" else '<span class="btn-voir-off">Lien non disponible</span>')
            html += f"""      <div class="offer-card {is_new}">
        <div>
          <div class="offer-title">{o['titre'][:120]}</div>
          <div class="offer-org">{o.get('org','')[:80]}</div>
          <div class="offer-resume">{o.get('resume','')}</div>
          <div class="tags">
            <span class="tag tag-pays">{o.get('pays','')}</span>
            <span class="tag tag-type">{o.get('type_contrat','')}</span>
            {new_tag}
          </div>
        </div>
        <div class="offer-footer">
          <div class="offer-source">Source : {o.get('source','')}</div>
          {btn}
        </div>
      </div>
"""
        html += "    </div>\n  </section>\n"

    sources_list = " · ".join(sorted(set(o.get("source","") for o in offers)))
    html += f"""
</main>
<footer class="footer">
  VeilleEmploi Afrique — Mise à jour automatique (lundi–jeudi) — {today_str}<br>
  Sources : {sources_list}<br>
  Une initiative <a href="https://academy.abedong.org" target="_blank">ABED Academy</a>
</footer>
</body></html>
"""
    return html


# ──────────────────────────────────────────────
# DONNÉES MOCK
# ──────────────────────────────────────────────

MOCK_OFFERS = [
    {"titre":"Coordinateur de projets agricoles","org":"FAO Bénin","pays":"Bénin",
     "url":"https://www.cdiscussion.com/offre-d-emploi/?details-job=1137292","source":"cDiscussion.com","raw_text":"Coordinateur projets agricoles FAO"},
    {"titre":"Assistant en suivi-évaluation MEAL","org":"Save the Children","pays":"Bénin",
     "url":"https://reliefweb.int/job/1234568","source":"ReliefWeb","raw_text":"Assistant suivi évaluation MEAL ONG indicateurs"},
    {"titre":"Comptable de projet ONG","org":"UNCDF","pays":"Sénégal",
     "url":"https://jobs.undp.org","source":"UNDP Jobs","raw_text":"Comptable projet finance budget bailleurs fonds ONG"},
    {"titre":"Animateur en nutrition communautaire","org":"Helen Keller International","pays":"Niger",
     "url":"https://reliefweb.int/job/1234569","source":"ReliefWeb","raw_text":"Animateur nutrition communautaire santé ANJE"},
    {"titre":"Conseiller junior en éducation financière","org":"CLCAM Bénin","pays":"Bénin",
     "url":"https://www.cdiscussion.com/offre-d-emploi/?details-job=1137296","source":"cDiscussion.com","raw_text":"Conseiller education financière microfinance épargne"},
    {"titre":"Agent de vulgarisation agricole","org":"Ministère Agriculture","pays":"Bénin",
     "url":"https://www.emploibenin.com/job/123","source":"EmploiBenin.com","raw_text":"Agent vulgarisation conseil agricole terrain producteurs"},
    {"titre":"Conseiller technique transformation agroalimentaire","org":"GIZ Togo","pays":"Togo",
     "url":"https://reliefweb.int/job/1234570","source":"ReliefWeb","raw_text":"Conseiller technique transformation agroalimentaire qualité"},
    {"titre":"Assistant administratif et financier","org":"ONG Plan International","pays":"Togo",
     "url":"https://senjob.com/job/12345","source":"Senjob.com","raw_text":"Assistant administratif financier gestion ONG"},
    {"titre":"Chargé de programme développement rural","org":"SNV Bénin","pays":"Bénin",
     "url":"https://www.novojob.com/benin/job/456","source":"Novojob.com","raw_text":"Chargé programme développement rural agriculture"},
    {"titre":"Conseil agricole en agroécologie","org":"SNV Bénin","pays":"Bénin",
     "url":"https://africawork.com/job/12345","source":"AfricaWork","raw_text":"Conseil agricole agroécologie pratiques durables producteurs"},
]


# ──────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

def run_pipeline(test_mode: bool = False):
    log.info(f"╔══ DÉBUT PIPELINE — {datetime.now().strftime('%Y-%m-%d %H:%M')} ══╗")

    if test_mode:
        log.info("Mode TEST : données mock")
        raw_offers = MOCK_OFFERS
    else:
        raw_offers = scrape_all()

    if not raw_offers:
        log.warning("Aucune offre collectée.")
        return

    if test_mode:
        secteurs_test = ["Agriculture & Agri-business","ONG & Développement",
                         "Finance & Microfinance","Santé & Nutrition","Education & Formation"]
        offers = []
        for i, o in enumerate(raw_offers):
            offers.append({**o,
                "secteur":          secteurs_test[i % len(secteurs_test)],
                "type_contrat":     ["CDI","CDD","Consultance"][i%3],
                "resume":           o["titre"][:60],
                "pertinence_score": 4,
                "id":               hashlib.md5(o["titre"].encode()).hexdigest()[:8],
                "date_collecte":    date.today().isoformat(),
            })
    else:
        offers = classify_and_enrich_with_claude(raw_offers)

    history_file = DATA_DIR / "history.json"
    offers, new_offers = deduplicate(offers, history_file)
    new_ids = {o["id"] for o in new_offers}

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"offers_{date.today().isoformat()}.json").write_text(
        json.dumps(offers,ensure_ascii=False,indent=2), encoding="utf-8"
    )

    if test_mode:
        nb_src = len(set(o.get('source','') for o in offers))
        summary = f"Bulletin test du {date.today().strftime('%d/%m/%Y')} — {len(offers)} offres simulées depuis {nb_src} sources."
    else:
        summary = generate_daily_summary(offers)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "index.html").write_text(
        generate_html_site(offers, new_ids, summary), encoding="utf-8"
    )
    nb_src = len(set(o.get('source','') for o in offers))
    log.info(f"Site généré : {len(offers)} offres, {nb_src} sources actives")

    try:
        from email_marketing import run_marketing_pipeline
        run_marketing_pipeline(offers, EXPORT_DIR)
    except Exception as e:
        log.error(f"Erreur pipeline marketing: {e}")

    log.info(f"╚══ FIN PIPELINE — {len(offers)} offres | {len(new_offers)} nouvelles ══╝")
    return offers


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.once or args.test:
        run_pipeline(test_mode=args.test)
    else:
        log.info("Agent planifié — lundi-jeudi à 06:00")
        run_pipeline()
        schedule.every().day.at("06:00").do(run_pipeline)
        while True:
            schedule.run_pending()
            time.sleep(60)
