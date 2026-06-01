# SkillGraph: AI-Powered Career Recommendation Engine

SkillGraph is a knowledge graph-based career recommendation system that helps users evaluate their readiness for a target role, identify skill gaps, and discover personalized learning recommendations.

Built using **Neo4j**, **ESCO skill taxonomy**, and **Streamlit**, the system combines graph reasoning with importance-weighted scoring to provide explainable career guidance.

---

## Project Overview

Traditional career recommendation systems often function as black boxes, recommending roles without explaining the reasoning behind them.

SkillGraph addresses this challenge by modeling relationships between:

- Job Roles
- Skills
- Courses
- Certifications

within a Neo4j Knowledge Graph and using graph traversal techniques to generate transparent, actionable recommendations.

The platform allows users to:

1. Select a target role
2. Self-assess their proficiency across role-specific skills
3. Calculate a role readiness score
4. Identify missing and developing skills
5. Receive personalized course and certification recommendations

---

## System Architecture

![Architecture Diagram](screenshots/architecture.png)

### Workflow

1. ESCO skills and occupation data are processed and loaded into Neo4j.
2. Custom role profiles are created with weighted skill requirements.
3. Users select a target role.
4. The system retrieves the most important skills associated with that role.
5. Users rate their proficiency levels.
6. SkillGraph computes an importance-weighted match score.
7. Missing and underdeveloped skills are identified.
8. Relevant courses and certifications are recommended through graph traversal.

---

## Key Features

### Role Readiness Assessment

Evaluate readiness for a target role using an importance-weighted scoring model.

### Explainable Skill Gap Analysis

Skills are categorized into:

- ✅ Matched Skills
- 📈 Skills to Improve
- ❌ Missing Skills

allowing users to understand exactly where they stand.

### Personalized Learning Recommendations

Recommend courses that directly address missing or underdeveloped skills.

### Certification Guidance

Recommend certifications based on the user's readiness level:

- Foundational
- Associate
- Professional

### Knowledge Graph Reasoning

Leverages graph relationships to connect:

- Roles → Skills
- Skills → Courses
- Roles → Certifications

creating explainable recommendations rather than black-box outputs.

---

## Knowledge Graph Design

### Node Types

| Node | Description |
|--------|------------|
| CustomRole | Target career role |
| EscoSkill | ESCO skill entity |
| Course | Learning resource |
| Certification | Professional certification |

### Relationship Types

| Relationship | Description |
|-------------|-------------|
| HAS_SKILL_METRIC | Role requires a skill |
| TEACHES_SKILL | Course teaches a skill |
| RELEVANT_FOR | Certification relevant for a role |

### Example Graph Structure

```text
(CustomRole)
      |
      | HAS_SKILL_METRIC
      v
 (EscoSkill)
      ^
      |
 TEACHES_SKILL
      |
   (Course)

(CustomRole)
      |
 RELEVANT_FOR
      |
(Certification)
```

## Scoring Methodology

Each role skill contains:

- Skill Importance
- Minimum Required Proficiency
- Favorable Target Proficiency

Candidate ratings are compared against these thresholds.

### Skill Classification
Matched: Candidate proficiency ≥ favorable target

To Improve: Minimum required ≤ candidate proficiency < favorable target

Missing: Candidate proficiency < minimum required

### Overall Match Score

The final score is calculated as an importance-weighted average across all required skills.

This provides a more realistic assessment than treating every skill equally.

## User Experience
### Step 1: Select a Target Role

Users choose the role they want to evaluate themselves against.

### Step 2: Assess Skills

Users rate their proficiency levels for the role's most important skills.

### Step 3: View Match Dashboard

SkillGraph generates:

- Overall Match Score
- Skill Breakdown
- Missing Skills
- Skills to Improve
- Learning Recommendations
- Certification Recommendations


## Neo4j Knowledge Graph

The graph stores relationships between roles, skills, courses, and certifications, enabling explainable recommendation generation through graph traversal.

## Repository Strcuture

```text
skillgraph-ai-career-recommendation/

├── data/
├── notebooks/
│   └── skillgraph_py_code.ipynb
│
├── src/
│   ├── build_esco_master.py
│   ├── skillgraph_ui.py
│   └── neo4j_query_saved_cypher_skillgraph.csv
│
├── screenshots/
│   ├── architecture.png
│   ├── graph_view.png
│   ├── step1_role_selection.png
│   ├── step2_skill_entry.png
│   └── step3_dashboard.png
│
└── README.md
```

## Technology Stack
### Backend
- Python
- Neo4j
- Cypher
### Frontend
- Streamlit
### Data Processing
- Pandas
- ESCO Skills Framework
### Knowledge Representation
- Knowledge Graphs
- Graph Traversal
- Relationship-Based Reasoning


## How to Run
### 1. Install Dependencies

```
pip install -r requirements.txt
```

### 2. Configure Neo4j Credentials

Create:

```
.streamlit/secrets.toml
```

```
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "your-password"
```

### 3. Build and Load the Knowledge Graph

```
python src/build_esco_master.py
```

### 4. Launch the application

```
streamlit run src/skillgraph_ui.py
```

## Results
- Built a Neo4j-based career intelligence platform using knowledge graphs.
- Developed an explainable skill-gap scoring engine.
- Connected role requirements to learning pathways through graph relationships.
- Generated personalized course and certification recommendations.
- Created an interactive Streamlit application for career self-assessment.


## Future Improvements
- Resume-to-role matching
- Automated skill extraction using NLP
- Learning roadmap generation
- LLM-powered career coaching
- Multi-role comparison dashboard
- Interactive graph visualization inside the application
