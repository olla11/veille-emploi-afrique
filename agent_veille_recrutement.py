"""
AGENT DE VEILLE RECRUTEMENT - BÉNIN & AFRIQUE FRANCOPHONE
==========================================================
Sources :
  - cdiscussion.com        (offres Bénin/Togo en direct)
  - API ReliefWeb          (offres humanitaires/ONG Afrique)
  - UNDP Jobs              (offres Nations Unies)

INSTALLATION:
    pip install requests beautifulsoup4 anthropic schedule python-dotenv

CONFIGURATION:
    Créer un fichier .env avec: ANTHROPIC_API_KEY=sk-ant-...

LANCEMENT:
    python agent_veille_recrutement_v2.py --once    # une seule exécution
    python agent_veille_recrutement_v2.py --test    # mode test (données mock)
    python agent_veille_recrutement_v2.py           # boucle quotidienne 06h00
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

# ──────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────

OUTPUT_DIR = Path("./site")
DATA_DIR   = Path("./data")
LOG_FILE   = Path("./logs/veille.log")

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

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────

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
# SOURCE 1 : cdiscussion.com
# ──────────────────────────────────────────────

def scrape_cdiscussion() -> list[dict]:
    """Scrape les offres d'emploi sur cdiscussion.com (Bénin/Togo)."""
    offers = []
    base = "https://www.cdiscussion.com"
    url  = f"{base}/offre-d-emploi/"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Chaque offre est dans un bloc avec un <h5> et un lien "Voir l'offre"
        for h5 in soup.select("h5 a[href*='details-job']"):
            titre = h5.get_text(strip=True)
            lien  = h5.get("href", "")
            if not lien.startswith("http"):
                lien = base + lien

            # Cherche le conteneur parent pour extraire org et pays
            parent = h5.find_parent()
            for _ in range(5):
                if parent is None:
                    break
                text = parent.get_text(" ", strip=True)
                if "Bénin" in text or "Togo" in text or "Cotonou" in text:
                    break
                parent = parent.find_parent()

            context = parent.get_text(" ", strip=True) if parent else ""
            # Extrait l'organisation (ligne après le titre)
            lines = [l.strip() for l in context.split("\n") if l.strip()]
            org = ""
            for line in lines:
                if line and line != titre and len(line) > 3 and "Voir l'offre" not in line:
                    org = line[:100]
                    break

            # Détermine le pays
            if "Togo" in context:
                pays = "Togo"
            else:
                pays = "Bénin"

            if titre and len(titre) > 5:
                offers.append({
                    "titre":    titre,
                    "org":      org or "Non précisé",
                    "pays":     pays,
                    "url":      lien,
                    "source":   "cDiscussion.com",
                    "raw_text": f"{titre} {org}",
                })

        log.info(f"  cDiscussion.com: {len(offers)} offres trouvées")

    except Exception as e:
        log.warning(f"  Erreur cDiscussion: {e}")

    return offers


# ──────────────────────────────────────────────
# SOURCE 2 : API ReliefWeb
# ──────────────────────────────────────────────

