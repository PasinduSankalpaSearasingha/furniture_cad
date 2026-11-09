"""
Skill Selector agent — reads the full feature plan and picks the best 1-3 skill templates.
Smarter than keyword search: understands the plan holistically, not just word matching.
"""

import json
import os
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI


def run_skill_selector(api_key: str, model: str, plan: str,
                        skill_registry_path: str) -> list:
    """
    Read the plan + skill registry, pick the best 1-3 matching skill templates.
    Returns list of {"name": str, "reason": str, "priority": int}.
    """
    if not plan:
        return []

    try:
        with open(skill_registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
    except Exception:
        return []

    # Compact skill list — one line per skill so it fits in context
    skill_lines = [f"  {e['name']}: {e['description']}" for e in registry]
    skills_block = "\n".join(skill_lines)

    SYSTEM = f"""You are a CadQuery Skill Selector.

You read a FEATURE PLAN and pick 1-3 skill templates that best match it.

Think about:
  - What is the PRIMARY shape? → that's priority 1
  - What secondary features can use a template? → priority 2, 3
  - Only recommend skills that are genuinely relevant — do NOT list "close enough" options

AVAILABLE SKILLS (71 total):
{skills_block}

RESPOND WITH VALID JSON ONLY (no markdown):
{{
  "selected": [
    {{
      "name": "bracket",
      "reason": "Plan describes an L-shaped bracket — this matches the primary shape",
      "priority": 1
    }},
    {{
      "name": "counterbore_hole",
      "reason": "Plan specifies M4 mounting holes with counterbore — use this template",
      "priority": 2
    }}
  ]
}}

If no skill matches well: {{"selected": []}}
Maximum 3 skills. Be selective — wrong skill recommendation is worse than none."""

    llm = ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0.0)

    result = llm.invoke([
        SystemMessage(content=SYSTEM),
        HumanMessage(content=f"FEATURE PLAN:\n{plan}\n\nSelect the best matching skill templates."),
    ])

    raw = result.content if isinstance(result.content, str) else str(result.content)
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```$", "", raw.strip())

    try:
        data = json.loads(raw.strip())
        return data.get("selected", [])
    except Exception:
        return []


def format_skill_recommendations(recommendations: list) -> str:
    """Format recommendations as a text block for the coder prompt."""
    if not recommendations:
        return ""
    lines = [
        "=== SKILL SELECTOR: RECOMMENDED TEMPLATES ===",
        "Start your code from these templates (in priority order):",
    ]
    for rec in sorted(recommendations, key=lambda x: x.get("priority", 9)):
        lines.append(f"  [{rec.get('priority', '?')}] use_skill('{rec['name']}', ...) — {rec['reason']}")
    lines.append("Call use_skill() for the priority-1 skill first, then extend/modify.")
    lines.append("=== USE THE RECOMMENDED SKILL AS YOUR STARTING POINT ===")
    return "\n".join(lines)
