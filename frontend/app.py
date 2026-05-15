"""
StatIQ — AI Analytics Platform
Enterprise analytics for non-technical stakeholders.
"""
import json
import os
import re
import time
import uuid
from datetime import datetime
import requests
import plotly.io as pio
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")
API_KEY = os.environ.get("API_KEY", "")
_HEADERS = {"X-Api-Key": API_KEY} if API_KEY else {}

st.set_page_config(
    page_title="StatIQ Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}
#MainMenu, footer { visibility: hidden; }

/* Sidebar logo */
.sidebar-logo {
    font-size: 22px;
    font-weight: 700;
    color: #60A5FA;
    letter-spacing: -0.5px;
    margin-bottom: 2px;
}
.sidebar-tagline {
    font-size: 12px;
    color: #64748B;
    margin-bottom: 16px;
}

/* Status pill */
.status-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 500;
    margin-bottom: 4px;
}
.status-on  { background: rgba(16,185,129,.15); color: #34D399; border: 1px solid rgba(52,211,153,.2); }
.status-off { background: rgba(251,191,36,.1);  color: #FCD34D; border: 1px solid rgba(252,211,77,.2); }

/* Section label */
.section-label {
    font-size: 10px;
    font-weight: 700;
    color: #475569;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    margin: 16px 0 6px 0;
}

/* Column row */
.col-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 5px 0;
    border-bottom: 1px solid #1E293B;
    font-size: 13px;
    color: #CBD5E1;
}
.col-badge {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.t-num  { background: rgba(59,130,246,.2);  color: #93C5FD; }
.t-txt  { background: rgba(16,185,129,.15); color: #6EE7B7; }
.t-date { background: rgba(251,191,36,.15); color: #FCD34D; }
.t-bool { background: rgba(167,139,250,.2); color: #C4B5FD; }
.t-other{ background: rgba(100,116,139,.2); color: #94A3B8; }

/* Welcome screen */
.welcome-wrap {
    text-align: center;
    max-width: 560px;
    margin: 60px auto 40px auto;
}
.welcome-title {
    font-size: 32px;
    font-weight: 700;
    color: #F1F5F9;
    letter-spacing: -0.5px;
    margin-bottom: 12px;
}
.welcome-sub {
    font-size: 16px;
    color: #94A3B8;
    line-height: 1.7;
    margin-bottom: 32px;
}

/* Feature card */
.feat-card {
    background: #1E293B;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 22px;
    height: 100%;
}
.feat-icon { font-size: 30px; margin-bottom: 10px; }
.feat-title {
    font-size: 15px;
    font-weight: 600;
    color: #F1F5F9;
    margin-bottom: 6px;
}
.feat-desc { font-size: 13px; color: #94A3B8; line-height: 1.5; }

/* Page header */
.page-header {
    padding-bottom: 20px;
    margin-bottom: 24px;
    border-bottom: 1px solid #1E293B;
}
.page-title {
    font-size: 26px;
    font-weight: 700;
    color: #F1F5F9;
    letter-spacing: -0.3px;
}
.page-sub { font-size: 14px; color: #64748B; margin-top: 4px; }

/* Agent step */
.step-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    background: #1E293B;
    border-left: 3px solid #3B82F6;
    border-radius: 0 8px 8px 0;
    padding: 10px 12px;
    margin: 5px 0;
}
.step-icon { font-size: 16px; flex-shrink: 0; line-height: 1.4; }
.step-name {
    font-size: 10px;
    font-weight: 700;
    color: #60A5FA;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.step-desc { font-size: 13px; color: #CBD5E1; }
.step-time { font-size: 11px; color: #475569; }

/* Running pulse */
.running-step {
    display: flex;
    align-items: center;
    gap: 8px;
    color: #60A5FA;
    font-size: 13px;
    font-weight: 500;
    padding: 8px 0;
}

/* Done banner */
.done-banner {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(16,185,129,.12);
    border: 1px solid rgba(52,211,153,.25);
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
    color: #34D399;
    font-weight: 500;
    margin-bottom: 4px;
}
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────

for key, default in [
    ("session_id", str(uuid.uuid4())),
    ("messages", []),
    ("connected", False),
    ("profile", None),
    ("dataset_name", None),
    ("pii_warning", None),
    ("pii_acknowledged", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── API helpers ───────────────────────────────────────────────────────────────

def connect_bigquery(dataset_ref: str) -> dict:
    r = requests.post(
        f"{BACKEND_URL}/api/datasets/connect",
        headers=_HEADERS,
        data={"session_id": st.session_state.session_id, "source": "bigquery", "dataset_ref": dataset_ref},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def upload_file(file) -> dict:
    r = requests.post(
        f"{BACKEND_URL}/api/datasets/upload",
        headers=_HEADERS,
        files={"file": (file.name, file.getvalue(), "application/octet-stream")},
        data={"session_id": st.session_state.session_id},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def stream_analysis(question: str):
    url = f"{BACKEND_URL}/api/analyze/stream"
    params = {"session_id": st.session_state.session_id, "question": question}
    with requests.get(url, params=params, headers=_HEADERS, stream=True, timeout=180) as resp:
        for line in resp.iter_lines():
            if line and line.startswith(b"data: "):
                try:
                    yield json.loads(line[6:])
                except json.JSONDecodeError:
                    pass


@st.cache_data(ttl=300)
def get_catalog() -> list:
    try:
        r = requests.get(f"{BACKEND_URL}/api/datasets/catalog", timeout=10)
        return r.json().get("datasets", [])
    except Exception:
        return []


def _col_badge(dtype: str) -> str:
    d = dtype.lower()
    if any(t in d for t in ["int", "float", "double", "decimal", "numeric"]):
        return '<span class="col-badge t-num">NUM</span>'
    if any(t in d for t in ["date", "time", "timestamp"]):
        return '<span class="col-badge t-date">DATE</span>'
    if any(t in d for t in ["varchar", "text", "string", "object", "char"]):
        return '<span class="col-badge t-txt">TXT</span>'
    if any(t in d for t in ["bool", "boolean"]):
        return '<span class="col-badge t-bool">BOOL</span>'
    return '<span class="col-badge t-other">—</span>'


def _agent_icon(agent: str) -> str:
    return {"sql": "🗄️", "stat": "📐", "viz": "📊", "forecast": "🔮", "router": "🧭"}.get(agent.lower(), "⚙️")


def _md_to_html_body(text: str) -> str:
    """Convert basic markdown to HTML paragraphs."""
    lines = text.split("\n")
    out, in_ul = [], False
    for line in lines:
        s = line.strip()
        if not s:
            if in_ul:
                out.append("</ul>"); in_ul = False
            out.append("")
            continue
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\*(.+?)\*",     r"<em>\1</em>",          s)
        s = re.sub(r"`(.+?)`",       r"<code>\1</code>",       s)
        if line.startswith("### "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h3>{s[4:]}</h3>")
        elif line.startswith("## "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h2>{s[3:]}</h2>")
        elif line.startswith("# "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h1>{s[2:]}</h1>")
        elif re.match(r"^[-*] ", line):
            if not in_ul: out.append("<ul>"); in_ul = True
            out.append(f"<li>{s[2:]}</li>")
        else:
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<p>{s}</p>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def _build_report_html(question: str, narrative: str, chart_json: str | None, dataset: str) -> str:
    chart_html = ""
    if chart_json:
        try:
            fig = pio.from_json(chart_json)
            fig.update_layout(paper_bgcolor="white", plot_bgcolor="white",
                              font_color="#0F172A", margin=dict(t=40, b=40, l=20, r=20))
            chart_html = f'<div class="chart">{fig.to_html(full_html=False, include_plotlyjs="cdn")}</div>'
        except Exception:
            pass

    date_str = datetime.now().strftime("%d/%m/%Y à %H:%M")
    body = _md_to_html_body(narrative)

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>StatIQ — {dataset}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Inter', system-ui, sans-serif; background: #F8FAFC;
          color: #0F172A; padding: 0; }}
  .page {{ max-width: 860px; margin: 0 auto; background: white;
           min-height: 100vh; padding: 48px 56px; }}
  header {{ display: flex; justify-content: space-between; align-items: flex-end;
            padding-bottom: 20px; border-bottom: 2px solid #2563EB; margin-bottom: 36px; }}
  .logo {{ font-size: 20px; font-weight: 700; color: #2563EB; letter-spacing: -0.3px; }}
  .meta {{ font-size: 12px; color: #94A3B8; text-align: right; line-height: 1.6; }}
  .question-block {{ background: #EFF6FF; border-left: 4px solid #2563EB;
                     padding: 16px 20px; border-radius: 0 10px 10px 0;
                     margin-bottom: 36px; }}
  .question-label {{ font-size: 10px; font-weight: 700; color: #2563EB;
                     text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }}
  .question-text {{ font-size: 16px; font-weight: 500; color: #1E3A5F; line-height: 1.5; }}
  .narrative p    {{ font-size: 15px; line-height: 1.8; color: #1E293B; margin-bottom: 12px; }}
  .narrative h1   {{ font-size: 22px; font-weight: 700; color: #0F172A; margin: 28px 0 12px; }}
  .narrative h2   {{ font-size: 18px; font-weight: 600; color: #1E3A5F; margin: 24px 0 10px; }}
  .narrative h3   {{ font-size: 15px; font-weight: 600; color: #1E40AF; margin: 20px 0 8px; }}
  .narrative ul   {{ padding-left: 20px; margin-bottom: 12px; }}
  .narrative li   {{ font-size: 15px; line-height: 1.7; color: #1E293B; margin-bottom: 4px; }}
  .narrative code {{ background: #F1F5F9; padding: 2px 6px; border-radius: 4px;
                     font-size: 13px; font-family: monospace; }}
  .chart          {{ margin: 32px 0; border: 1px solid #E2E8F0; border-radius: 10px;
                     overflow: hidden; }}
  footer {{ margin-top: 56px; padding-top: 16px; border-top: 1px solid #E2E8F0;
            font-size: 11px; color: #CBD5E1; text-align: center; }}
  @media print {{ body {{ background: white; }} .page {{ padding: 24px 32px; }} }}
</style>
</head>
<body>
<div class="page">
  <header>
    <div class="logo">📊 StatIQ</div>
    <div class="meta">{dataset}<br>{date_str}</div>
  </header>
  <div class="question-block">
    <div class="question-label">Question</div>
    <div class="question-text">{question}</div>
  </div>
  <div class="narrative">{body}</div>
  {chart_html}
  <footer>StatIQ Analytics Intelligence Platform &nbsp;·&nbsp; Confidentiel &nbsp;·&nbsp; {date_str}</footer>
</div>
</body>
</html>"""


def _build_report_markdown(question: str, narrative: str, dataset: str) -> str:
    date_str = datetime.now().strftime("%d/%m/%Y à %H:%M")
    return f"""# Rapport d'analyse — {dataset}

> Généré par StatIQ le {date_str}

---

## Question

> {question}

---

## Analyse

{narrative}

---

*StatIQ Analytics Intelligence Platform · Confidentiel*
"""


def _build_report_pdf(question: str, narrative: str, chart_json: str | None, dataset: str) -> bytes:
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, HRFlowable
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2.2*cm, rightMargin=2.2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    date_str = datetime.now().strftime("%d/%m/%Y à %H:%M")
    blue  = colors.HexColor("#2563EB")
    navy  = colors.HexColor("#1E3A5F")
    slate = colors.HexColor("#64748B")
    light = colors.HexColor("#EFF6FF")
    gray  = colors.HexColor("#94A3B8")

    def S(name, **kw):
        return ParagraphStyle(name, **kw)

    sLogo   = S("logo",   fontSize=16, fontName="Helvetica-Bold", textColor=blue, spaceAfter=2)
    sMeta   = S("meta",   fontSize=8,  textColor=slate, spaceAfter=0)
    sQlabel = S("qlabel", fontSize=8,  fontName="Helvetica-Bold", textColor=blue,
                          spaceBefore=12, spaceAfter=3)
    sQ      = S("q",      fontSize=12, fontName="Helvetica-Bold", textColor=navy,
                          leading=18, spaceAfter=16)
    sH1     = S("h1",     fontSize=14, fontName="Helvetica-Bold", textColor=colors.HexColor("#0F172A"),
                          spaceBefore=14, spaceAfter=4)
    sH2     = S("h2",     fontSize=12, fontName="Helvetica-Bold", textColor=navy,
                          spaceBefore=12, spaceAfter=4)
    sH3     = S("h3",     fontSize=11, fontName="Helvetica-Bold", textColor=blue,
                          spaceBefore=10, spaceAfter=3)
    sBody   = S("body",   fontSize=10, leading=16, textColor=colors.HexColor("#1E293B"), spaceAfter=5)
    sBullet = S("bullet", fontSize=10, leading=16, textColor=colors.HexColor("#1E293B"),
                          leftIndent=14, spaceAfter=3)
    sFooter = S("footer", fontSize=8,  textColor=gray, alignment=TA_CENTER)

    story = []

    # Header
    story.append(Paragraph("StatIQ", sLogo))
    story.append(Paragraph(f"{dataset} &nbsp;·&nbsp; {date_str}", sMeta))
    story.append(HRFlowable(width="100%", thickness=2, color=blue, spaceBefore=8, spaceAfter=14))

    # Question block
    story.append(Paragraph("QUESTION", sQlabel))
    story.append(Paragraph(question, sQ))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E2E8F0"), spaceAfter=12))

    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors as rl_colors

    def _fmt(text: str) -> str:
        """Apply inline markdown to a string for ReportLab Paragraph."""
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        t = re.sub(r"__(.+?)__",     r"<b>\1</b>", t)
        t = re.sub(r"\*(.+?)\*",     r"<i>\1</i>", t)
        t = re.sub(r"_(.+?)_",       r"<i>\1</i>", t)
        t = re.sub(r"`(.+?)`",       r"<font name='Courier'>\1</font>", t)
        return t

    def _parse_md_table(lines: list[str]) -> list[list[str]]:
        """Parse markdown table lines into a list of rows."""
        rows = []
        for ln in lines:
            ln = ln.strip()
            if re.match(r"^\|[-:| ]+\|$", ln):
                continue  # separator row
            cells = [c.strip() for c in ln.strip("|").split("|")]
            rows.append(cells)
        return rows

    def _md_table_flowable(rows: list[list[str]]):
        """Convert parsed markdown table rows to a styled ReportLab Table."""
        col_count = max(len(r) for r in rows)
        available = 16.6 * cm
        col_w = available / col_count

        data = []
        for row in rows:
            # pad short rows
            padded = row + [""] * (col_count - len(row))
            data.append([Paragraph(_fmt(c), sBody) for c in padded])

        tbl = Table(data, colWidths=[col_w] * col_count, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0),  colors.HexColor("#EFF6FF")),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  navy),
            ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
            ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",   (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
            ("LEFTPADDING",  (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return tbl

    # Narrative — collect table blocks, then emit
    lines = narrative.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()

        # Skip empty
        if not s:
            story.append(Spacer(1, 0.15*cm))
            i += 1
            continue

        # Skip bare `---` separators
        if re.match(r"^-{3,}$", s):
            i += 1
            continue

        # Markdown table block — collect all consecutive table lines
        if s.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            rows = _parse_md_table(table_lines)
            if rows:
                story.append(Spacer(1, 0.2*cm))
                story.append(_md_table_flowable(rows))
                story.append(Spacer(1, 0.3*cm))
            continue

        # Headings
        if line.startswith("# "):
            story.append(Paragraph(_fmt(s[2:]), sH1))
        elif line.startswith("## "):
            story.append(Paragraph(_fmt(s[3:]), sH2))
        elif line.startswith("### "):
            story.append(Paragraph(_fmt(s[4:]), sH3))
        # Numbered list
        elif re.match(r"^\d+\.\s", s):
            story.append(Paragraph(_fmt(s), sBullet))
        # Bullet list
        elif re.match(r"^[-*>] ", s):
            story.append(Paragraph(f"• {_fmt(s[2:])}", sBullet))
        # Normal paragraph
        else:
            story.append(Paragraph(_fmt(s), sBody))
        i += 1

    # Chart
    if chart_json:
        try:
            import plotly.io as _pio
            fig = _pio.from_json(chart_json)
            # Wrap long titles and use light theme for print
            current_title = (fig.layout.title.text or "") if fig.layout.title else ""
            if len(current_title) > 60:
                current_title = current_title[:57] + "…"
            fig.update_layout(
                paper_bgcolor="white",
                plot_bgcolor="#F8FAFC",
                font_color="#0F172A",
                title_text=current_title,
                title_font_size=13,
                margin=dict(t=50, b=50, l=40, r=40),
            )
            img_bytes = fig.to_image(format="png", width=800, height=440, scale=2)
            story.append(Spacer(1, 0.5*cm))
            story.append(Image(BytesIO(img_bytes), width=16.6*cm, height=9.1*cm))
        except Exception:
            pass

    # Footer
    story.append(Spacer(1, 1*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E2E8F0")))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(f"StatIQ Analytics Intelligence Platform &nbsp;·&nbsp; Confidentiel &nbsp;·&nbsp; {date_str}", sFooter))

    doc.build(story)
    return buf.getvalue()


def _render_download_buttons(question: str, narrative: str, chart_json: str | None, dataset: str, key: str) -> None:
    """Single popover button with HTML / PDF / Markdown export options."""
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    with st.popover("📤 Exporter le rapport"):
        st.markdown("**Choisir le format d'export :**")

        html = _build_report_html(question, narrative, chart_json, dataset)
        st.download_button(
            "⬇ Rapport HTML",
            data=html,
            file_name=f"statiq_{ts}.html",
            mime="text/html",
            use_container_width=True,
            key=f"{key}_html",
            help="Page web autonome avec graphique interactif",
        )

        try:
            pdf = _build_report_pdf(question, narrative, chart_json, dataset)
            st.download_button(
                "⬇ Rapport PDF",
                data=pdf,
                file_name=f"statiq_{ts}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"{key}_pdf",
                help="Document imprimable avec graphique",
            )
        except Exception:
            st.button("⬇ PDF (indisponible)", disabled=True,
                      use_container_width=True, key=f"{key}_pdf_err")

        md = _build_report_markdown(question, narrative, dataset)
        st.download_button(
            "⬇ Rapport Markdown",
            data=md,
            file_name=f"statiq_{ts}.md",
            mime="text/markdown",
            use_container_width=True,
            key=f"{key}_md",
            help="Format texte brut pour Notion, GitHub, etc.",
        )


def _agent_label(agent: str) -> str:
    return {
        "sql": "Interrogation des données",
        "stat": "Analyse statistique",
        "viz": "Génération du graphique",
        "forecast": "Calcul des prévisions",
        "router": "Sélection de la méthode",
    }.get(agent.lower(), agent.capitalize())


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown('<div class="sidebar-logo">📊 StatIQ</div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-tagline">Analytics Intelligence Platform</div>', unsafe_allow_html=True)

    if st.session_state.connected:
        name = st.session_state.dataset_name or "Dataset"
        st.markdown(f'<div class="status-pill status-on">● Connecté &mdash; {name}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-pill status-off">○ Aucune source connectée</div>', unsafe_allow_html=True)

    st.divider()
    st.markdown('<div class="section-label">Source de données</div>', unsafe_allow_html=True)

    _is_local = "localhost" in BACKEND_URL or "127.0.0.1" in BACKEND_URL
    _source_options = ["Importer un fichier"] if _is_local else ["Données GCP", "Importer un fichier"]
    source_tab = st.radio("Type de source", _source_options, horizontal=True, label_visibility="collapsed")

    if source_tab == "Données GCP":
        catalog = get_catalog()
        if catalog:
            selected_name = st.selectbox("Jeu de données", [d["name"] for d in catalog], label_visibility="collapsed")
            selected_meta = next((d for d in catalog if d["name"] == selected_name), None)
            if selected_meta:
                st.caption(selected_meta["description"])
                st.markdown('<div class="section-label">Questions suggérées</div>', unsafe_allow_html=True)
                for q in selected_meta["sample_questions"][:3]:
                    if st.button(q, key=f"sq_{q[:20]}", use_container_width=True):
                        st.session_state["quick_question"] = q
        else:
            st.warning("Backend non disponible.")
            selected_meta = None

        if st.button("Connecter", use_container_width=True, type="primary", disabled=not catalog):
            with st.spinner("Connexion à BigQuery…"):
                try:
                    result = connect_bigquery(selected_meta["ref"])
                    st.session_state.connected = True
                    st.session_state.profile = result.get("profile")
                    st.session_state.dataset_name = selected_name
                    st.success("Connecté ✓")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur : {e}")

    else:
        uploaded = st.file_uploader(
            "Glissez votre fichier ici",
            type=["csv", "xlsx", "xls"],
            label_visibility="collapsed",
        )
        st.caption("CSV ou Excel · Max 100 MB")
        if uploaded and st.button("Charger et analyser", use_container_width=True, type="primary"):
            with st.spinner("Chargement en cours…"):
                try:
                    result = upload_file(uploaded)
                    st.session_state.connected = True
                    st.session_state.profile = result.get("profile")
                    st.session_state.dataset_name = uploaded.name
                    st.session_state.pii_warning = result.get("pii_warning")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur : {e}")

    # Dataset profile
    if st.session_state.profile:
        p = st.session_state.profile
        st.divider()
        st.markdown('<div class="section-label">Aperçu du dataset</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        c1.metric("Lignes", f"{p.get('n_rows', 0):,}")
        c2.metric("Colonnes", p.get("n_cols", 0))
        with st.expander("Colonnes disponibles"):
            for col in p.get("columns", [])[:25]:
                badge = _col_badge(col.get("dtype", ""))
                null_pct = col.get("null_pct", 0)
                null_info = f'<span style="color:#94A3B8;font-size:11px">{null_pct}% vide</span>' if null_pct > 0 else ""
                st.markdown(
                    f'<div class="col-row"><span>{col["name"]}{" " + null_info if null_info else ""}</span>{badge}</div>',
                    unsafe_allow_html=True,
                )

    st.divider()
    if st.button("Nouvelle session", use_container_width=True):
        for k in ["messages", "connected", "profile", "dataset_name", "pii_warning", "pii_acknowledged"]:
            st.session_state[k] = [] if k == "messages" else (False if k in ["connected", "pii_acknowledged"] else None)
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()

    st.caption(f"Session `{st.session_state.session_id[:8]}`")

# ── Main area ─────────────────────────────────────────────────────────────────

if not st.session_state.connected:
    # Welcome screen
    st.markdown("""
    <div class="welcome-wrap">
        <div class="welcome-title">Bienvenue sur StatIQ</div>
        <div class="welcome-sub">
            Posez des questions sur vos données en langage naturel et obtenez
            des analyses, graphiques et prévisions en quelques secondes —
            sans écrire une seule ligne de code.
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    features = [
        ("📊", "Analyses statistiques", "Distributions, corrélations, tests de significativité — interprétés en français clair."),
        ("📈", "Visualisations interactives", "Graphiques générés automatiquement, adaptés au type de question posé."),
        ("🔮", "Prévisions IA", "Modèles de forecasting pour anticiper les tendances et planifier vos décisions."),
    ]
    for col, (icon, title, desc) in zip([col1, col2, col3], features):
        with col:
            st.markdown(
                f'<div class="feat-card"><div class="feat-icon">{icon}</div>'
                f'<div class="feat-title">{title}</div>'
                f'<div class="feat-desc">{desc}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)
    st.info("👈 Connectez une source de données dans le panneau latéral pour commencer.", icon=None)

else:
    # Page header
    dataset_name = st.session_state.dataset_name or "Dataset"
    st.markdown(
        f'<div class="page-header">'
        f'<div class="page-title">Analyse — {dataset_name}</div>'
        f'<div class="page-sub">Posez votre question en français ou en anglais, l\'IA sélectionne la meilleure approche.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # PII consent gate
    if st.session_state.pii_warning and not st.session_state.pii_acknowledged:
        st.warning(
            "**Données personnelles détectées dans ce fichier**\n\n"
            f"Les colonnes suivantes contiennent potentiellement des données sensibles : "
            f"**{st.session_state.pii_warning.split(':')[1].split('.')[0].strip()}**\n\n"
            "Avant de continuer, assurez-vous que :\n"
            "- Vous êtes autorisé à traiter ces données (RGPD / politique interne)\n"
            "- Les données sont anonymisées ou pseudonymisées si nécessaire\n"
            "- Cette analyse ne sera pas partagée sans contrôle préalable",
            icon=None,
        )
        col_a, col_b = st.columns([1, 2])
        with col_a:
            if st.button("J'ai compris, continuer", type="primary", use_container_width=True):
                st.session_state.pii_acknowledged = True
                st.rerun()
        with col_b:
            if st.button("Annuler et supprimer le fichier", use_container_width=True):
                for k in ["messages", "connected", "profile", "dataset_name", "pii_warning", "pii_acknowledged"]:
                    st.session_state[k] = [] if k == "messages" else (False if k in ["connected", "pii_acknowledged"] else None)
                st.session_state.session_id = str(uuid.uuid4())
                st.rerun()
        st.stop()

    if st.session_state.pii_warning and st.session_state.pii_acknowledged:
        st.info("Données sensibles — analyse autorisée par l'utilisateur.", icon="🔒")

    # Suggested questions when conversation is empty
    if not st.session_state.messages:
        catalog = get_catalog()
        meta = next((d for d in catalog if d["name"] == dataset_name), None)
        suggestions = meta["sample_questions"] if meta else [
            "Quelle est la distribution des valeurs principales ?",
            "Y a-t-il des corrélations significatives entre les variables ?",
            "Montre l'évolution dans le temps.",
            "Quelles sont les valeurs les plus élevées et les plus basses ?",
        ]
        st.markdown("**Par où commencer ?**")
        cols = st.columns(2)
        for i, q in enumerate(suggestions[:4]):
            if cols[i % 2].button(q, key=f"hint_{i}", use_container_width=True):
                st.session_state["quick_question"] = q
        st.divider()

    # Chat history replay
    for i, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                if msg.get("narrative"):
                    st.markdown(msg["narrative"])
                if msg.get("chart_json"):
                    try:
                        st.plotly_chart(pio.from_json(msg["chart_json"]), use_container_width=True)
                    except Exception:
                        pass
                if msg.get("steps"):
                    n = len(msg["steps"])
                    with st.expander(f"{n} étape{'s' if n > 1 else ''} d'analyse", expanded=False):
                        for s in msg["steps"]:
                            icon = _agent_icon(s["agent"])
                            label = _agent_label(s["agent"])
                            ms = s.get("duration_ms", 0)
                            st.markdown(
                                f'<div class="step-row">'
                                f'<span class="step-icon">{icon}</span>'
                                f'<div><div class="step-name">{label}</div>'
                                f'<div class="step-time">{ms:.0f} ms</div></div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                if msg.get("narrative"):
                    q = st.session_state.messages[i - 1]["content"] if i > 0 else ""
                    _render_download_buttons(q, msg["narrative"], msg.get("chart_json"), dataset_name, key=f"dl_{i}")

    # Input
    prefill = st.session_state.pop("quick_question", None)
    question = st.chat_input("Posez votre question sur les données…") or prefill

    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            status_box    = st.empty()
            narrative_box = st.empty()
            chart_box     = st.empty()

            narrative_parts: list[str] = []
            chart_json = None
            steps: list[dict] = []
            _pending: dict = {}
            t0 = time.time()

            try:
                for event in stream_analysis(question):
                    etype = event.get("event")
                    edata = event.get("data")

                    if etype == "agent_start":
                        agent = edata.get("agent", "")
                        _pending = {"agent": agent, "input_summary": edata.get("input", "")}
                        label = _agent_label(agent)
                        status_box.markdown(
                            f'<div class="running-step">⚙️ &nbsp;<span>{label}…</span></div>',
                            unsafe_allow_html=True,
                        )

                    elif etype == "agent_done":
                        steps.append({
                            "agent": edata.get("agent", ""),
                            "input_summary": _pending.get("input_summary", ""),
                            "duration_ms": edata.get("duration_ms", 0),
                        })
                        _pending = {}

                    elif etype == "text_chunk":
                        narrative_parts.append(edata)
                        narrative_box.markdown("".join(narrative_parts))

                    elif etype == "chart":
                        chart_json = edata
                        try:
                            chart_box.plotly_chart(pio.from_json(chart_json), use_container_width=True)
                        except Exception:
                            pass

                    elif etype == "done":
                        elapsed = round(time.time() - t0, 1)
                        status_box.markdown(
                            f'<div class="done-banner">✓ Analyse terminée en {elapsed} s</div>',
                            unsafe_allow_html=True,
                        )

                    elif etype == "error":
                        st.error(edata)

            except Exception as e:
                st.error(f"Connexion interrompue : {e}")

            if steps:
                n = len(steps)
                with st.expander(f"{n} étape{'s' if n > 1 else ''} d'analyse", expanded=False):
                    for s in steps:
                        icon = _agent_icon(s["agent"])
                        label = _agent_label(s["agent"])
                        ms = s.get("duration_ms", 0)
                        st.markdown(
                            f'<div class="step-row">'
                            f'<span class="step-icon">{icon}</span>'
                            f'<div><div class="step-name">{label}</div>'
                            f'<div class="step-time">{ms:.0f} ms</div></div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

            final_narrative = "".join(narrative_parts)
            if final_narrative:
                _render_download_buttons(question, final_narrative, chart_json, dataset_name,
                                         key=f"dl_live_{uuid.uuid4().hex[:8]}")

            st.session_state.messages.append({
                "role": "assistant",
                "narrative": final_narrative,
                "chart_json": chart_json,
                "steps": steps,
            })