def scrape_reliefweb() -> list[dict]:
    """Collecte les offres via l'API gratuite ReliefWeb."""
    offers = []

    # IDs pays ReliefWeb : Bénin=20, Togo=226, Sénégal=188,
    # Côte d'Ivoire=48, Burkina Faso=33, Niger=154, Mali=130
    url = "https://api.reliefweb.int/v1/reports"
    params = {
        "appname": "veille-emploi-afrique",
        "limit": 50,
        "sort[]": "date:desc",
        "fields[include][]": ["title", "body", "source", "country", "date", "url"],
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data  = resp.json()
        items = data.get("data", [])
        log.info(f"  ReliefWeb API: {len(items)} offres trouvées")

        for item in items:
            fields    = item.get("fields", {})
            titre     = fields.get("title", "")
            sources   = fields.get("source", [{}])
            org       = sources[0].get("name", "Non précisé") if sources else "Non précisé"
            pays_list = fields.get("country", [{}])
            pays      = pays_list[0].get("name", "Afrique") if pays_list else "Afrique"
            lien      = fields.get("url", "")
            body      = fields.get("body", "")[:300]

            if not titre:
                continue

            offers.append({
                "titre":    titre,
                "org":      org,
                "pays":     pays,
                "url":      lien,
                "source":   "ReliefWeb",
                "raw_text": f"{titre} {org} {body}",
            })

    except Exception as e:
        log.error(f"  Erreur API ReliefWeb: {e}")

    return offers


# ──────────────────────────────────────────────
# SOURCE 3 : UNDP Jobs
# ──────────────────────────────────────────────

def scrape_undp() -> list[dict]:
    """Scrape les offres UNDP pour l'Afrique de l'Ouest."""
    offers = []
    try:
        url  = "https://jobs.undp.org/cj_view_jobs.cfm"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        for row in soup.select("table tr")[1:30]:
            cells   = row.find_all("td")
            if len(cells) < 2:
                continue
            titre   = cells[0].get_text(strip=True)
            lien_el = cells[0].find("a")
            lien    = "https://jobs.undp.org" + lien_el["href"] if lien_el else ""
            pays    = cells[-1].get_text(strip=True) if cells else "Afrique"

            if titre and len(titre) > 5:
                offers.append({
                    "titre":    titre,
                    "org":      "UNDP",
                    "pays":     pays,
                    "url":      lien,
                    "source":   "UNDP Jobs",
                    "raw_text": titre,
                })

        log.info(f"  UNDP Jobs: {len(offers)} offres trouvées")

    except Exception as e:
        log.warning(f"  Erreur UNDP: {e}")

    return offers


# ──────────────────────────────────────────────
# COLLECTE PRINCIPALE
# ──────────────────────────────────────────────

def scrape_all() -> list[dict]:
    """Lance toutes les sources de collecte."""
    log.info("=== Début de la collecte ===")
    all_offers = []

    log.info("Scraping : cDiscussion.com")
    all_offers.extend(scrape_cdiscussion())
    time.sleep(2)

    log.info("Scraping : ReliefWeb API")
    all_offers.extend(scrape_reliefweb())
    time.sleep(1)

    log.info("Scraping : UNDP Jobs")
    all_offers.extend(scrape_undp())

    log.info(f"Total brut : {len(all_offers)} offres collectées")
    return all_offers


# ──────────────────────────────────────────────
# CLASSIFICATION IA (CLAUDE)
# ──────────────────────────────────────────────

def classify_and_enrich_with_claude(offers: list[dict]) -> list[dict]:
    """Envoie les offres à Claude pour classification et enrichissement."""
    if not offers:
        return []

    client    = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    enriched  = []
    batch_size = 20

    for i in range(0, len(offers), batch_size):
        batch      = offers[i:i+batch_size]
        items_json = json.dumps([
            {"id": idx, "titre": o["titre"], "org": o.get("org",""), "pays": o.get("pays","")}
            for idx, o in enumerate(batch)
        ], ensure_ascii=False)

        prompt = f"""Tu es un expert en recrutement en Afrique francophone (Bénin, Togo, Sénégal...).

Voici une liste d'offres d'emploi/consultance :
{items_json}

Pour CHAQUE offre, retourne un JSON avec :
- id (même que l'entrée)
- secteur : UN parmi {json.dumps(SECTEURS, ensure_ascii=False)}
- type_contrat : "CDI", "CDD", "Consultance", "Stage", ou "Inconnu"
- pays_detecte : pays principal (garde celui fourni si précis)
- resume : 1 phrase courte (max 15 mots) décrivant le poste en français
- pertinence_score : entier 1-5 (5=très pertinent pour Afrique francophone)

Réponds UNIQUEMENT avec un tableau JSON valide, sans commentaire ni markdown.
"""

        try:
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role":"user","content":prompt}]
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            classifications = json.loads(raw)

            for item in classifications:
                idx = item.get("id", 0)
                if idx < len(batch):
                    offer = dict(batch[idx])
                    offer["secteur"]          = item.get("secteur", "Autre")
                    offer["type_contrat"]     = item.get("type_contrat", "Inconnu")
                    offer["pays"]             = item.get("pays_detecte", offer.get("pays",""))
                    offer["resume"]           = item.get("resume","")
                    offer["pertinence_score"] = item.get("pertinence_score", 3)
                    offer["id"]               = hashlib.md5(
                        (offer["titre"]+offer.get("org","")).encode()
                    ).hexdigest()[:8]
                    offer["date_collecte"]    = date.today().isoformat()
                    enriched.append(offer)

            log.info(f"  Batch {i//batch_size+1} classifié ({len(classifications)} offres)")
            time.sleep(0.5)

        except Exception as e:
            log.error(f"Erreur Claude classification: {e}")
            for offer in batch:
                offer["secteur"]       = "Autre"
                offer["type_contrat"]  = "Inconnu"
                offer["resume"]        = offer["titre"][:80]
                offer["id"]            = hashlib.md5(offer["titre"].encode()).hexdigest()[:8]
                offer["date_collecte"] = date.today().isoformat()
                enriched.append(offer)

    # Filtre qualité
    enriched = [o for o in enriched if o.get("pertinence_score",0) >= 2]
    log.info(f"Après filtre qualité : {len(enriched)} offres retenues")
    return enriched


# ──────────────────────────────────────────────
# DÉDUPLICATION
# ──────────────────────────────────────────────

def deduplicate(offers: list[dict], history_file: Path):
    """Détecte les nouvelles offres par rapport à l'historique."""
    history = {}
    if history_file.exists():
        history = json.loads(history_file.read_text(encoding="utf-8"))

    new_offers = [o for o in offers if o["id"] not in history]

    cutoff = date.today().isoformat()[:7]
    new_history = {oid: info for oid, info in history.items()
                   if info.get("date","")[:7] >= cutoff}
    for o in offers:
        new_history[o["id"]] = {"titre": o["titre"][:60], "date": o["date_collecte"]}

    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(
        json.dumps(new_history, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log.info(f"{len(new_offers)} nouvelles offres (sur {len(offers)} collectées)")
    return offers, new_offers


# ──────────────────────────────────────────────
# RÉSUMÉ ÉDITORIAL
# ──────────────────────────────────────────────

def generate_daily_summary(offers: list[dict]) -> str:
    """Génère un paragraphe de résumé éditorial avec Claude."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY",""))

    by_sector = {}
    for o in offers:
        s = o.get("secteur","Autre")
        by_sector.setdefault(s, []).append(o)

    sector_summary = {s: [o["titre"] for o in lst[:5]] for s, lst in by_sector.items()}

    prompt = f"""Tu es éditeur d'un bulletin de veille emploi pour l'Afrique francophone.

Voici les offres du jour classées par secteur :
{json.dumps(sector_summary, ensure_ascii=False, indent=2)}

Rédige un résumé éditorial en 3-5 phrases en français :
- Cite les secteurs les plus actifs aujourd'hui
- Mentionne les organisations qui recrutent
- Donne une tendance générale
- Ton : professionnel, informatif, direct

Réponds uniquement avec le texte du résumé, sans titre ni balises.
"""
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{"role":"user","content":prompt}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"Erreur génération résumé: {e}")
        return f"Bulletin du {date.today().strftime('%d/%m/%Y')} — {len(offers)} offres collectées."


# ──────────────────────────────────────────────
# GÉNÉRATION DU SITE HTML
# ──────────────────────────────────────────────

def generate_html_site(offers: list[dict], new_ids: set, summary: str) -> str:
    """Génère le fichier index.html complet."""

    by_sector = {}
    for o in offers:
        s = o.get("secteur","Autre")
        by_sector.setdefault(s, []).append(o)

    today_str = datetime.now().strftime("%d %B %Y à %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VeilleEmploi Afrique — {date.today().strftime('%d/%m/%Y')}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #F8F7F2; color: #2C2C2A; line-height: 1.6; }}
  a {{ color: inherit; text-decoration: none; }}
  .site-header {{ background: #fff; border-bottom: 1px solid #E0DED8; padding: 1rem 2rem; position: sticky; top:0; z-index:100; display:flex; align-items:center; justify-content:space-between; gap:1rem; flex-wrap:wrap; }}
  .logo {{ font-size: 1.4rem; font-weight: 600; color: #0F6E56; }}
  .logo span {{ color: #2C2C2A; }}
  .update-badge {{ font-size: 0.78rem; background: #EAF3DE; color: #3B6D11; padding: 4px 12px; border-radius: 20px; }}
  .hero {{ background: linear-gradient(135deg, #0F6E56 0%, #1D9E75 100%); color: white; padding: 2.5rem 2rem 2rem; }}
  .hero h1 {{ font-size: 1.8rem; font-weight: 700; margin-bottom: 0.5rem; }}
  .hero p {{ opacity: 0.9; font-size: 1rem; max-width: 600px; }}
  .stats {{ display: flex; gap: 1rem; margin-top: 1.5rem; flex-wrap: wrap; }}
  .stat {{ background: rgba(255,255,255,0.15); border-radius: 10px; padding: 0.75rem 1.25rem; min-width: 120px; }}
  .stat-n {{ font-size: 1.8rem; font-weight: 700; }}
  .stat-l {{ font-size: 0.8rem; opacity: 0.85; }}
  .summary-box {{ background: #fff; margin: 1.5rem 2rem; padding: 1.25rem 1.5rem; border-radius: 12px; border-left: 4px solid #1D9E75; font-size: 0.95rem; color: #3B3A35; line-height: 1.7; }}
  .summary-box strong {{ color: #0F6E56; }}
  .main {{ max-width: 1100px; margin: 0 auto; padding: 0 1.5rem 3rem; }}
  .sector-section {{ margin: 2rem 0; }}
  .sector-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 1rem; }}
  .sector-dot {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}
  .sector-name {{ font-size: 1rem; font-weight: 600; color: #2C2C2A; }}
  .sector-count {{ font-size: 0.8rem; background: #F1EFE8; color: #5F5E5A; padding: 2px 8px; border-radius: 10px; }}
  .offers-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }}
  .offer-card {{ background: #fff; border: 1px solid #E0DED8; border-radius: 12px; padding: 1rem 1.25rem; transition: border-color 0.2s, box-shadow 0.2s; display: flex; flex-direction: column; justify-content: space-between; }}
  .offer-card:hover {{ border-color: #1D9E75; box-shadow: 0 2px 12px rgba(29,158,117,0.1); }}
  .offer-card.is-new {{ border-left: 3px solid #1D9E75; }}
  .offer-title {{ font-size: 0.95rem; font-weight: 600; color: #1a1a18; margin-bottom: 4px; }}
  .offer-org {{ font-size: 0.85rem; color: #5F5E5A; margin-bottom: 6px; }}
  .offer-resume {{ font-size: 0.82rem; color: #555; margin-bottom: 8px; font-style: italic; }}
  .tags {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }}
  .tag {{ font-size: 0.72rem; padding: 2px 8px; border-radius: 4px; }}
  .tag-pays {{ background: #E6F1FB; color: #185FA5; }}
  .tag-type {{ background: #FAEEDA; color: #854F0B; }}
  .tag-new {{ background: #EAF3DE; color: #3B6D11; font-weight: 600; }}
  .offer-footer {{ display: flex; align-items: center; justify-content: space-between; margin-top: auto; padding-top: 10px; border-top: 1px solid #F1EFE8; flex-wrap: wrap; gap: 6px; }}
  .offer-source {{ font-size: 0.72rem; color: #aaa; }}
  .btn-voir {{ display: inline-block; font-size: 0.82rem; font-weight: 600; color: #0F6E56; background: #E1F5EE; padding: 5px 14px; border-radius: 6px; border: 1px solid #1D9E75; transition: background 0.2s; }}
  .btn-voir:hover {{ background: #1D9E75; color: white; }}
  .btn-voir-off {{ font-size: 0.78rem; color: #ccc; font-style: italic; }}
  .footer {{ text-align: center; padding: 2rem; font-size: 0.8rem; color: #888; border-top: 1px solid #E0DED8; background: #fff; margin-top: 2rem; }}
  @media (max-width: 600px) {{
    .hero h1 {{ font-size: 1.3rem; }}
    .summary-box {{ margin: 1rem; }}
    .offers-grid {{ grid-template-columns: 1fr; }}
    .site-header {{ padding: 0.75rem 1rem; }}
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
  <p>Bénin, Togo, Sénégal, Côte d'Ivoire et plus — offres classées par secteur d'activité</p>
  <div class="stats">
    <div class="stat"><div class="stat-n">{len(offers)}</div><div class="stat-l">Offres du jour</div></div>
    <div class="stat"><div class="stat-n">{len(new_ids)}</div><div class="stat-l">Nouvelles offres</div></div>
    <div class="stat"><div class="stat-n">{len(by_sector)}</div><div class="stat-l">Secteurs actifs</div></div>
    <div class="stat"><div class="stat-n">{len(set(o.get('pays','') for o in offers))}</div><div class="stat-l">Pays couverts</div></div>
  </div>
</div>

<div class="summary-box">
  <strong>Résumé du {date.today().strftime('%d/%m/%Y')} :</strong> {summary}
</div>

<main class="main">
"""

    for sector, sector_offers in sorted(by_sector.items(), key=lambda x: -len(x[1])):
        color = SECTEUR_COLORS.get(sector, "#888")
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
            if url and url != "#":
                btn = f'<a href="{url}" target="_blank" rel="noopener" class="btn-voir">Voir l\'offre complète →</a>'
            else:
                btn = '<span class="btn-voir-off">Lien non disponible</span>'

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

    html += f"""
</main>

<footer class="footer">
  VeilleEmploi Afrique — Mise à jour automatique quotidienne — {today_str}<br>
  Sources : cDiscussion.com · ReliefWeb · UNDP Jobs | Propulsé par Claude (Anthropic)
</footer>

</body>
</html>
"""
    return html


# ──────────────────────────────────────────────
# DONNÉES MOCK (mode --test)
# ──────────────────────────────────────────────

MOCK_OFFERS = [
    {"titre": "Coordinateur de projets agricoles", "org": "FAO Bénin", "pays": "Bénin",
     "url": "https://www.cdiscussion.com/offre-d-emploi/?details-job=1137292", "source": "cDiscussion.com", "raw_text": "Coordinateur projets agricoles FAO"},
    {"titre": "Consultant en chaîne de valeur riz", "org": "GESCOD Togo", "pays": "Togo",
     "url": "https://reliefweb.int/job/1234567", "source": "ReliefWeb", "raw_text": "Consultant chaîne valeur riz"},
    {"titre": "Responsable suivi-évaluation", "org": "Save the Children", "pays": "Bénin",
     "url": "https://reliefweb.int/job/1234568", "source": "ReliefWeb", "raw_text": "Responsable suivi évaluation ONG"},
    {"titre": "Chargé de programme microfinance", "org": "UNCDF", "pays": "Sénégal",
     "url": "https://jobs.undp.org/cj_view_job.cfm?cur_job_id=123", "source": "UNDP Jobs", "raw_text": "Chargé programme microfinance"},
    {"titre": "Avis de recrutement d'un expert en audit interne", "org": "AGENCE BENINOISE DE PROTECTION CIVILE", "pays": "Bénin",
     "url": "https://www.cdiscussion.com/offre-d-emploi/?details-job=1137294", "source": "cDiscussion.com", "raw_text": "Expert audit interne ABPC"},
    {"titre": "Renforcement des équipes ADER", "org": "Agence de Développement de l'Élevage des Ruminants", "pays": "Bénin",
     "url": "https://www.cdiscussion.com/offre-d-emploi/?details-job=1137292", "source": "cDiscussion.com", "raw_text": "Recrutement ADER élevage"},
]


# ──────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

def run_pipeline(test_mode: bool = False):
    """Pipeline complet : collecte → classifie → génère HTML."""
    log.info(f"╔══ DÉBUT PIPELINE — {datetime.now().strftime('%Y-%m-%d %H:%M')} ══╗")

    # 1. Collecte
    if test_mode:
        log.info("Mode TEST : utilisation de données mock")
        raw_offers = MOCK_OFFERS
    else:
        raw_offers = scrape_all()

    if not raw_offers:
        log.warning("Aucune offre collectée. Arrêt du pipeline.")
        return

    # 2. Classification IA
    if test_mode:
        offers = []
        for i, o in enumerate(raw_offers):
            offers.append({**o,
                "secteur":          SECTEURS[i % len(SECTEURS)],
                "type_contrat":     ["CDI","CDD","Consultance"][i%3],
                "resume":           o["titre"][:60],
                "pertinence_score": 4,
                "id":               hashlib.md5(o["titre"].encode()).hexdigest()[:8],
                "date_collecte":    date.today().isoformat(),
            })
    else:
        offers = classify_and_enrich_with_claude(raw_offers)

    # 3. Déduplication
    history_file = DATA_DIR / "history.json"
    offers, new_offers = deduplicate(offers, history_file)
    new_ids = {o["id"] for o in new_offers}

    # 4. Sauvegarde JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today_file = DATA_DIR / f"offers_{date.today().isoformat()}.json"
    today_file.write_text(json.dumps(offers, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Données sauvegardées : {today_file}")

    # 5. Résumé éditorial
    if test_mode:
        summary = f"Bulletin test du {date.today().strftime('%d/%m/%Y')} — {len(offers)} offres simulées dans {len(set(o['secteur'] for o in offers))} secteurs. Les offres proviennent de cDiscussion.com, ReliefWeb et UNDP Jobs."
    else:
        summary = generate_daily_summary(offers)

    # 6. Génération HTML
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html = generate_html_site(offers, new_ids, summary)
    index_file = OUTPUT_DIR / "index.html"
    index_file.write_text(html, encoding="utf-8")
    log.info(f"Site généré : {index_file}")

    log.info(f"╚══ FIN PIPELINE — {len(offers)} offres | {len(new_offers)} nouvelles ══╝")
    return offers


# ──────────────────────────────────────────────
# ENTRÉE PRINCIPALE
# ──────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent veille recrutement Afrique francophone")
    parser.add_argument("--once", action="store_true", help="Exécuter une seule fois")
    parser.add_argument("--test", action="store_true", help="Mode test (données mock)")
    args = parser.parse_args()

    if args.once or args.test:
        run_pipeline(test_mode=args.test)
    else:
        log.info("Agent planifié — exécution chaque jour à 06:00")
        run_pipeline()
        schedule.every().day.at("06:00").do(run_pipeline)
        while True:
            schedule.run_pending()
            time.sleep(60)
