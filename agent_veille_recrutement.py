"""
AGENT DE VEILLE RECRUTEMENT - BÉNIN & AFRIQUE FRANCOPHONE
==========================================================
Scrape quotidiennement les appels à recrutement et consultances,
les classe par secteur, génère un résumé HTML et met à jour le site.

INSTALLATION:
    pip install requests beautifulsoup4 anthropic schedule python-dotenv

CONFIGURATION:
    Créer un fichier .env avec:
    ANTHROPIC_API_KEY=sk-ant-...

LANCEMENT:
    python agent_veille_recrutement.py            # tourne en boucle (cron interne)
    python agent_veille_recrutement.py --once     # une seule exécution
    python agent_veille_recrutement.py --test     # mode test (pas de vraie requête HTTP)
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
# CONFIGURATION GÉNÉRALE
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
# SOURCES DE SCRAPING
# ──────────────────────────────────────────────
# Chaque source est un dict avec:
#   url      : URL à scraper (liste ou chaîne)
#   parser   : nom de la fonction de parsing (voir PARSERS ci-dessous)
#   pays     : pays principal couvert
#   label    : nom affiché de la source

SOURCES = [
    # ── BÉNIN ──
    {
        "label": "Emploi Bénin",
        "url":   "https://www.emploi.bj/offres-emploi",
        "parser": "generic_job_list",
        "pays":  "Bénin",
    },
    {
        "label": "ONG Emploi BJ",
        "url":   "https://www.ong-emploi.bj/offres",
        "parser": "generic_job_list",
        "pays":  "Bénin",
    },
    # ── AFRIQUE FRANCOPHONE ──
    {
        "label": "ReliefWeb (Bénin + Togo)",
        "url": [
            "https://reliefweb.int/jobs?country=20&type=job",
            "https://reliefweb.int/jobs?country=226&type=job",
        ],
        "parser": "reliefweb",
        "pays":  "Multi-pays",
    },
    {
        "label": "DevEx Afrique",
        "url":   "https://www.devex.com/jobs/search?location=West+Africa&language=French",
        "parser": "devex",
        "pays":  "Afrique francophone",
    },
    {
        "label": "Expertise France",
        "url":   "https://www.expertisefrance.fr/offres-d-emploi",
        "parser": "generic_job_list",
        "pays":  "Multi-pays",
    },
    {
        "label": "UNDP Jobs (Afrique de l'Ouest)",
        "url":   "https://jobs.undp.org/cj_view_jobs.cfm?cur_job_type=&cur_job_level=&cur_job_category=&cur_job_location=West+Africa",
        "parser": "generic_job_list",
        "pays":  "Afrique francophone",
    },
    {
        "label": "IRC Relief",
        "url":   "https://rescue.org/careers",
        "parser": "generic_job_list",
        "pays":  "Multi-pays",
    },
]


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
# PARSERS
# ──────────────────────────────────────────────

def parse_generic_job_list(html: str, source: dict) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    base_url = source["url"] if isinstance(source["url"], str) else source["url"][0]
    domain = "/".join(base_url.split("/")[:3])  # ex: https://reliefweb.int

    candidates = soup.select(
        "article.job, li.job, div.job-item, div.offer, "
        ".job-listing, .vacancies-item, .post-item"
    )
    if not candidates:
        candidates = soup.select("h2 a, h3 a")

    for el in candidates[:30]:
        title = el.get_text(" ", strip=True)[:200]
        # Cherche le lien dans l'élément ou ses enfants
        link_el = el if el.name == "a" else el.find("a")
        link = ""
        if link_el:
            href = link_el.get("href", "")
            if href.startswith("http"):
                link = href
            elif href.startswith("/"):
                link = domain + href
        if not title or len(title) < 8:
            continue
        offers.append({
            "titre":    title,
            "org":      source["label"],
            "pays":     source["pays"],
            "url":      link,
            "source":   source["label"],
            "raw_text": title,
        })
    return offers


def parse_reliefweb(html: str, source: dict) -> list[dict]:
    """Parser pour ReliefWeb."""
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    for item in soup.select(".job-list-item, article.job"):
        titre = item.select_one("h3, h2, .title")
        org   = item.select_one(".organization, .source")
        link  = item.select_one("a")
        if not titre:
            continue
        offers.append({
            "titre":    titre.get_text(strip=True),
            "org":      org.get_text(strip=True) if org else "Non précisé",
            "pays":     source["pays"],
            "url":      "https://reliefweb.int" + link.get("href","") if link else "",
            "source":   "ReliefWeb",
            "raw_text": titre.get_text(strip=True),
        })
    return offers


def parse_devex(html: str, source: dict) -> list[dict]:
    """Parser pour DevEx."""
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    for item in soup.select(".job-listing, .job-result"):
        titre = item.select_one(".job-title, h2")
        org   = item.select_one(".company-name, .org")
        if not titre:
            continue
        offers.append({
            "titre":    titre.get_text(strip=True),
            "org":      org.get_text(strip=True) if org else "Non précisé",
            "pays":     source["pays"],
            "url":      "",
            "source":   "DevEx",
            "raw_text": titre.get_text(strip=True),
        })
    return offers


PARSERS = {
    "generic_job_list": parse_generic_job_list,
    "reliefweb":        parse_reliefweb,
    "devex":            parse_devex,
}


# ──────────────────────────────────────────────
# SCRAPING
# ──────────────────────────────────────────────

def scrape_source(source: dict) -> list[dict]:
    """Télécharge et parse une source."""
    urls = source["url"] if isinstance(source["url"], list) else [source["url"]]
    parser_fn = PARSERS.get(source["parser"], parse_generic_job_list)
    all_offers = []
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            offers = parser_fn(resp.text, source)
            all_offers.extend(offers)
            log.info(f"  {source['label']}: {len(offers)} offre(s) trouvée(s)")
            time.sleep(1.5)  # délai poli
        except Exception as e:
            log.warning(f"  Erreur scraping {url}: {e}")
    return all_offers


def scrape_all() -> list[dict]:
    """Lance le scraping de toutes les sources."""
    log.info("=== Début du scraping ===")
    all_offers = []
    for source in SOURCES:
        log.info(f"Scraping : {source['label']}")
        offers = scrape_source(source)
        all_offers.extend(offers)
    log.info(f"Total brut : {len(all_offers)} offres collectées")
    return all_offers


# ──────────────────────────────────────────────
# CLASSIFICATION IA (CLAUDE)
# ──────────────────────────────────────────────

def classify_and_enrich_with_claude(offers: list[dict]) -> list[dict]:
    """
    Envoie les titres à Claude pour :
    - Classifier par secteur
    - Extraire type de contrat (CDI, CDD, Consultance, Stage)
    - Détecter le pays si absent ou "Multi-pays"
    - Générer un résumé court
    """
    if not offers:
        return []

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    # Traitement par batch de 20 pour ne pas dépasser le context
    enriched = []
    batch_size = 20

    for i in range(0, len(offers), batch_size):
        batch = offers[i:i+batch_size]
        items_json = json.dumps([
            {"id": idx, "titre": o["titre"], "org": o.get("org",""), "pays": o.get("pays","")}
            for idx, o in enumerate(batch)
        ], ensure_ascii=False)

        prompt = f"""Tu es un expert en recrutement en Afrique francophone (Bénin, Togo, Sénégal, etc.).

