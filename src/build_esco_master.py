"""
Usage:
    python build_esco_master.py --data_dir ./data --out ./esco_master.csv

Input: ESCO CSV files (ESCO dataset - v1.2.1 - classification - en - csv) from https://esco.ec.europa.eu/en/use-esco/download

Output: a single denormalised master table from all ESCO CSV files which models ESCO as:
    Occupation -[:PART_OF_ISCOGROUP]-> ISCOGroup (hierarchy)
    Skill      -[:ESSENTIAL_FOR / :OPTIONAL_FOR]-> Occupation
    Skill      -[:BROADER_THAN]-> SkillGroup (hierarchy)

Flattened to one row per occupation × skill pair, with all hierarchy levels as columns.

columns (83 total):
    Occupation (7)   : occ_uri, occ_label, occ_description, occ_code,
                       occ_isco_code, occ_status, occ_regulated_profession_note
    ISCO hierarchy (8): isco_l1_uri .. isco_l4_uri, isco_l1_label .. isco_l4_label
    Skill-occupation  (2): relation_type  (essential | optional), skill_type_in_occ
    Skill (7)        : skill_uri, skill_label, skill_description, skill_type,
                       skill_reuse_level, skill_status, skill_modified_date
    Thematic flags (3): is_digital, is_transversal, is_digcomp
    Skill hierarchy (12): skill_l0_uri .. skill_l3_uri,
                          skill_l0_label .. skill_l3_label,
                          skill_l0_code .. skill_l3_code
"""

import argparse
import csv
import os
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--data_dir", default=".", help="Folder containing all ESCO CSVs")
parser.add_argument("--out",      default="esco_master.csv", help="Output CSV path")
args = parser.parse_args()

def p(fname):
    return os.path.join(args.data_dir, fname)

# ── 1. LOAD RAW FILES ────────────────────────────────────────────────────────

print("Loading files...")

occ_df     = pd.read_csv(p("occupations_en.csv"),                 encoding="utf-8-sig", dtype=str)
isco_df    = pd.read_csv(p("ISCOGroups_en.csv"),                  encoding="utf-8-sig", dtype=str)
broader_occ= pd.read_csv(p("broaderRelationsOccPillar_en.csv"),   encoding="utf-8-sig", dtype=str)
skills_df  = pd.read_csv(p("skills_en.csv"),                      encoding="utf-8-sig", dtype=str)
sg_df      = pd.read_csv(p("skillGroups_en.csv"),                 encoding="utf-8-sig", dtype=str)
sh_df      = pd.read_csv(p("skillsHierarchy_en.csv"),             encoding="utf-8-sig", dtype=str)
broader_sk = pd.read_csv(p("broaderRelationsSkillPillar_en.csv"), encoding="utf-8-sig", dtype=str)
occ_sk     = pd.read_csv(p("occupationSkillRelations_en.csv"),    encoding="utf-8-sig", dtype=str)

# Thematic collections — used only as URI sets for boolean flags
digital_uris     = set(pd.read_csv(p("digitalSkillsCollection_en.csv"),     encoding="utf-8-sig")["conceptUri"])
transversal_uris = set(pd.read_csv(p("transversalSkillsCollection_en.csv"), encoding="utf-8-sig")["conceptUri"])
digcomp_uris     = set(pd.read_csv(p("digCompSkillsCollection_en.csv"),     encoding="utf-8-sig")["conceptUri"])

print(f"  Occupations: {len(occ_df):,}  |  ISCO groups: {len(isco_df):,}  |  "
      f"Skills: {len(skills_df):,}  |  Occ-skill pairs: {len(occ_sk):,}")


# ── 2. BUILD ISCO HIERARCHY LOOKUP ───────────────────────────────────────────
# Mirrors the gist's PART_OF_ISCOGROUP relationship, but resolved to all 4 levels.

print("Building ISCO hierarchy...")

# Map each ISCO group URI → {code, label}
isco_meta = {
    row["conceptUri"]: {"code": row["code"], "label": row["preferredLabel"]}
    for _, row in isco_df.iterrows()
}

