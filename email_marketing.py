"""
MODULE EMAIL MARKETING ABED ACADEMY
====================================
Génère chaque jour :
  1. Un fichier Excel — Top 10 offres liées aux formations ABED Academy
  2. Un fichier Word  — 5 posts LinkedIn + 2 scripts TikTok pour ABED Academy
  3. Envoie tout par email à l'équipe à 7h00

Dépendances supplémentaires :
    pip install openpyxl python-docx
"""

import os
import json
import smtplib
import logging
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

import anthropic
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH

log = logging.getLogger("veille")

# ──────────────────────────────────────────────
# FORMATIONS ABED ACADEMY
# ──────────────────────────────────────────────

FORMATIONS_ABED = [
    {
        "nom": "Accompagnement à l'Entrepreneuriat Agricole Innovant et Inclusif",
        "url": "https://academy.abedong.org/course/index.php?categoryid=5",
        "competences": ["entrepreneuriat agricole", "agribusiness", "coaching agricole",
                        "agriculture", "développement rural", "chaîne de valeur",
                        "projet agricole", "innovation agricole", "élevage", "maraîchage"],
        "metiers_cibles": ["conseiller agricole", "chef de projet agricole",
                           "coordinateur agri", "responsable filière", "technicien agricole"],
    },
    {
        "nom": "Suivi-Évaluation (MEAL)",
        "url": "https://academy.abedong.org/course/index.php?categoryid=4",
        "competences": ["suivi-évaluation", "MEAL", "monitoring", "évaluation", "cadre logique",
                        "indicateurs", "collecte de données", "rapport", "gestion de projet",
                        "redevabilité", "base de données", "kobo", "ODK"],
        "metiers_cibles": ["chargé de suivi-évaluation", "responsable MEAL",
                           "assistant suivi", "coordinateur M&E", "chargé de programme"],
    },
    {
        "nom": "Nutrition Communautaire & Transformation Agroalimentaire",
        "url": "https://academy.abedong.org/course/index.php?categoryid=7",
        "competences": ["nutrition", "santé communautaire", "transformation alimentaire",
                        "sécurité alimentaire", "WASH", "malnutrition", "agroalimentaire",
                        "ANJE", "programme nutritionnel", "qualité alimentaire"],
        "metiers_cibles": ["nutritionniste", "agent de santé communautaire",
                           "chargé nutrition", "responsable WASH", "technicien agroalimentaire"],
    },
    {
        "nom": "Finances & Gestion",
        "url": "https://academy.abedong.org/course/index.php?categoryid=8",
        "competences": ["finance", "comptabilité", "gestion budgétaire", "microfinance",
                        "analyse financière", "trésorerie", "audit", "crédit",
                        "gestion de caisse", "reporting financier", "planification financière"],
        "metiers_cibles": ["comptable", "gestionnaire financier", "analyste financier",
                           "chargé de crédit", "contrôleur de gestion", "aide-comptable"],
    },
    {
        "nom": "Insertion Professionnelle & Employabilité",
        "url": "https://academy.abedong.org/course/index.php?categoryid=6",
        "competences": ["employabilité", "recherche d'emploi", "CV", "entretien",
                        "soft skills", "communication professionnelle", "leadership",
                        "gestion du temps", "travail en équipe", "développement personnel"],
        "metiers_cibles": ["tous secteurs", "jeunes diplômés", "reconversion professionnelle"],
    },
    {
        "nom": "Conseils Agricoles",
        "url": "https://academy.abedong.org/course/index.php?categoryid=11",
        "competences": ["conseil agricole", "vulgarisation", "appui technique",
                        "diagnostic agronomique", "agroécologie", "systèmes de production",
                        "producteurs", "coopératives", "organisations paysannes"],
        "metiers_cibles": ["conseiller agricole", "agent de vulgarisation",
                           "responsable formation agricole", "technicien terrain"],
    },
]


# ──────────────────────────────────────────────
# MATCHING OFFRES ↔ FORMATIONS
# ──────────────────────────────────────────────

def match_formation(offre: dict) -> dict:
    """Trouve la formation ABED la plus pertinente pour une offre."""
    texte = (offre.get("titre","") + " " +
             offre.get("resume","") + " " +
             offre.get("secteur","") + " " +
             offre.get("raw_text","")).lower()

    best_formation = FORMATIONS_ABED[-1]  # fallback: Insertion Pro
    best_score = 0

    for formation in FORMATIONS_ABED:
        score = 0
        for mot in formation["competences"]:
            if mot.lower() in texte:
                score += 2
        for metier in formation["metiers_cibles"]:
            if metier.lower() in texte:
                score += 3
        if score > best_score:
            best_score = score
            best_formation = formation

    return best_formation