Voici une liste d'offres d'emploi/consultance extraites de sites web :
{items_json}

Pour CHAQUE offre, retourne un JSON avec :
- id (même que l'entrée)
- secteur : UN parmi {json.dumps(SECTEURS, ensure_ascii=False)}
- type_contrat : "CDI", "CDD", "Consultance", "Stage", ou "Inconnu"
- pays_detecte : pays principal (si déjà précis, garde-le; sinon déduis du contexte)
- resume : 1 phrase courte (max 15 mots) décrivant le poste en français
- pertinence_score : entier 1-5 (5=très pertinent pour Afrique franco.)

Réponds UNIQUEMENT avec un tableau JSON valide, sans commentaire ni markdown.
"""

        try:
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role":"user","content":prompt}]
            )
            raw = resp.content[0].text.strip()
            # Nettoyage au cas où
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
                offer["secteur"]      = "Autre"
                offer["type_contrat"] = "Inconnu"
                offer["resume"]       = offer["titre"][:80]
                offer["id"]           = hashlib.md5(offer["titre"].encode()).hexdigest()[:8]
                offer["date_collecte"]= date.today().isoformat()
                enriched.append(offer)

    # Filtre qualité : score >= 2 seulement
    enriched = [o for o in enriched if o.get("pertinence_score",0) >= 2]
    log.info(f"Après filtre qualité: {len(enriched)} offres retenues")
    return enriched


# ──────────────────────────────────────────────
# DÉDUPLICATION
# ──────────────────────────────────────────────

def deduplicate(offers: list[dict], history_file: Path) -> tuple[list[dict], list[dict]]:
    """
    Compare avec l'historique pour détecter les nouvelles offres.
    Retourne (toutes_les_offres_d_aujourd_hui, nouvelles_offres_seulement)
    """
    history = {}
    if history_file.exists():
        history = json.loads(history_file.read_text(encoding="utf-8"))

    today_ids = {o["id"] for o in offers}
    new_offers = [o for o in offers if o["id"] not in history]

    # Mise à jour de l'historique (garde 60 jours)
    cutoff = date.today().isoformat()[:7]  # YYYY-MM
    new_history = {oid: info for oid, info in history.items()
                   if info.get("date","")[:7] >= cutoff}
    for o in offers:
        new_history[o["id"]] = {"titre": o["titre"][:60], "date": o["date_collecte"]}

    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(json.dumps(new_history, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info(f"{len(new_offers)} nouvelles offres (sur {len(offers)} collectées)")
    return offers, new_offers


# ──────────────────────────────────────────────
# GÉNÉRATION DU RÉSUMÉ QUOTIDIEN (Claude)
# ──────────────────────────────────────────────

def generate_daily_summary(offers: list[dict]) -> str:
    """Génère un paragraphe de résumé éditorial par secteur."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY",""))

    by_sector = {}
    for o in offers:
        s = o.get("secteur","Autre")
        by_sector.setdefault(s, []).append(o)

    sector_summary = {s: [o["titre"] for o in lst[:5]] for s, lst in by_sector.items()}

    prompt = f"""Tu es éditeur d'un bulletin de veille emploi pour l'Afrique francophone (Bénin, Togo, Sénégal...).

Voici les offres du jour classées par secteur :
{json.dumps(sector_summary, ensure_ascii=False, indent=2)}

Rédige un résumé éditorial en 4-6 phrases en français, comme pour une newsletter professionnelle :
- Cite les secteurs les plus actifs
- Mentionne les types d'organisations qui recrutent
- Donne une tendance générale du marché
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
    """Génère le fichier index.html complet du site."""

    by_sector = {}
    for o in offers:
        s = o.get("secteur","Autre")
        by_sector.setdefault(s, []).append(o)

    today_str = datetime.now().strftime("%d %B %Y à %H:%M")

    # ─── CSS et HEAD ───
    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VeilleEmploi Afrique — {date.today().strftime('%d/%m/%Y')}</title>
  .offer-footer {{ display: flex; align-items: center; justify-content: space-between; margin-top: 10px; flex-wrap: wrap; gap: 6px; }}
  .btn-voir {{ display: inline-block; font-size: 0.82rem; font-weight: 600; color: #0F6E56; background: #E1F5EE; padding: 5px 12px; border-radius: 6px; border: 1px solid #1D9E75; transition: background 0.2s; }}
  .btn-voir:hover {{ background: #1D9E75; color: white; }}
  .btn-voir-off {{ font-size: 0.78rem; color: #aaa; font-style: italic; }}
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
  .offer-card {{ background: #fff; border: 1px solid #E0DED8; border-radius: 12px; padding: 1rem 1.25rem; transition: border-color 0.2s, box-shadow 0.2s; }}
  .offer-card:hover {{ border-color: #1D9E75; box-shadow: 0 2px 12px rgba(29,158,117,0.1); }}
  .offer-card.is-new {{ border-left: 3px solid #1D9E75; }}
  .offer-title {{ font-size: 0.95rem; font-weight: 600; color: #1a1a18; margin-bottom: 4px; }}
  .offer-org {{ font-size: 0.85rem; color: #5F5E5A; margin-bottom: 8px; }}
  .offer-resume {{ font-size: 0.82rem; color: #444; margin-bottom: 8px; font-style: italic; }}
  .tags {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .tag {{ font-size: 0.72rem; padding: 2px 8px; border-radius: 4px; }}
  .tag-pays {{ background: #E6F1FB; color: #185FA5; }}
  .tag-type {{ background: #FAEEDA; color: #854F0B; }}
  .tag-new {{ background: #EAF3DE; color: #3B6D11; font-weight: 600; }}
  .offer-source {{ font-size: 0.72rem; color: #888; margin-top: 8px; }}
  .footer {{ text-align: center; padding: 2rem; font-size: 0.8rem; color: #888; border-top: 1px solid #E0DED8; background: #fff; }}
  @media (max-width: 600px) {{
    .hero h1 {{ font-size: 1.3rem; }}
    .summary-box {{ margin: 1rem; }}
    .offers-grid {{ grid-template-columns: 1fr; }}
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
  <p>Bénin, Togo, Sénégal, Côte d'Ivoire, Burkina Faso, Niger et plus — classés par secteur d'activité</p>
  <div class="stats">
    <div class="stat"><div class="stat-n">{len(offers)}</div><div class="stat-l">Offres du jour</div></div>
    <div class="stat"><div class="stat-n">{len(new_ids)}</div><div class="stat-l">Nouvelles offres</div></div>
    <div class="stat"><div class="stat-n">{len(by_sector)}</div><div class="stat-l">Secteurs actifs</div></div>
    <div class="stat"><div class="stat-n">{len(set(o.get('pays','') for o in offers))}</div><div class="stat-l">Pays couverts</div></div>
  </div>
</div>

<div class="summary-box">
  <strong>Résumé éditorial du {date.today().strftime('%d/%m/%Y')} :</strong> {summary}
</div>

<main class="main">
"""

    # ─── Sections par secteur ───
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
            is_new = "is-new" if o["id"] in new_ids else ""
            new_tag = '<span class="tag tag-new">Nouveau</span>' if o["id"] in new_ids else ""
            url = o.get("url","") or ""
            btn_source = f'<a href="{url}" target="_blank" rel="noopener" class="btn-voir">Voir l\'offre complète →</a>' if url and url != "#" else '<span class="btn-voir-off">Lien non disponible</span>'
            html += f"""      <div class="offer-card {is_new}">
        <div class="offer-title">{o['titre'][:100]}</div>
        <div class="offer-org">{o.get('org','')}</div>
        <div class="offer-resume">{o.get('resume','')}</div>
        <div class="tags">
          <span class="tag tag-pays">{o.get('pays','')}</span>
          <span class="tag tag-type">{o.get('type_contrat','')}</span>
          {new_tag}
        </div>
        <div class="offer-footer">
          <div class="offer-source">Source : {o.get('source','')}</div>
          {btn_source}
        </div>
      </div>
"""
        html += "    </div>\n  </section>\n"

    # ─── Footer ───
    html += f"""
</main>

<footer class="footer">
  VeilleEmploi Afrique — Mise à jour automatique quotidienne — Données collectées le {today_str}<br>
  Agent IA propulsé par Claude (Anthropic) | Développé avec ♡ pour le Bénin et l'Afrique francophone
</footer>

</body>
</html>
"""
    return html