# Map each ISCO group URI → its parent ISCO group URI
isco_parent = {
    row["conceptUri"]: row["broaderUri"]
    for _, row in broader_occ.iterrows()
    if row["broaderType"] == "ISCOGroup" and row["conceptType"] == "ISCOGroup"
}

# Map each ISCO group URI → its ancestor chain, keyed by code length (1=major .. 4=unit)
def isco_ancestors(uri):
    """Walk up the ISCO tree; return dict keyed by level (1-4)."""
    chain, cur = {}, uri
    while cur:
        meta = isco_meta.get(cur, {})
        code = meta.get("code", "")
        level = len(code)          # 1-digit=major, 2=sub-major, 3=minor, 4=unit
        if 1 <= level <= 4:
            chain[level] = {"uri": cur, "code": code, "label": meta.get("label", "")}
        cur = isco_parent.get(cur)
    return chain

# Precompute for all ISCO URIs
isco_chain_cache = {uri: isco_ancestors(uri) for uri in isco_meta}

# Map occupation URI → ISCO unit-group URI
# Occupations that are specialisms (Occupation→Occupation parents) need chain-walking
occ_to_isco_direct = {
    row["conceptUri"]: row["broaderUri"]
    for _, row in broader_occ.iterrows()
    if row["conceptType"] == "Occupation" and row["broaderType"] == "ISCOGroup"
}
occ_to_occ_parent = {
    row["conceptUri"]: row["broaderUri"]
    for _, row in broader_occ.iterrows()
    if row["conceptType"] == "Occupation" and row["broaderType"] == "Occupation"
}

def resolve_isco_uri(occ_uri, _depth=0):
    """Resolve occupation URI → ISCO unit-group URI, following specialism chains."""
    if occ_uri in occ_to_isco_direct:
        return occ_to_isco_direct[occ_uri]
    parent = occ_to_occ_parent.get(occ_uri)
    return resolve_isco_uri(parent, _depth + 1) if parent and _depth < 15 else None

# Build final ISCO lookup DataFrame, one row per occupation
isco_rows = []
for _, row in occ_df.iterrows():
    isco_uri = resolve_isco_uri(row["conceptUri"])
    chain    = isco_chain_cache.get(isco_uri, {}) if isco_uri else {}
    isco_rows.append({
        "conceptUri":    row["conceptUri"],
        "isco_l1_uri":   chain.get(1, {}).get("uri",   ""),
        "isco_l1_label": chain.get(1, {}).get("label", ""),
        "isco_l2_uri":   chain.get(2, {}).get("uri",   ""),
        "isco_l2_label": chain.get(2, {}).get("label", ""),
        "isco_l3_uri":   chain.get(3, {}).get("uri",   ""),
        "isco_l3_label": chain.get(3, {}).get("label", ""),
        "isco_l4_uri":   chain.get(4, {}).get("uri",   ""),
        "isco_l4_label": chain.get(4, {}).get("label", ""),
    })
isco_lookup = pd.DataFrame(isco_rows)


# ── 3. BUILD SKILL GROUP HIERARCHY LOOKUP ────────────────────────────────────
# Mirrors the gist's BROADER_THAN skill chain.
# skillsHierarchy_en.csv already provides the flattened 4-level view (L0-L3),
# so we use it directly rather than walking broaderRelationsSkillPillar ourselves.

print("Building skill group hierarchy...")

# skillsHierarchy maps each deepest-level group row to its full ancestor chain.
# A skill URI may appear as a child of any of L0-L3; we need to find which
# skill group is the *direct* parent of each individual skill.
# Use broaderRelationsSkillPillar for the direct skill→skillgroup edge,
# then look up that group's ancestry in skillsHierarchy.

# Direct parent of each skill (KnowledgeSkillCompetence → SkillGroup)
skill_direct_parent = {
    row["conceptUri"]: row["broaderUri"]
    for _, row in broader_sk.iterrows()
    if row["conceptType"] == "KnowledgeSkillCompetence" and row["broaderType"] == "SkillGroup"
}

# For each SkillGroup URI, find its row in skillsHierarchy (deepest populated level = that group)
# Build a lookup: skillGroup_uri → (l0_uri, l0_label, l0_code, ..., l3_uri, l3_label, l3_code)
def best_sh_match(group_uri, sh_df):
    """Return the skillsHierarchy row where this group appears at the deepest level."""
    for col in ["Level 3 URI", "Level 2 URI", "Level 1 URI", "Level 0 URI"]:
        match = sh_df[sh_df[col] == group_uri]
        if not match.empty:
            return match.iloc[0]
    return None

