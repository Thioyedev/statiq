"""
StatIQ — Streamlit Frontend
Connects to the FastAPI backend via HTTP + SSE streaming.
"""
import json
import os
import time
import uuid
import requests
import plotly.io as pio
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")

st.set_page_config(
    page_title="StatIQ — AI Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&display=swap');
html, body, [class*="css"] { font-family: 'IBM Plex Mono', monospace; }
.stChatMessage { border-radius: 10px; }
.agent-badge {
    display: inline-block;
    background: rgba(0,229,255,.12);
    color: #00E5FF;
    border: 1px solid rgba(0,229,255,.3);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-right: 6px;
}
.metric-card {
    background: rgba(17,24,39,.8);
    border: 1px solid #1E2D40;
    border-radius: 8px;
    padding: 12px 16px;
    text-align: center;
}
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []
if "connected" not in st.session_state:
    st.session_state.connected = False
if "profile" not in st.session_state:
    st.session_state.profile = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def connect_bigquery(dataset_ref: str) -> dict:
    r = requests.post(
        f"{BACKEND_URL}/api/datasets/connect",
        data={
            "session_id": st.session_state.session_id,
            "source": "bigquery",
            "dataset_ref": dataset_ref,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def upload_csv(file) -> dict:
    r = requests.post(
        f"{BACKEND_URL}/api/datasets/upload",
        files={"file": (file.name, file.getvalue(), "text/csv")},
        data={"session_id": st.session_state.session_id},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def stream_analysis(question: str):
    """Generator that yields parsed SSE events."""
    url = f"{BACKEND_URL}/api/analyze/stream"
    params = {"session_id": st.session_state.session_id, "question": question}
    with requests.get(url, params=params, stream=True, timeout=180) as resp:
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


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 📊 StatIQ")
    st.markdown("*AI-Powered Analytics Agent*")
    st.divider()

    # Data source selector
    st.markdown("### Données")
    source_tab = st.radio("Source", ["🌐 GCP Open Data", "📁 Upload CSV"], horizontal=True)

    if source_tab == "🌐 GCP Open Data":
        catalog = get_catalog()
        dataset_names = [d["name"] for d in catalog]
        selected = st.selectbox("Dataset", dataset_names)
        selected_meta = next((d for d in catalog if d["name"] == selected), None)

        if selected_meta:
            st.caption(selected_meta["description"])
            st.markdown("**Questions suggérées :**")
            for q in selected_meta["sample_questions"][:3]:
                if st.button(q, key=q, use_container_width=True):
                    st.session_state["quick_question"] = q

        if st.button("🔌 Connecter", use_container_width=True, type="primary"):
            with st.spinner("Connexion à BigQuery..."):
                try:
                    result = connect_bigquery(selected_meta["ref"])
                    st.session_state.connected = True
                    st.session_state.profile = result.get("profile")
                    st.success("Connecté ✓")
                except Exception as e:
                    st.error(f"Erreur : {e}")

    else:
        uploaded = st.file_uploader("Fichier CSV ou Excel", type=["csv", "xlsx", "xls"])
        if uploaded and st.button("📤 Charger", use_container_width=True, type="primary"):
            with st.spinner("Upload en cours..."):
                try:
                    result = upload_csv(uploaded)
                    st.session_state.connected = True
                    st.session_state.profile = result.get("profile")
                    st.success("Chargé ✓")
                except Exception as e:
                    st.error(f"Erreur : {e}")

    # Profile summary
    if st.session_state.profile:
        p = st.session_state.profile
        st.divider()
        st.markdown("### Profil dataset")
        col1, col2 = st.columns(2)
        col1.metric("Lignes", f"{p.get('n_rows', 0):,}")
        col2.metric("Colonnes", p.get("n_cols", 0))
        with st.expander("Colonnes"):
            for c in p.get("columns", [])[:15]:
                st.markdown(
                    f"`{c['name']}` — *{c['dtype']}* — {c['null_pct']}% null"
                )

    st.divider()
    if st.button("🗑 Nouvelle session", use_container_width=True):
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.session_state.connected = False
        st.session_state.profile = None
        st.rerun()

    st.caption(f"Session: `{st.session_state.session_id[:8]}…`")

# ── Main chat area ────────────────────────────────────────────────────────────

st.markdown("## Posez votre question analytique")

if not st.session_state.connected:
    st.info("👈 Connectez un dataset dans la barre latérale pour commencer.")
else:
    # Replay history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                if msg.get("narrative"):
                    st.markdown(msg["narrative"])
                if msg.get("chart_json"):
                    fig = pio.from_json(msg["chart_json"])
                    st.plotly_chart(fig, use_container_width=True)
                if msg.get("steps"):
                    with st.expander("🔍 Étapes de l'agent", expanded=False):
                        for step in msg["steps"]:
                            st.markdown(f'<span class="agent-badge">{step["agent"]}</span> {step["input_summary"]}', unsafe_allow_html=True)

    # Handle quick question from sidebar
    prefill = st.session_state.pop("quick_question", None)
    question = st.chat_input("Ex: Quelle est la tendance mensuelle des trajets en 2022 ?") or prefill

    if question:
        # Display user message
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        # Stream response
        with st.chat_message("assistant"):
            status_placeholder = st.empty()
            narrative_placeholder = st.empty()
            chart_placeholder = st.empty()
            steps_placeholder = st.empty()

            narrative_parts = []
            chart_json = None
            steps = []
            _pending_step: dict = {}
            t0 = time.time()

            try:
                for event in stream_analysis(question):
                    evt_type = event.get("event")
                    evt_data = event.get("data")

                    if evt_type == "agent_start":
                        agent = evt_data.get("agent", "")
                        _pending_step = {"agent": agent, "input_summary": evt_data.get("input", "")}
                        status_placeholder.markdown(
                            f'<span class="agent-badge">{agent}</span> En cours…', unsafe_allow_html=True
                        )

                    elif evt_type == "agent_done":
                        agent = evt_data.get("agent", "")
                        duration = evt_data.get("duration_ms", 0)
                        steps.append({
                            "agent": agent,
                            "input_summary": _pending_step.get("input_summary", ""),
                            "duration_ms": duration,
                        })
                        _pending_step = {}

                    elif evt_type == "text_chunk":
                        narrative_parts.append(evt_data)
                        narrative_placeholder.markdown("".join(narrative_parts))

                    elif evt_type == "chart":
                        chart_json = evt_data
                        try:
                            fig = pio.from_json(chart_json)
                            chart_placeholder.plotly_chart(fig, use_container_width=True)
                        except Exception:
                            pass

                    elif evt_type == "done":
                        elapsed = round(time.time() - t0, 1)
                        status_placeholder.caption(f"✓ Analyse terminée en {elapsed}s")

                    elif evt_type == "error":
                        st.error(f"Erreur : {evt_data}")

            except Exception as e:
                st.error(f"Connexion au backend perdue : {e}")

            # Display steps
            if steps:
                with st.expander("🔍 Étapes de l'agent", expanded=False):
                    for s in steps:
                        badge_html = f'<span class="agent-badge">{s["agent"]}</span>'
                        summary = f" — `{s['input_summary']}`" if s.get("input_summary") else ""
                        st.markdown(f"{badge_html}{summary} · {s['duration_ms']:.0f} ms", unsafe_allow_html=True)

            # Save to history
            st.session_state.messages.append({
                "role": "assistant",
                "narrative": "".join(narrative_parts),
                "chart_json": chart_json,
                "steps": steps,
            })