# ──────────────────────────────────────────────
# DONNÉES DE TEST (mode --test)
# ──────────────────────────────────────────────

MOCK_OFFERS = [
    {"titre": "Coordinateur de projets agricoles", "org": "FAO Bénin", "pays": "Bénin", "url": "", "source": "FAO", "raw_text": "Coordinateur de projets agricoles FAO"},
    {"titre": "Consultant en chaîne de valeur riz", "org": "GESCOD/Togo", "pays": "Togo", "url": "", "source": "GESCOD", "raw_text": "Consultant chaîne de valeur riz"},
    {"titre": "Responsable suivi-évaluation", "org": "Save the Children Bénin", "pays": "Bénin", "url": "", "source": "STC", "raw_text": "Responsable suivi évaluation ONG"},
    {"titre": "Chargé de programme microfinance", "org": "UNCDF", "pays": "Sénégal", "url": "", "source": "UNCDF", "raw_text": "Chargé programme microfinance"},
    {"titre": "Médecin superviseur", "org": "MSF Niger", "pays": "Niger", "url": "", "source": "MSF", "raw_text": "Médecin superviseur terrain"},
]


# ──────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

def run_pipeline(test_mode: bool = False):
    """Pipeline complet : scrape → classifie → génère HTML."""
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
        # Enrichissement simulé en mode test
        offers = []
        for i, o in enumerate(raw_offers):
            offers.append({**o,
                "secteur": SECTEURS[i % len(SECTEURS)],
                "type_contrat": ["CDI","CDD","Consultance"][i%3],
                "resume": o["titre"][:60],
                "pertinence_score": 4,
                "id": hashlib.md5(o["titre"].encode()).hexdigest()[:8],
                "date_collecte": date.today().isoformat(),
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
        summary = f"Bulletin test du {date.today().strftime('%d/%m/%Y')} — {len(offers)} offres simulées dans {len(set(o['secteur'] for o in offers))} secteurs."
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
    parser.add_argument("--once",  action="store_true", help="Exécuter une seule fois puis quitter")
    parser.add_argument("--test",  action="store_true", help="Mode test (données mock, pas de vraies requêtes)")
    args = parser.parse_args()

    if args.once or args.test:
        run_pipeline(test_mode=args.test)
    else:
        # Lancement immédiat + planification quotidienne à 6h00
        log.info("Agent planifié — exécution chaque jour à 06:00")
        run_pipeline()
        schedule.every().day.at("06:00").do(run_pipeline)
        while True:
            schedule.run_pending()
            time.sleep(60)
