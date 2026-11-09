# CadQuery AI Agent v4

A full agentic AI that lives inside [CQ-Editor](https://github.com/CadQuery/CQ-editor) and generates 3D CAD code from plain English descriptions or reference images.

**You type or attach an image** → 10 AI agents plan, code, check, render, review, and self-correct → **Working CadQuery code loaded into your editor**

---

## What It Does

```
You → [Memory] → [Dims] → [Assembly?] → [Plan] → [Skills] → [Code] → [Critic]
    → [Repair?] → [Render] → [Dim Check] → [Review] → [Approve] → [Save] → CQ-Editor
```

Ten AI agents collaborate across 13 pipeline stages. You only step in at the final approval.

---

## Key Features

- **Image input** — attach a reference photo and the AI reproduces the exact shape
- **Dimension extraction** — reads proportions from the image OR infers sizes from your text
- **Assembly detection** — recognises multi-part inputs and plans each part separately
- **Skill-aware coding** — picks the best template from 71 pre-built shapes before writing code
- **Code critic** — checks that code logically matches the plan before rendering
- **Repair specialist** — deep error expert with CadQuery traceback pattern library
- **Dimension validator** — compares rendered SVG proportions against the reference image
- **Visual review** — renders 3 SVG projections and AI inspects them visually
- **Human approval gate** — you approve, recreate, or cancel before code is loaded
- **Memory curator** — deduplicates memory, generates better tags, decides save/update/skip
- **Long-term memory** — remembers shapes across sessions, recalls similar ones automatically
- **Fast mode** — toggle off visual pipeline for instant results on simple shapes

---

## All 10 Agents

| # | Agent | Solves |
|---|-------|--------|
| 1 | Dimension Extractor | AI guessing dimensions blindly — extracts mm from image OR text |
| 2 | Assembly Decomposer | Multi-part images treated as one — splits into per-part plans |
| 3 | Planner | Unstructured code without a plan — produces an exact feature contract |
| 4 | Skill Selector | Coder picking wrong template — reads all 71 skills, picks best match |
| 5 | Coder | Core generator — writes + self-validates CadQuery code (5 retries) |
| 6 | Code Critic | Code that runs but mismatches the plan — e.g. "plan says 4 holes, code has 2" |
| 7 | Repair Specialist | Hard errors after all retries — error pattern library for CadQuery failures |
| 8 | Dimension Validator | Shape looks right but wrong proportions — compares SVG ratios vs image |
| 9 | Reviewer | No visual QC — sees rendered SVG, approves or rejects with specifics |
| 10 | Memory Curator | Duplicate memory — dedup check + better tags before saving |

---

## Full Pipeline

```
         YOUR REQUEST + IMAGE
                  │
          [1] MEMORY RECALL         ← search past similar shapes
                  │
          [2] DIMENSION EXTRACTOR   ← extract mm from image proportions OR text
                  │
          [3] ASSEMBLY DECOMPOSER   ← single part or multi-part?
                  │
          [4] PLANNER AGENT         ← exact feature plan from image + dims + memory
                  │
          [5] SKILL SELECTOR        ← pick best 1-3 templates from 71 skills
                  │
          [6] CODER AGENT ◄─────────── sees image + plan + skills + dims + critique
          (5 retries until SUCCESS)
                  │
          [7] CODE CRITIC           ← "plan says fillet, code has no fillet call"
                  │              └── issues found → back to Coder (max 1×)
          [8] REPAIR SPECIALIST     ← only if Coder exhausts all 5 retries
                  │
          [9] RENDER                ← isometric, front, top SVG projections
                  │
          [10] DIMENSION VALIDATOR  ← compare SVG width:height vs reference image
                  │
          [11] REVIEWER AGENT       ← visual comparison rendered SVG vs image
                  │
      APPROVED ───┤─── REJECTED (loops back to Coder, max 2×)
                  │
      [12] HUMAN APPROVAL GATE
           [Approve & Load] [Recreate] [Cancel]
                  │
          [13] MEMORY CURATOR       ← SAVE_NEW / UPDATE / SKIP + better tags
                  │
          CODE IN CQ-EDITOR
```

Full documentation: [docs/v4_agentic_workflow.md](docs/v4_agentic_workflow.md)

---

## Project Structure

```
cadquery-ai-agent/
├── docs/
│   └── v4_agentic_workflow.md       Full 13-stage pipeline documentation
├── skills/                          71 shape templates
│   ├── skill_registry.json          Index with name + description for all 71 skills
│   ├── features/                    bracket, gear, pocket, shaft, sweep, splines...
│   ├── assemblies/                  bolt, nut, washer, MAssembly constrained
│   ├── import_export/               STEP, STL, SVG, DXF
│   └── generic/                     fallback template
├── cq_agent_v4/                     Full agentic pipeline
│   ├── state.py                     Pipeline state (20+ fields)
│   ├── graph.py                     LangGraph StateGraph — 13 nodes
│   ├── widget.py                    CQ-Editor panel with approval UI
│   ├── agents/
│   │   ├── dimension_extractor.py   Agent 1 — dimensions from image OR text
│   │   ├── assembly_decomposer.py   Agent 2 — multi-part detection
│   │   ├── planner.py               Agent 3 — feature plan
│   │   ├── skill_selector.py        Agent 4 — template selection
│   │   ├── coder.py                 Agent 5 — code writer + self-validator
│   │   ├── code_critic.py           Agent 6 — plan vs code logic check
│   │   ├── repair_specialist.py     Agent 7 — error pattern library
│   │   ├── dimension_validator.py   Agent 8 — proportion comparison
│   │   ├── reviewer.py              Agent 9 — visual SVG inspection
│   │   └── memory_curator.py        Agent 10 — memory quality control
│   ├── tools/
│   │   ├── cadquery_tools.py        execute_cadquery, render_shape
│   │   └── skill_tools.py           search_skills, list_skills, use_skill, plan_shape
│   └── memory/
│       ├── store.py                 recall_similar(), save_shape(), update_shape()
│       └── shapes_memory.json       persistent shape database (up to 100 entries)
└── cq_editor_integration/           Drop-in files for CQ-Editor
    ├── README.md
    └── widgets/
        └── ai_chat_agent_v4.py
```

---

## Setup

### 1. Install dependencies

```bash
pip install langchain-google-genai langgraph langchain-core
```

### 2. Get a Google API key

Get one free at [aistudio.google.com](https://aistudio.google.com)

Copy `.env.example` → `.env` and fill in your key:

```
GOOGLE_API_KEY=your_key_here
MODEL=gemini-2.0-flash
```

Recommended models: `gemini-2.5-pro` (best quality) · `gemini-2.0-flash` (fastest, free tier)

### 3. Install the CQ-Editor widget

```
cq_editor_integration/widgets/ai_chat_agent_v4.py
  → CQ-editor/cq_editor/widgets/ai_chat_agent_v4.py
```

See `cq_editor_integration/README.md` for the 3-line edit to `main_window.py`.

### 4. Configure in CQ-Editor

Open the **AI Agent v4** panel → Preferences:

| Setting | Description |
|---------|-------------|
| Google API Key | Auto-loads from `.env` if present |
| Model | e.g. `gemini-2.0-flash` |
| Agent Dir | Full path to the `CQ-editor` root folder |
| Visual Review | ON = full 13-stage pipeline · OFF = fast mode (skip render/review/approval) |

---

## Fast Mode vs Full Mode

| Stage | Full Mode | Fast Mode |
|-------|-----------|-----------|
| Memory Recall | ✅ | ✅ |
| Dimension Extractor | ✅ | ✅ |
| Assembly Decomposer | ✅ | ✅ |
| Planner | ✅ | ✅ |
| Skill Selector | ✅ | ✅ |
| Coder (5 retries) | ✅ | ✅ |
| Code Critic | ✅ | ✅ |
| Repair Specialist | ✅ if needed | ✅ if needed |
| Render | ✅ | ❌ skipped |
| Dimension Validator | ✅ | ❌ skipped |
| Reviewer | ✅ | ❌ skipped |
| Human Approval | ✅ | ❌ auto-loaded |
| Memory Curator + Save | ✅ | ✅ |
| Typical time | ~60–120 sec | ~15–30 sec |

---

## Skill Library (71 skills)

**Basic shapes:** sphere · cone · frustum · torus · hex prism · ring · pin · ellipse · wedge · partial cylinder

**Features:** bracket · enclosure · pocket · stepped shaft · shell · revolve · text on face · helix · involute gear · sweep · ISO thread · grid holes · polar holes · counterbore hole · countersink hole · multi-section loft

**Structural:** rib · boss · snap fit · T-slot extrusion · U/C/L channel · living hinge

**Mechanical:** pulley · pipe flange · hook · handle · cable clip

**Splines:** spline path sweep · spline profile (teardrop/leaf/blob/star) · spline surface (vase/hourglass) · NACA airfoil · spline transition (round→square)

**2D Primitives:** ellipse · wedge · partial cylinder · bezier profile · slot plate · offset profile (frame/gasket)

**Arrays & Patterns:** rectangular array · polar array · mirror body · angled feature

**Compound Shapes:** lidded box · boolean intersection

**Gears (cq_gears):** helical gear · bevel gear · ring gear · worm gear set · rack gear · herringbone gear · planetary gear set

**Assemblies:** MAssembly constrained · bolt · nut · washer · bolt+nut · flange bolt pattern · plate with bolts

**Electronics:** PCB standoff · heat sink

**Import/Export:** STEP import · STEP/STL/SVG export · DXF import

---

## Agent Tools

| Tool | Used by | Purpose |
|------|---------|---------|
| `plan_shape` | Planner | Commits to feature list — the contract the coder must honour |
| `search_skills` | Coder | Keyword search over the skill library |
| `list_skills` | Coder | Shows all 71 available templates |
| `use_skill` | Coder | Renders a template with custom parameters |
| `execute_cadquery` | Coder, Repair | Runs code headlessly — returns `SUCCESS` or full traceback |
| `render_shape` | Graph | Exports 3 SVG projections for visual review |

---

## Requirements

- Python 3.10+
- CadQuery 2.4+
- CQ-Editor (bundled Python 3.12)
- `langchain-google-genai`, `langgraph`, `langchain-core`
- Google API key (free tier works for `gemini-2.0-flash`)
- PyQt5 (bundled with CQ-Editor — used for SVG rendering)

Optional: `cq-warehouse` — needed for thread, bolt, nut, and washer skills  
Optional: `cq_gears` — needed for helical, bevel, ring, worm, rack, herringbone, planetary gear skills

---

## License

MIT