# Precompute for every skill group that appears in broader relations
unique_skill_groups = set(skill_direct_parent.values())
sg_hierarchy_cache = {}
for sg_uri in unique_skill_groups:
    row = best_sh_match(sg_uri, sh_df)
    if row is not None:
        sg_hierarchy_cache[sg_uri] = {
            "skill_l0_uri":   row.get("Level 0 URI",            ""),
            "skill_l0_label": row.get("Level 0 preferred term", ""),
            "skill_l0_code":  row.get("Level 0 code",           ""),
            "skill_l1_uri":   row.get("Level 1 URI",            ""),
            "skill_l1_label": row.get("Level 1 preferred term", ""),
            "skill_l1_code":  row.get("Level 1 code",           ""),
            "skill_l2_uri":   row.get("Level 2 URI",            ""),
            "skill_l2_label": row.get("Level 2 preferred term", ""),
            "skill_l2_code":  row.get("Level 2 code",           ""),
            "skill_l3_uri":   row.get("Level 3 URI",            ""),
            "skill_l3_label": row.get("Level 3 preferred term", ""),
            "skill_l3_code":  row.get("Level 3 code",           ""),
        }

# Build a skill-level hierarchy lookup DataFrame
sh_rows = []
for _, sk in skills_df.iterrows():
    sg_uri = skill_direct_parent.get(sk["conceptUri"])
    hier   = sg_hierarchy_cache.get(sg_uri, {}) if sg_uri else {}
    sh_rows.append({"conceptUri": sk["conceptUri"], **{
        k: hier.get(k, "") for k in [
            "skill_l0_uri","skill_l0_label","skill_l0_code",
            "skill_l1_uri","skill_l1_label","skill_l1_code",
            "skill_l2_uri","skill_l2_label","skill_l2_code",
            "skill_l3_uri","skill_l3_label","skill_l3_code",
        ]
    }})
skill_hier_lookup = pd.DataFrame(sh_rows)


# ── 4. ASSEMBLE MASTER TABLE ─────────────────────────────────────────────────
# Core: occupationSkillRelations — one row per occupation × skill pair (126,051 rows)
# Then left-join all enrichment tables on their respective URIs.

print("Assembling master table...")

# Keep occupationUri as occ_uri; rename relation/type cols to avoid merge clashes
occ_sk = occ_sk.rename(columns={
    "occupationUri": "occ_uri",
    "relationType":  "relation_type",         # essential | optional  (gist: ESSENTIAL_FOR / OPTIONAL_FOR)
    "skillType":     "skill_type_in_occ",     # skill/competence | knowledge (the type recorded in the relation)
})

# ── Occupation columns
occ_cols = occ_df[[
    "conceptUri","preferredLabel","description",
    "code","iscoGroup","status","regulatedProfessionNote"
]].rename(columns={
    "conceptUri":              "occ_uri",
    "preferredLabel":          "occ_label",
    "description":             "occ_description",
    "code":                    "occ_code",
    "iscoGroup":               "occ_isco_code",
    "status":                  "occ_status",
    "regulatedProfessionNote": "occ_regulated_profession_note",
})

# ── Skill columns
skill_cols = skills_df[[
    "conceptUri","preferredLabel","altLabels","skillType"
]].rename(columns={
    "conceptUri":    "skill_uri",
    "preferredLabel":"skill_preferred_label",
    "altLabels":     "skill_alternative_label",
    "skillType":     "skill_type",
})

# Add thematic flags to skill_cols
skill_cols["is_digital"]     = skill_cols["skill_uri"].isin(digital_uris)
skill_cols["is_transversal"]  = skill_cols["skill_uri"].isin(transversal_uris)
skill_cols["is_digcomp"]      = skill_cols["skill_uri"].isin(digcomp_uris)

