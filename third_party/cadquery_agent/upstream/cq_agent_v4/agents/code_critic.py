"""
Code Critic agent — verifies generated code logically matches the feature plan.
Catches mismatches AFTER the code executes successfully:
  "Plan says 4 holes but code has 2 pushPoints"
  "Plan says fillet but no .fillet() call in code"
  "Plan says sphere, code creates a box"
"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

SYSTEM = """You are a CadQuery Code Critic.

You compare a FEATURE PLAN against GENERATED CODE to find LOGICAL mismatches.

DO NOT check syntax — code already executed successfully. Only check LOGIC.

CHECK FOR:
- Feature count: "plan says 4 holes, code has 2 pushPoints"
- Missing features: "plan says fillet, no .fillet() call found"
- Wrong dimensions: "plan says 80mm wide, code has box(40, ...)"
- Wrong shape type: "plan says hollow/shell, code is solid"
- Wrong hole type: "plan says countersink, code uses plain .hole()"
- Missing show_object(): last line must be show_object(result)
- Wrong axis: "plan says vertical cylinder, code extrudes on XZ not XY"

DO NOT flag:
- Variable naming style
- Comment presence/absence
- Import ordering
- Minor dimension estimates that weren't specified exactly

RESPOND WITH VALID JSON ONLY (no markdown):
{
  "issues_found": true,
  "issues": [
    "Plan specifies 4 corner holes but code only has pushPoints([(x1,y1),(x2,y2)])",
    "Plan mentions filleted edges but no .fillet() call present"
  ],
  "corrections": [
    "Add corner positions: .pushPoints([(35,15),(-35,15),(35,-15),(-35,-15)]).hole(5)",
    "Add before show_object: result = result.edges('|Z').fillet(2)"
  ],
  "severity": "critical|minor|none"
}

If no issues: {"issues_found": false, "issues": [], "corrections": [], "severity": "none"}"""


def run_code_critic(api_key: str, model: str, plan: str, code: str) -> dict:
    """
    Check if generated code logically matches the feature plan.
    Returns a dict with issues_found, issues, corrections, severity.
    """
    if not plan or not code:
        return {"issues_found": False, "issues": [], "corrections": [], "severity": "none"}

    llm = ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0.0)

    prompt = f"""FEATURE PLAN:
{plan}

GENERATED CODE:
```python
{code}
```

Find logical mismatches between plan and code. Check feature counts, shape type, and dimensions."""

    result = llm.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=prompt)])
    raw = result.content if isinstance(result.content, str) else str(result.content)
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```$", "", raw.strip())

    try:
        data = json.loads(raw.strip())
    except Exception:
        data = {"issues_found": False, "issues": [], "corrections": [], "severity": "none"}

    return data


def format_critic_feedback(data: dict) -> str:
    """Format critic output as text to inject into the coder's prompt."""
    if not data or not data.get("issues_found"):
        return ""
    lines = [
        "=== CODE CRITIC: PLAN vs CODE MISMATCH ===",
        f"Severity: {data.get('severity', 'unknown')}",
        "Issues found:",
    ]
    for issue in data.get("issues", []):
        lines.append(f"  ISSUE: {issue}")
    lines.append("Required corrections:")
    for fix in data.get("corrections", []):
        lines.append(f"  FIX: {fix}")
    lines.append("=== REWRITE CODE TO FIX ALL ISSUES ABOVE ===")
    return "\n".join(lines)
