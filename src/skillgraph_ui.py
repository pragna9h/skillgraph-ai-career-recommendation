import streamlit as st
import streamlit.components.v1 as components
from neo4j import GraphDatabase

# ── Session state defaults ───────────────
for key, default in {
    "step":               1,
    "selected_role_id":   None,
    "selected_role_title": None,
    "skill_rows":         [],
    "candidate_scores":   {},
    "scroll_trigger":     False,  # <-- ADD THIS NEW FLAG
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ─────────────────────────────────────────
# Neo4j connection
# ─────────────────────────────────────────
# Store credentials in .streamlit/secrets.toml:
#   NEO4J_URI      = "bolt://localhost:7687"
#   NEO4J_USER     = "neo4j"
#   NEO4J_PASSWORD = "your-password"

NEO4J_URI      = st.secrets.get("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = st.secrets.get("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = st.secrets.get("NEO4J_PASSWORD", "password")


@st.cache_resource
def get_driver():
    """Single shared Neo4j driver (cached for the lifetime of the app process)."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def run_query(cypher: str, parameters: dict | None = None) -> list[dict]:
    """Execute a read query and return results as a list of dicts."""
    driver = get_driver()
    with driver.session() as session:
        result = session.run(cypher, parameters or {})
        return [record.data() for record in result]


# ─────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────

def score_skill(candidate_value: int, minimum_required: int, favorable_target: int) -> float:
    """
    0      if candidate_value < minimum_required
    1      if candidate_value >= favorable_target
    0.4–1  partial credit between minimum and favorable
    """
    if candidate_value < minimum_required:
        return 0.0
    if candidate_value >= favorable_target:
        return 1.0
    return 0.4 + 0.6 * (
        (candidate_value - minimum_required)
        / (favorable_target - minimum_required + 1e-9)
    )


def compute_overall_score(
    rows: list[dict], candidate_scores: dict
) -> tuple[float, list[dict], list[dict], list[dict]]:
    """
    Returns (overall_pct, fully_matched, to_improve, missing).

    fully_matched : candidate_value >= favorable_target
    to_improve    : minimum_required <= candidate_value < favorable_target
    missing       : candidate_value < minimum_required
    """
    weighted_sum = 0.0
    total_weight = 0.0
    fully_matched: list[dict] = []
    to_improve:    list[dict] = []
    missing:       list[dict] = []

    for row in rows:
        uri        = row["esco_skill_uri"]
        cand       = candidate_scores.get(uri, 0)
        min_req    = int(row["minimum_required"])
        fav        = int(row["favorable_target"])
        importance = float(row["skill_importance"])

        s             = score_skill(cand, min_req, fav)
        weighted_sum += s * importance
        total_weight += importance

        if cand < min_req:
            missing.append({**row, "candidate_value": cand})
        elif cand < fav:
            to_improve.append({**row, "candidate_value": cand})
        else:
            fully_matched.append({**row, "candidate_value": cand})

    overall_pct = 100.0 * (weighted_sum / total_weight if total_weight else 0.0)
    return overall_pct, fully_matched, to_improve, missing


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────

PROFICIENCY_LABELS = {
    0: "Not present",
    1: "Awareness",
    2: "Working",
    3: "Competent",
    4: "Strong",
    5: "Expert",
}


def score_color(score: float) -> str:
    if score >= 80:
        return "#10b981"
    if score >= 60:
        return "#f59e0b"
    return "#ef4444"


# def scroll_to_top() -> None:
#     js = """
#     <script>
#         var body = window.parent.document.querySelector(".main");
#         if (body) body.scrollTop = 0;
#     </script>
#     """
#     components.html(js, height=0)
def scroll_to_top() -> None:
    js = """
    <script>
        // Wait 150ms for Streamlit to finish rendering the DOM
        setTimeout(function() {
            var appView = window.parent.document.querySelector('[data-testid="stAppViewContainer"]');
            var main = window.parent.document.querySelector('.main');
            
            if (appView) appView.scrollTop = 0;
            if (main) main.scrollTop = 0;
            window.parent.scrollTo(0, 0);
        }, 150); 
    </script>
    """
    components.html(js, height=0)

# ─────────────────────────────────────────
# Cached DB fetchers
# ─────────────────────────────────────────

@st.cache_data(ttl=300)
def fetch_all_roles() -> list[dict]:
    """Step 1 – all CustomRole nodes, sorted by title."""
    return run_query("""
        MATCH (r:CustomRole)
        RETURN r.role_id    AS role_id,
               r.role_title AS role_title
        ORDER BY r.role_title
    """)


@st.cache_data(ttl=300)
def fetch_skills_for_role(role_id: str) -> list[dict]:
    """Step 2 – top-20 skills for a given role, capped at 15."""
    return run_query(
        """
        MATCH (r:CustomRole {role_id: $role_id})-[rel:HAS_SKILL_METRIC]->(s:EscoSkill)
        WHERE rel.is_top20 = "Y"
        RETURN
            r.role_id           AS role_id,
            r.role_title        AS role_title,
            s.esco_skill_uri    AS esco_skill_uri,
            s.label             AS skill_label,
            rel.relation_type   AS relation_type,
            rel.skill_importance AS skill_importance,
            rel.minimum_required AS minimum_required,
            rel.favorable_target AS favorable_target,
            rel.skill_rank      AS skill_rank
        ORDER BY rel.skill_rank
        LIMIT 15
        """,
        {"role_id": role_id},
    )


def fetch_courses_for_missing(
    missing_rows: list[dict],
    to_improve_rows: list[dict],
    limit: int = 3,
) -> list[dict]:
    """
    Step 3 – recommend courses ranked by how much importance-weight they address.

    Each skill is assigned a query-time importance weight:
      - missing skills  → full skill_importance   (candidate is below minimum)
      - to_improve skills → 50% of skill_importance (candidate meets minimum but not target)

    The Cypher UNWINDs these weighted entries and sums importance across all skills a
    course teaches, producing a relevance_score.  Level difficulty is a tiebreaker so
    that, when two courses are equally relevant, the more accessible one surfaces first.
    covered_skills is collected so the UI can tell the user exactly which gaps each
    course addresses.
    """
    if not missing_rows and not to_improve_rows:
        return []

    # Build priority list: missing at full weight, to_improve at half weight.
    # Sort each group by importance descending so the order is deterministic.
    skill_priorities: list[dict] = []
    seen: set[str] = set()

    for row in sorted(missing_rows, key=lambda r: float(r["skill_importance"]), reverse=True):
        uri = row["esco_skill_uri"]
        if uri not in seen:
            skill_priorities.append({"uri": uri, "importance": float(row["skill_importance"])})
            seen.add(uri)

    for row in sorted(to_improve_rows, key=lambda r: float(r["skill_importance"]), reverse=True):
        uri = row["esco_skill_uri"]
        if uri not in seen:
            skill_priorities.append({"uri": uri, "importance": float(row["skill_importance"]) * 0.5})
            seen.add(uri)

    return run_query(
        """
        UNWIND $skill_priorities AS sp
        MATCH (c:Course)-[:TEACHES_SKILL]->(s:EscoSkill {esco_skill_uri: sp.uri})
        WITH c,
             SUM(sp.importance)  AS relevance_score,
             COLLECT(s.label)    AS covered_skills
        ORDER BY
            relevance_score DESC,
            CASE c.level
                WHEN 'beginner'     THEN 1
                WHEN 'intermediate' THEN 2
                WHEN 'advanced'     THEN 3
                ELSE 4
            END,
            c.title
        LIMIT $limit
        RETURN
            c.title        AS course_title,
            c.url          AS course_url,
            c.level        AS level,
            c.program_type AS program_type,
            c.provider     AS provider,
            relevance_score,
            covered_skills
        """,
        {"skill_priorities": skill_priorities, "limit": limit},
    )


def _preferred_cert_tier(overall_pct: float) -> str:
    """
    Map overall match score to the certification tier that best fits the candidate:
      < 40%  → foundational  (fill big gaps first)
      40–69% → associate     (build on partial competency)
      ≥ 70%  → professional  (validate near-complete readiness)
    """
    if overall_pct < 40:
        return "foundational"
    if overall_pct < 70:
        return "associate"
    return "professional"


def fetch_certs_for_role(
    role_id: str,
    overall_pct: float,
    missing_rows: list[dict],
    limit: int = 3,
) -> list[dict]:
    """
    Step 3 – recommend certifications adapted to where the candidate actually stands.

    preferred_tier is derived from overall_pct and surfaced first via a Cypher CASE
    boost so that, e.g., a candidate scoring 35% sees foundational certs rather than
    professional ones.  Within each tier the standard foundational→associate→professional
    ordering still applies as a secondary sort, so the full list remains coherent.

    A context_note is returned per cert so the UI can explain *why* it was surfaced.
    """
    preferred_tier = _preferred_cert_tier(overall_pct)

    # Build a short plain-language note that explains the recommendation context.
    # Attached in Python (not Cypher) because it depends on runtime candidate state.
    has_critical_gaps = any(
        float(r["skill_importance"]) >= 0.15 for r in missing_rows
    )

    results = run_query(
        """
        MATCH (r:CustomRole {role_id: $role_id})-[:RELEVANT_FOR]->(c:Certification)
        RETURN
            c.cert_key         AS cert_key,
            c.name             AS cert_name,
            c.tier             AS tier,
            c.url              AS url,
            c.page_title       AS page_title,
            c.meta_description AS meta_description
        ORDER BY
            CASE $preferred_tier
                WHEN 'professional' THEN
                    CASE c.tier 
                        WHEN 'professional' THEN 1 
                        WHEN 'associate'    THEN 2 
                        WHEN 'foundational' THEN 3 
                        ELSE 4 
                    END
                WHEN 'associate' THEN
                    CASE c.tier 
                        WHEN 'associate'    THEN 1 
                        WHEN 'foundational' THEN 2 
                        WHEN 'professional' THEN 3 
                        ELSE 4 
                    END
                ELSE /* Defaults to 'foundational' */
                    CASE c.tier 
                        WHEN 'foundational' THEN 1 
                        WHEN 'associate'    THEN 2 
                        WHEN 'professional' THEN 3 
                        ELSE 4 
                    END
            END,
            c.name
        LIMIT $limit
        """,
        {"role_id": role_id, "preferred_tier": preferred_tier, "limit": limit},
    )

    # Annotate each result with a context note derived from the candidate's profile.
    # tier_notes = {
    #     "foundational": (
    #         "Recommended to build foundational credibility before tackling gaps."
    #         if has_critical_gaps
    #         else "Good starting point to formalise your current knowledge."
    #     ),
    #     "associate":    "Validates your working-level competency and supports gap closure.",
    #     "professional": "Demonstrates near-complete readiness for this role.",
    # }

    for row in results:
        tier = row.get("tier", "")
        # row["context_note"] = tier_notes.get(tier, "Relevant certification for this role.")

    return results

# ─────────────────────────────────────────
# App layout
# ─────────────────────────────────────────

st.set_page_config(page_title="SkillGraph", layout="centered")

# Hide default Streamlit headers and footers for a standalone app feel
hide_st_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {background-color: transparent;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

st.title("SkillGraph")
st.caption("3 Easy Steps: Choose A Role → Rate Your Skills → View Your Match Dashboard")

# ── Session state defaults ───────────────
for key, default in {
    "step":               1,
    "selected_role_id":   None,
    "selected_role_title": None,
    "skill_rows":         [],
    "candidate_scores":   {},
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ── Sidebar ──────────────────────────────
with st.sidebar:
    st.header("Controls")
    st.write(f"Current step: **{st.session_state.step} / 3**")
    if st.button("Reset & start over", use_container_width=True):
        for key in ["step", "selected_role_id", "selected_role_title",
                    "skill_rows", "candidate_scores"]:
            st.session_state.pop(key, None)
        # scroll_to_top()
        st.rerun()

# ── Step progress indicator ──────────────
with st.container(border=True):
    step_labels = ["1 · Role", "2 · Skills", "3 · Dashboard"]
    cols = st.columns(3)
    for i, (col, label) in enumerate(zip(cols, step_labels)):
        with col:
            if st.session_state.step == i + 1:
                st.markdown(f"**:blue[{label}]**")
            elif st.session_state.step > i + 1:
                st.markdown(f"{label} ✓")
            else:
                st.markdown(f"_{label}_")

st.write("") # Spacer

# ── Check for scroll trigger ──────────────
if st.session_state.scroll_trigger:
    scroll_to_top()
    st.session_state.scroll_trigger = False # Reset it immediately so it only fires once

# ═══════════════════════════════════════════
# STEP 1 — Role selection
# ═══════════════════════════════════════════
if st.session_state.step == 1:
    with st.container(border=True):
        st.subheader("Step 1 · Choose your target role")
        st.write("Select the role you want to assess yourself against.")

        roles = fetch_all_roles()
        if not roles:
            st.error(
                "Could not load roles from the database. "
                "Check your Neo4j connection settings in `.streamlit/secrets.toml`."
            )
            st.stop()

        role_titles      = [r["role_title"] for r in roles]
        role_id_by_title = {r["role_title"]: r["role_id"] for r in roles}

        selected_title = st.selectbox(
            "Role", 
            role_titles, 
            index=None, 
            placeholder="Start typing to search for a role...",
            label_visibility="collapsed"
        )

        st.write("")
        if st.button("Next: Enter your skills →", type="primary"):
            # CHANGE: Ensure a role is actually selected before proceeding
            if selected_title:
                st.session_state.selected_role_id    = role_id_by_title[selected_title]
                st.session_state.selected_role_title = selected_title
                st.session_state.candidate_scores    = {}   
                st.session_state.skill_rows          = []
                # scroll_to_top()
                st.session_state.scroll_trigger = True
                st.session_state.step = 2
                st.rerun()
            else:
                st.warning("Please select a role from the list before continuing.")

# ═══════════════════════════════════════════
# STEP 2 — Skill self-assessment
# ═══════════════════════════════════════════
elif st.session_state.step == 2:
    role_id    = st.session_state.selected_role_id
    role_title = st.session_state.selected_role_title

    st.subheader(f"Your skills for: {role_title}")
    st.write("Set your proficiency level for each skill.  \n**0 = Not present** · **1 = Awareness** · **2 = Working** · **3 = Competent** · **4 = Strong** · **5 = Expert**")

    skill_rows = fetch_skills_for_role(role_id)
    st.session_state.skill_rows = skill_rows

    if not skill_rows:
        st.warning("No skills found for this role. Go back and select another role.")
    else:
        candidate_scores: dict[str, int] = {}

        # Wrap each skill in its own card to mimic a feed list
        for row in skill_rows:
            with st.container(border=True):
                uri      = row["esco_skill_uri"]
                label    = row['skill_label'].split('(')[0].strip().capitalize()
                rel_type = row.get("relation_type", "")
                rank     = int(row["skill_rank"])
                min_req  = int(row["minimum_required"])
                fav      = int(row["favorable_target"])

                prev_val = st.session_state.candidate_scores.get(uri, 0)

                col_meta, col_input = st.columns([2, 3])
                with col_meta:
                    st.markdown(f"**{label}**")
                    # st.caption(f"Rank #{rank}  ·  {rel_type}")
                    # st.caption(f"Min: {min_req}/5  ·  Target: {fav}/5")
                with col_input:
                    level = st.slider(
                        label,
                        min_value=0,
                        max_value=5,
                        value=prev_val,
                        key=f"slider_{uri}",
                        label_visibility="collapsed",
                    )
                    st.caption(PROFICIENCY_LABELS.get(level, ""))

                candidate_scores[uri] = level

        st.write("")
        col_back, col_next = st.columns(2)
        with col_back:
            if st.button("← Back to role selection", use_container_width=True):
                scroll_to_top()
                st.session_state.step = 1
                st.rerun()
        with col_next:
            if st.button("Next: View dashboard →", type="primary", use_container_width=True):
                st.session_state.candidate_scores = candidate_scores
                # scroll_to_top()
                st.session_state.scroll_trigger = True
                st.session_state.step = 3
                st.rerun()

# ═══════════════════════════════════════════
# STEP 3 — Match dashboard
# ═══════════════════════════════════════════
elif st.session_state.step == 3:
    role_id          = st.session_state.selected_role_id
    role_title       = st.session_state.selected_role_title
    skill_rows       = st.session_state.skill_rows
    candidate_scores = st.session_state.candidate_scores

    if not skill_rows:
        st.warning("No skill data available. Please go back to Step 2.")
    else:
        overall_pct, fully_matched, to_improve, missing = compute_overall_score(
            skill_rows, candidate_scores
        )

        # Profile Hero Card
        with st.container(border=True):
            st.subheader(f"Match Profile: {role_title}")
            st.markdown(
                f"<h1 style='margin-top:0; color:{score_color(overall_pct)};'>"
                f"{overall_pct:.1f}%</h1>",
                unsafe_allow_html=True,
            )
            st.write("**Overall match score (importance-weighted)**")
            
            st.divider()
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("✅ Matched",  len(fully_matched))
            col_b.metric("📈 To improve",     len(to_improve))
            col_c.metric("❌ Missing",         len(missing))

        # Skills Breakdown Card
        with st.container(border=True):
            st.subheader("Skill Breakdown")

            if fully_matched:
                st.markdown("#### ✅ Matched")
                for row in fully_matched:
                    st.write(f"- **{row['skill_label'].split('(')[0].strip()}** — {row['candidate_value']}/5")

            if to_improve:
                st.markdown("#### 📈 To improve")
                for row in to_improve:
                    st.write(f"- **{row['skill_label'].split('(')[0].strip()}** — currently {row['candidate_value']}/5, target {int(row['favorable_target'])}/5")

            if missing:
                st.markdown("#### ❌ Missing")
                for row in missing:
                    st.write(f"- **{row['skill_label'].split('(')[0].strip()}** — minimum required: {int(row['minimum_required'])}/5")

        # Learning & Certifications Card
        with st.container(border=True):
            st.subheader("Recommended Learning Path")
            
            if missing or to_improve:
                st.markdown("#### 📚 Courses")
                courses = fetch_courses_for_missing(missing, to_improve)
                if courses:
                    for c in courses:
                        title          = c.get("course_title", "—")
                        url            = c.get("course_url", "")
                        provider       = c.get("provider", "")
                        level          = str(c.get("level", "")).capitalize().strip()
                        prog           = c.get("program_type", "").replace('_', ' ').capitalize().strip()
                        covered        = c.get("covered_skills", [])
                        link           = f"[{title}]({url})" if url else f"**{title}**"
                        meta           = "  · ".join(filter(None, [provider, level, prog]))
                        covered_text   = ", ".join(covered) if covered else "—"
                        st.write(f"- {link}  \n  ·  {meta}")
                else:
                    st.info("No courses found in the database for your current gaps.")
            else:
                st.success("No skill gaps — no course recommendations needed.")

            st.divider()
            
            preferred_tier = _preferred_cert_tier(overall_pct)
            st.markdown(f"#### 🏅 Certifications (Prioritizing **{preferred_tier}** tier)")
            certs = fetch_certs_for_role(role_id, overall_pct, missing)
            if certs:
                for c in certs:
                    name    = c.get("cert_name", "—")
                    url     = c.get("url", "")
                    tier    = "Level: " + c.get("tier", "").capitalize()
                    desc    = c.get("meta_description", "").split('.')[0]
                    # note    = c.get("context_note", "")
                    link    = f"[{name}]({url})" if url else f"**{name}**"
                    st.write(f"- {link}  \n·  {tier}  \n {desc}.")
            else:
                st.info("No certifications found for this role.")

    st.write("")
    scroll_to_top()
    if st.button("← Back to skill entry", use_container_width=True):
        scroll_to_top()
        st.session_state.step = 2
        st.rerun()

    # scroll_to_top()