def select_top10_for_abed(offers: list[dict]) -> list[dict]:
    """
    Sélectionne les 10 offres les plus pertinentes pour ABED Academy
    en maximisant la diversité des formations couvertes.
    """
    # Score chaque offre
    scored = []
    for o in offers:
        formation = match_formation(o)
        texte = (o.get("titre","") + " " + o.get("raw_text","")).lower()
        score = sum(2 for m in formation["competences"] if m.lower() in texte)
        score += sum(3 for m in formation["metiers_cibles"] if m.lower() in texte)
        score += o.get("pertinence_score", 3)
        scored.append((score, o, formation))

    scored.sort(key=lambda x: -x[0])

    # Sélection top 10 avec diversité des formations
    top10 = []
    formations_utilisees = {}
    for score, offre, formation in scored:
        nom_f = formation["nom"]
        if formations_utilisees.get(nom_f, 0) < 3:  # max 3 par formation
            offre["_formation_matchee"] = formation
            top10.append(offre)
            formations_utilisees[nom_f] = formations_utilisees.get(nom_f, 0) + 1
        if len(top10) >= 10:
            break

    # Compléter si moins de 10
    if len(top10) < 10:
        for score, offre, formation in scored:
            if offre not in top10:
                offre["_formation_matchee"] = formation
                top10.append(offre)
            if len(top10) >= 10:
                break

    return top10


# ──────────────────────────────────────────────
# ENRICHISSEMENT IA DES OFFRES
# ──────────────────────────────────────────────

def enrich_offers_for_marketing(offers: list[dict]) -> list[dict]:
    """
    Utilise Claude pour enrichir chaque offre avec :
    - Compétences requises détaillées
    - Argumentaire ABED Academy spécifique
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY",""))

    formations_context = json.dumps([
        {"nom": f["nom"], "url": f["url"], "competences": f["competences"][:5]}
        for f in FORMATIONS_ABED
    ], ensure_ascii=False)

    items = json.dumps([
        {
            "id": i,
            "titre": o.get("titre",""),
            "org": o.get("org",""),
            "pays": o.get("pays",""),
            "secteur": o.get("secteur",""),
            "resume": o.get("resume",""),
            "formation_abed": o.get("_formation_matchee",{}).get("nom",""),
        }
        for i, o in enumerate(offers)
    ], ensure_ascii=False)

    prompt = f"""Tu es le directeur marketing de ABED Academy, une plateforme de formation professionnelle au Bénin.

Formations disponibles sur ABED Academy :
{formations_context}

Voici les 10 meilleures offres d'emploi du jour :
{items}