# ── Join pipeline
master = (
    occ_sk
    # 1. Join occupation metadata
    .merge(occ_cols,              left_on="occ_uri",        right_on="occ_uri",   how="left")
    # 2. Join ISCO 4-level hierarchy
    .merge(isco_lookup,           left_on="occ_uri",        right_on="conceptUri", how="left")
    # 3. Join skill metadata + thematic flags
    .merge(skill_cols,            left_on="skillUri",       right_on="skill_uri",  how="left")
    # 4. Join skill group 4-level hierarchy
    .merge(skill_hier_lookup,     left_on="skillUri",       right_on="conceptUri", how="left",
           suffixes=("","_sh"))
)

# Drop the redundant join-key columns that pandas adds during merges
drop_cols = [c for c in master.columns if c in {
    "occupationLabel",  # occ_label used instead
    "skillLabel",       # skill_preferred_label used instead
    "skillUri",         # skill_uri used instead (skillUri is the raw col from occ_sk)
    "conceptUri", "conceptUri_sh",  # merge keys
}]
master = master.drop(columns=drop_cols, errors="ignore")

# Final column order: occ → ISCO hierarchy → relation → skill → flags → skill hierarchy
ordered = [
    # Occupation
    "occ_uri","occ_label","occ_description","occ_code",
    "occ_isco_code","occ_status","occ_regulated_profession_note",
    # ISCO hierarchy (gist: PART_OF_ISCOGROUP chain)
    "isco_l1_uri","isco_l1_label",
    "isco_l2_uri","isco_l2_label",
    "isco_l3_uri","isco_l3_label",
    "isco_l4_uri","isco_l4_label",
    # Relation (gist: ESSENTIAL_FOR | OPTIONAL_FOR)
    "relation_type","skill_type_in_occ",
    # Skill
    "skill_uri","skill_preferred_label","skill_alternative_label","skill_type",
    # Thematic flags
    "is_digital","is_transversal","is_digcomp",
    # Skill group hierarchy (gist: BROADER_THAN chain)
    "skill_l0_uri","skill_l0_label","skill_l0_code",
    "skill_l1_uri","skill_l1_label","skill_l1_code",
    "skill_l2_uri","skill_l2_label","skill_l2_code",
    "skill_l3_uri","skill_l3_label","skill_l3_code",
]
# Keep only the ordered columns that exist; append any unexpected extras at the end
extra = [c for c in master.columns if c not in ordered]
master = master[[c for c in ordered if c in master.columns] + extra]

print(f"  Master table: {len(master):,} rows × {len(master.columns)} columns")


# ── 5. SAVE ───────────────────────────────────────────────────────────────────
out_path = args.out
master.to_csv(out_path, index=False, encoding="utf-8")
print(f"  Saved → {out_path}")

# Quick sanity-check summary
print("\nColumn counts by section:")
print(f"  Occupation cols      : {sum(1 for c in master.columns if c.startswith('occ_'))}")
print(f"  ISCO hierarchy cols  : {sum(1 for c in master.columns if c.startswith('isco_'))}")
print(f"  Relation cols        : {sum(1 for c in master.columns if 'relation' in c or c == 'skill_type_in_occ')}")
print(f"  Skill cols           : {sum(1 for c in master.columns if c.startswith('skill_') and 'l0' not in c and 'l1' not in c and 'l2' not in c and 'l3' not in c)}")
print(f"  Thematic flag cols   : {sum(1 for c in master.columns if c.startswith('is_'))}")
print(f"  Skill group hier cols: {sum(1 for c in master.columns if any(c.startswith(f'skill_l{i}') for i in range(4)))}")

print("\nRelation type breakdown:")
print(master["relation_type"].value_counts().to_string())

print("\nDigital / transversal / digcomp flags:")
print(f"  is_digital    : {master['is_digital'].sum():,}")
print(f"  is_transversal: {master['is_transversal'].sum():,}")
print(f"  is_digcomp    : {master['is_digcomp'].sum():,}")
print("\nSample row (first essential relation):")
sample = master[master["relation_type"] == "essential"].iloc[0]
for col in ["occ_label","isco_l1_label","isco_l4_label","relation_type","skill_preferred_label","skill_alternative_label","skill_type","skill_l0_label","skill_l1_label"]:
    print(f"  {col:25s}: {sample.get(col,'')}")