Pour CHAQUE offre, génère un JSON avec :
- id (même que l'entrée)
- competences_requises : liste de 3-5 compétences clés pour ce poste
- argumentaire_abed : 2 phrases montrant comment la formation ABED préparée aide à décrocher ce poste (ton convaincant, personnel, orienté résultat)
- niveau_requis : "Débutant", "Intermédiaire", ou "Expérimenté"

Réponds UNIQUEMENT avec un tableau JSON valide.
"""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            messages=[{"role":"user","content":prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        enrichissements = json.loads(raw)

        for item in enrichissements:
            idx = item.get("id", 0)
            if idx < len(offers):
                offers[idx]["competences_requises"] = item.get("competences_requises", [])
                offers[idx]["argumentaire_abed"]    = item.get("argumentaire_abed", "")
                offers[idx]["niveau_requis"]         = item.get("niveau_requis", "")

        log.info("Enrichissement marketing des offres terminé")
    except Exception as e:
        log.error(f"Erreur enrichissement marketing: {e}")
        for o in offers:
            if "competences_requises" not in o:
                o["competences_requises"] = []
                o["argumentaire_abed"]    = f"La formation {o.get('_formation_matchee',{}).get('nom','')} vous prépare idéalement à ce poste."
                o["niveau_requis"]         = "Intermédiaire"

    return offers


# ──────────────────────────────────────────────
# GÉNÉRATION DU FICHIER EXCEL
# ──────────────────────────────────────────────

def generate_excel(offers: list[dict], output_path: Path) -> Path:
    """Génère le fichier Excel Top 10 offres ABED Academy."""

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Top 10 Offres du Jour"

    # Couleurs ABED
    VERT_ABED   = "0F6E56"
    VERT_CLAIR  = "E1F5EE"
    BLANC       = "FFFFFF"
    GRIS_CLAIR  = "F8F7F2"
    ORANGE      = "EF9F27"

    border_thin = Border(
        left=Side(style='thin', color="CCCCCC"),
        right=Side(style='thin', color="CCCCCC"),
        top=Side(style='thin', color="CCCCCC"),
        bottom=Side(style='thin', color="CCCCCC"),
    )

    # ── TITRE ──
    ws.merge_cells("A1:H1")
    ws["A1"] = f"ABED ACADEMY — Top 10 Offres d'Emploi | {date.today().strftime('%d %B %Y')}"
    ws["A1"].font      = Font(name="Arial", size=14, bold=True, color=BLANC)
    ws["A1"].fill      = PatternFill("solid", fgColor=VERT_ABED)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    ws.merge_cells("A2:H2")
    ws["A2"] = "Offres sélectionnées en lien avec les formations ABED Academy | academy.abedong.org"
    ws["A2"].font      = Font(name="Arial", size=10, italic=True, color=VERT_ABED)
    ws["A2"].fill      = PatternFill("solid", fgColor=VERT_CLAIR)
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20

    # ── EN-TÊTES ──
    headers = [
        "N°", "Titre du Poste", "Organisation", "Pays",
        "Formation ABED Recommandée", "Compétences Requises",
        "Argumentaire ABED Academy", "Lien pour Postuler"
    ]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col, value=header)
        cell.font      = Font(name="Arial", size=10, bold=True, color=BLANC)
        cell.fill      = PatternFill("solid", fgColor=VERT_ABED)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = border_thin
    ws.row_dimensions[4].height = 28

    # ── DONNÉES ──
    for i, offre in enumerate(offers):
        row = i + 5
        formation = offre.get("_formation_matchee", {})
        competences = "\n".join(f"• {c}" for c in offre.get("competences_requises", []))
        lien = offre.get("url","") or "Non disponible"

        values = [
            i + 1,
            offre.get("titre",""),
            offre.get("org",""),
            offre.get("pays",""),
            formation.get("nom",""),
            competences,
            offre.get("argumentaire_abed",""),
            lien,
        ]

        bg = BLANC if i % 2 == 0 else GRIS_CLAIR

        for col, val in enumerate(values, 1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.font      = Font(name="Arial", size=9)
            cell.fill      = PatternFill("solid", fgColor=bg)
            cell.border    = border_thin
            cell.alignment = Alignment(vertical="top", wrap_text=True)

            # Numéro en gras centré
            if col == 1:
                cell.font      = Font(name="Arial", size=10, bold=True, color=VERT_ABED)
                cell.alignment = Alignment(horizontal="center", vertical="center")

            # Titre en gras
            if col == 2:
                cell.font = Font(name="Arial", size=9, bold=True)

            # Formation en vert
            if col == 5:
                cell.font = Font(name="Arial", size=9, color=VERT_ABED, bold=True)

            # Lien cliquable en bleu
            if col == 8 and lien != "Non disponible":
                cell.font      = Font(name="Arial", size=9, color="185FA5", underline="single")
                ws.cell(row=row, column=col).hyperlink = lien

        ws.row_dimensions[row].height = 80

    # ── LARGEURS COLONNES ──
    col_widths = [4, 35, 28, 12, 32, 28, 45, 35]
    for col, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width

    # ── FEUILLE 2 : LIENS FORMATIONS ──
    ws2 = wb.create_sheet("Formations ABED Academy")
    ws2.merge_cells("A1:C1")
    ws2["A1"] = "Formations ABED Academy — Liens directs"
    ws2["A1"].font      = Font(name="Arial", size=13, bold=True, color=BLANC)
    ws2["A1"].fill      = PatternFill("solid", fgColor=VERT_ABED)
    ws2["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[1].height = 28

    headers2 = ["Formation", "Compétences clés", "Lien d'inscription"]
    for col, h in enumerate(headers2, 1):
        cell = ws2.cell(row=2, column=col, value=h)
        cell.font   = Font(name="Arial", size=10, bold=True, color=BLANC)
        cell.fill   = PatternFill("solid", fgColor=VERT_ABED)
        cell.border = border_thin
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for i, f in enumerate(FORMATIONS_ABED):
        row = i + 3
        ws2.cell(row=row, column=1, value=f["nom"]).font = Font(name="Arial", size=9, bold=True)
        ws2.cell(row=row, column=2, value=", ".join(f["competences"][:5])).font = Font(name="Arial", size=9)
        link_cell = ws2.cell(row=row, column=3, value=f["url"])
        link_cell.font      = Font(name="Arial", size=9, color="185FA5", underline="single")
        link_cell.hyperlink = f["url"]
        for col in range(1, 4):
            ws2.cell(row=row, column=col).border = border_thin
            ws2.cell(row=row, column=col).fill   = PatternFill("solid", fgColor=BLANC if i%2==0 else GRIS_CLAIR)
        ws2.row_dimensions[row].height = 22

    ws2.column_dimensions["A"].width = 45
    ws2.column_dimensions["B"].width = 50
    ws2.column_dimensions["C"].width = 50

    wb.save(output_path)
    log.info(f"Excel généré : {output_path}")
    return output_path


# ──────────────────────────────────────────────
# GÉNÉRATION DU DOCUMENT WORD
# ──────────────────────────────────────────────

def generate_word(offers: list[dict], output_path: Path) -> Path:
    """Génère le document Word avec posts LinkedIn et scripts TikTok."""

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY",""))

    # Génère les contenus avec Claude
    offres_context = json.dumps([
        {
            "titre": o.get("titre",""),
            "org": o.get("org",""),
            "pays": o.get("pays",""),
            "secteur": o.get("secteur",""),
            "resume": o.get("resume",""),
            "formation_abed": o.get("_formation_matchee",{}).get("nom",""),
            "url_formation": o.get("_formation_matchee",{}).get("url",""),
        }
        for o in offers[:5]
    ], ensure_ascii=False)

    prompt_linkedin = f"""Tu es le community manager de ABED Academy (academy.abedong.org), une plateforme de formation professionnelle au Bénin qui transforme les diplômés en professionnels employables.

Voici les 5 meilleures offres d'emploi du jour :
{offres_context}

Génère 5 posts LinkedIn distincts pour la page ABED Academy.

Pour chaque post :
- Commence par une accroche forte (question ou stat choc)
- Cite l'offre d'emploi réelle comme preuve de marché
- Présente la formation ABED correspondante comme solution
- Termine par un call-to-action vers academy.abedong.org
- Ajoute 5-7 hashtags pertinents
- Longueur : 150-200 mots par post
- Ton : professionnel, inspirant, orienté résultats

Format de réponse JSON :
[
  {{
    "titre": "Titre court du post",
    "contenu": "Texte complet du post LinkedIn",
    "hashtags": "#hashtag1 #hashtag2..."
  }},
  ...
]
Réponds UNIQUEMENT avec le JSON valide.
"""

    prompt_tiktok = f"""Tu es le créateur de contenu TikTok de ABED Academy (academy.abedong.org).

Voici les meilleures offres du jour : {offres_context}

Génère 2 scripts TikTok (30-60 secondes) pour promouvoir ABED Academy.

Chaque script doit avoir :
- Une accroche visuelle forte en 3 secondes (ce qu'on voit à l'écran)
- Un texte parlé dynamique, style voix off ou face caméra
- Des mentions d'offres d'emploi réelles du Bénin/Afrique pour crédibilité
- La formation ABED comme solution
- Un CTA final clair

Format JSON :
[
  {{
    "titre": "Titre du TikTok",
    "accroche_visuelle": "Ce qu'on voit en 3 premières secondes",
    "script_parle": "Texte complet à dire",
    "texte_ecran": "Texte à afficher à l'écran (sous-titres clés)",
    "hashtags": "#hashtag1 #hashtag2..."
  }},
  ...
]
Réponds UNIQUEMENT avec le JSON valide.
"""

    posts_linkedin = []
    scripts_tiktok = []

    try:
        resp1 = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            messages=[{"role":"user","content":prompt_linkedin}]
        )
        raw1 = resp1.content[0].text.strip()
        if raw1.startswith("```"):
            raw1 = raw1.split("```")[1]
            if raw1.startswith("json"):
                raw1 = raw1[4:]
        posts_linkedin = json.loads(raw1)
        log.info(f"  {len(posts_linkedin)} posts LinkedIn générés")
    except Exception as e:
        log.error(f"Erreur génération LinkedIn: {e}")

    try:
        resp2 = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role":"user","content":prompt_tiktok}]
        )
        raw2 = resp2.content[0].text.strip()
        if raw2.startswith("```"):
            raw2 = raw2.split("```")[1]
            if raw2.startswith("json"):
                raw2 = raw2[4:]
        scripts_tiktok = json.loads(raw2)
        log.info(f"  {len(scripts_tiktok)} scripts TikTok générés")
    except Exception as e:
        log.error(f"Erreur génération TikTok: {e}")

    # ── CONSTRUCTION DU DOCUMENT WORD ──
    doc = Document()

    # Styles de base
    style_normal = doc.styles['Normal']
    style_normal.font.name = "Arial"
    style_normal.font.size = Pt(11)

    VERT = RGBColor(0x0F, 0x6E, 0x56)
    VERT_CLAIR = RGBColor(0x1D, 0x9E, 0x75)
    GRIS = RGBColor(0x5F, 0x5E, 0x5A)
    ORANGE = RGBColor(0xEF, 0x9F, 0x27)

    def add_heading(text, level=1, color=None):
        p = doc.add_paragraph()
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Arial"
        if level == 1:
            run.font.size = Pt(18)
            run.font.color.rgb = color or VERT
        elif level == 2:
            run.font.size = Pt(14)
            run.font.color.rgb = color or VERT_CLAIR
        elif level == 3:
            run.font.size = Pt(12)
            run.font.color.rgb = color or VERT
        p.paragraph_format.space_before = Pt(12)
        p.paragraph_format.space_after  = Pt(6)
        return p

    def add_separator():
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after  = Pt(4)
        run = p.add_run("─" * 80)
        run.font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
        run.font.size = Pt(8)

    def add_label_value(label, value, label_color=None):
        p = doc.add_paragraph()
        run_label = p.add_run(f"{label} : ")
        run_label.bold = True
        run_label.font.size = Pt(10)
        run_label.font.color.rgb = label_color or VERT
        run_val = p.add_run(str(value))
        run_val.font.size = Pt(10)
        p.paragraph_format.space_after = Pt(2)

    # ── PAGE DE TITRE ──
    doc.add_paragraph()
    p_titre = doc.add_paragraph()
    p_titre.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p_titre.add_run("ABED ACADEMY")
    run.bold = True
    run.font.size = Pt(28)
    run.font.color.rgb = VERT

    p_sous = doc.add_paragraph()
    p_sous.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run2 = p_sous.add_run("Contenu Marketing Quotidien")
    run2.font.size = Pt(16)
    run2.font.color.rgb = GRIS

    p_date = doc.add_paragraph()
    p_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run3 = p_date.add_run(f"{date.today().strftime('%A %d %B %Y').capitalize()}")
    run3.font.size = Pt(12)
    run3.italic = True
    run3.font.color.rgb = GRIS

    doc.add_paragraph()
    p_url = doc.add_paragraph()
    p_url.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run4 = p_url.add_run("academy.abedong.org")
    run4.font.size = Pt(12)
    run4.font.color.rgb = VERT_CLAIR
    run4.underline = True

    doc.add_page_break()

    # ── SECTION 1 : POSTS LINKEDIN ──
    add_heading("PARTIE 1 — Posts LinkedIn (5 publications)", level=1)
    p_intro = doc.add_paragraph(
        "Ces 5 posts sont prêts à être publiés sur la page LinkedIn de ABED Academy. "
        "Chaque post est basé sur une offre d'emploi réelle du jour et met en avant "
        "la formation ABED correspondante."
    )
    p_intro.paragraph_format.space_after = Pt(12)

    for i, post in enumerate(posts_linkedin):
        add_separator()
        add_heading(f"Post LinkedIn #{i+1} — {post.get('titre','')}", level=2)

        p_content = doc.add_paragraph(post.get("contenu",""))
        p_content.paragraph_format.left_indent  = Cm(1)
        p_content.paragraph_format.space_after  = Pt(8)

        p_tags = doc.add_paragraph()
        run_tags = p_tags.add_run(post.get("hashtags",""))
        run_tags.font.color.rgb = RGBColor(0x18, 0x5F, 0xA5)
        run_tags.font.size = Pt(9)
        p_tags.paragraph_format.left_indent = Cm(1)

        doc.add_paragraph()

    doc.add_page_break()

    # ── SECTION 2 : SCRIPTS TIKTOK ──
    add_heading("PARTIE 2 — Scripts TikTok (2 vidéos)", level=1)
    p_intro2 = doc.add_paragraph(
        "Ces 2 scripts TikTok sont conçus pour des vidéos courtes (30-60 secondes). "
        "Ils utilisent les offres d'emploi réelles du jour pour capter l'attention "
        "et promouvoir les formations ABED Academy."
    )
    p_intro2.paragraph_format.space_after = Pt(12)

    for i, script in enumerate(scripts_tiktok):
        add_separator()
        add_heading(f"Script TikTok #{i+1} — {script.get('titre','')}", level=2, color=ORANGE)

        add_label_value("Accroche visuelle (3 premières secondes)",
                        script.get("accroche_visuelle",""), label_color=ORANGE)

        add_heading("Texte parlé (voix off / face caméra)", level=3)
        p_script = doc.add_paragraph(script.get("script_parle",""))
        p_script.paragraph_format.left_indent = Cm(1)
        p_script.paragraph_format.space_after = Pt(6)

        add_label_value("Texte à afficher à l'écran", script.get("texte_ecran",""))

        p_tags2 = doc.add_paragraph()
        run_t = p_tags2.add_run(script.get("hashtags",""))
        run_t.font.color.rgb = RGBColor(0x18, 0x5F, 0xA5)
        run_t.font.size = Pt(9)
        p_tags2.paragraph_format.left_indent = Cm(1)

        doc.add_paragraph()

    doc.add_page_break()

    # ── SECTION 3 : RAPPEL OFFRES ──
    add_heading("ANNEXE — Récapitulatif des 10 offres analysées", level=1)
    for i, offre in enumerate(offers):
        p = doc.add_paragraph()
        run_num = p.add_run(f"{i+1}. ")
        run_num.bold = True
        run_num.font.color.rgb = VERT
        run_titre = p.add_run(offre.get("titre",""))
        run_titre.bold = True
        run_titre.font.size = Pt(10)

        p2 = doc.add_paragraph()
        p2.paragraph_format.left_indent = Cm(0.8)
        run_org = p2.add_run(f"{offre.get('org','')} | {offre.get('pays','')} | ")
        run_org.font.size = Pt(9)
        run_org.font.color.rgb = GRIS
        run_form = p2.add_run(offre.get("_formation_matchee",{}).get("nom",""))
        run_form.font.size = Pt(9)
        run_form.font.color.rgb = VERT_CLAIR
        run_form.bold = True
        p2.paragraph_format.space_after = Pt(4)

    doc.save(output_path)
    log.info(f"Word généré : {output_path}")
    return output_path


# ──────────────────────────────────────────────
# ENVOI EMAIL
# ──────────────────────────────────────────────
def send_daily_email(excel_path: Path, word_path: Path, offers: list[dict]):
    """Envoie l'email quotidien via Resend."""
    import base64
    import resend

    resend.api_key = os.environ.get("RESEND_API_KEY", "")

    DESTINATAIRES  = [
        "olla.admi@gmail.com",
        "adriendogo@gmail.com",
        "prudencedogo@gmail.com",
    ]

    today     = date.today().strftime("%d/%m/%Y")
    nb_offres = len(offers)
    secteurs  = list(set(o.get("secteur", "") for o in offers))[:4]

    corps_html = f"""
<html><body style="font-family: Arial, sans-serif; color: #2C2C2A; max-width: 600px; margin: 0 auto;">
<div style="background: #0F6E56; padding: 24px; border-radius: 8px 8px 0 0; text-align: center;">
  <h1 style="color: white; margin: 0; font-size: 22px;">ABED ACADEMY</h1>
  <p style="color: #E1F5EE; margin: 4px 0 0; font-size: 14px;">Bulletin Marketing Quotidien — {today}</p>
</div>
<div style="background: #fff; padding: 24px; border: 1px solid #E0DED8;">
  <p>Bonjour à toute l'équipe,</p>
  <p>Voici le bulletin marketing ABED Academy du <strong>{today}</strong>.
  L'agent a analysé <strong>{nb_offres} offres</strong> dans les secteurs :
  <strong>{", ".join(secteurs)}</strong>.</p>
  <h3 style="color: #0F6E56;">📎 Fichiers joints</h3>
  <div style="background: #E1F5EE; padding: 12px 16px; border-radius: 6px; margin-bottom: 12px;">
    <strong>📊 Excel</strong> — Top 10 offres + formation ABED recommandée
  </div>
  <div style="background: #FAEEDA; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px;">
    <strong>📝 Word</strong> — 5 posts LinkedIn + 2 scripts TikTok
  </div>
  <h3 style="color: #0F6E56;">🎯 Top 5 offres du jour</h3>
  <table style="width:100%; border-collapse: collapse; font-size: 13px;">
    <tr style="background: #0F6E56; color: white;">
      <th style="padding: 8px; text-align: left;">Poste</th>
      <th style="padding: 8px; text-align: left;">Organisation</th>
      <th style="padding: 8px; text-align: left;">Formation ABED</th>
    </tr>
    {"".join(f'<tr style="background:{"#F8F7F2" if i%2==0 else "#fff"};"><td style="padding:8px;border-bottom:1px solid #E0DED8;"><strong>{o.get("titre","")[:55]}</strong></td><td style="padding:8px;border-bottom:1px solid #E0DED8;color:#5F5E5A;">{o.get("org","")[:35]}</td><td style="padding:8px;border-bottom:1px solid #E0DED8;color:#0F6E56;font-size:12px;">{o.get("_formation_matchee",{}).get("nom","")[:35]}</td></tr>' for i,o in enumerate(offers[:5]))}
  </table>
  <div style="margin-top:24px;padding:16px;background:#E1F5EE;border-radius:6px;text-align:center;">
    <a href="https://academy.abedong.org" style="color:#0F6E56;font-weight:600;">academy.abedong.org</a>
  </div>
</div>
<div style="background:#F8F7F2;padding:12px;text-align:center;font-size:11px;color:#888;">
  Généré automatiquement — {datetime.now().strftime('%d/%m/%Y à %H:%M')}
</div>
</body></html>
"""

    # Pièces jointes
    attachments = []
    for path, filename, ctype in [
        (excel_path,
         f"ABED_Top10_{date.today().isoformat()}.xlsx",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (word_path,
         f"ABED_Marketing_{date.today().isoformat()}.docx",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ]:
        if path.exists():
            with open(path, "rb") as f:
                content = base64.b64encode(f.read()).decode()
            attachments.append({
                "filename": filename,
                "content":  content,
                "type":     ctype,
            })

    params = {
        "from":        "ABED Academy <onboarding@resend.dev>",
        "to":          DESTINATAIRES,
        "subject":     f"[ABED Academy] Bulletin Marketing {today} — {nb_offres} offres",
        "html":        corps_html,
        "attachments": attachments,
    }

    try:
        response = resend.Emails.send(params)
        log.info(f"Email envoyé via Resend — id: {response['id']}")
    except Exception as e:
        log.error(f"Erreur Resend: {e}")
        raise

    
# ──────────────────────────────────────────────
# FONCTION PRINCIPALE
# ──────────────────────────────────────────────

def run_marketing_pipeline(offers: list[dict], output_dir: Path):
    """
    Pipeline marketing complet :
    1. Sélectionne top 10 offres pertinentes pour ABED
    2. Enrichit avec Claude (compétences + argumentaire)
    3. Génère Excel
    4. Génère Word (LinkedIn + TikTok)
    5. Envoie email
    """
    if not offers:
        log.warning("Aucune offre disponible pour le pipeline marketing.")
        return

    log.info("=== PIPELINE MARKETING ABED ACADEMY ===")

    # 1. Sélection top 10
    top10 = select_top10_for_abed(offers)
    log.info(f"Top 10 offres sélectionnées")

    # 2. Enrichissement IA
    top10 = enrich_offers_for_marketing(top10)

    # 3. Génération fichiers
    output_dir.mkdir(parents=True, exist_ok=True)
    excel_path = output_dir / f"ABED_Top10_{date.today().isoformat()}.xlsx"
    word_path  = output_dir / f"ABED_Marketing_{date.today().isoformat()}.docx"

    generate_excel(top10, excel_path)
    generate_word(top10, word_path)

    # 4. Envoi email
    send_daily_email(excel_path, word_path, top10)

    log.info("=== PIPELINE MARKETING TERMINÉ ===")
