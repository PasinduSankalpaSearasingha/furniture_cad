"""
Repair Specialist agent — CadQuery error expert activated when the coder fails 3+ times.
Has deep knowledge of common CadQuery error patterns and their root causes.
"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

SYSTEM = """You are a CadQuery Repair Specialist — expert at fixing hard CadQuery errors.

You receive code that FAILED to execute after multiple retries, the full error, and the original plan.

CADQUERY ERROR PATTERN LIBRARY:

"StdFail_NotDone" | "BRep_Builder" | "OCC":
  Root: Shape construction failed at kernel level.
  Fixes:
  - Sweep: profile too large for path curvature → reduce profile size by 50%
  - Loft: wire vertex counts must match → use same edge count on all wires
  - Boolean on bad geometry → add .clean() before boolean operations
  - Try replacing with simpler alternative (extrude+fillet instead of complex sweep)

"NullPointerException" | "AttributeError: 'NoneType'" | "object has no attribute":
  Root: Selection returned nothing (empty workplane).
  Fixes:
  - .faces(">Z") wrong direction → try "<Z", ">Y", "<Y", ">X", "<X"
  - .edges("|Z") on rounded shape → use .edges() or .edges("#Z")
  - Chain broke → split into variables and check each step individually
  - Wrong selector string → use positional: .faces()[0], .faces()[-1]

"Cannot add edge to wire" | "Wire is not closed":
  Root: Sketch curves don't connect at endpoints.
  Fixes:
  - Ensure all curve endpoints meet exactly (no floating point gaps)
  - Use .close() at end of sketch chain
  - Replace open sketch with rect() or circle() which are always closed

"Fillet failed" | "BRepFilletAPI" | "Edge not found":
  Root: Fillet radius exceeds edge geometry.
  Fixes:
  - Reduce fillet radius to < half the smallest wall thickness
  - Use try/except: try .fillet(r) except: .fillet(r * 0.3)
  - Apply fillet to specific edges: .edges("|Z").fillet(r) not .edges().fillet(r)
  - For very thin parts: skip fillet entirely

"could not find a face" | "ValueError" | "IndexError: list index":
  Root: Selector string returned empty, or list access out of bounds.
  Fixes:
  - Print-debug: wrap in try/except, catch ValueError
  - Use .val() and check is not None before chaining
  - Replace string selector with positional: .faces()[N]

"Shape is null" | "TopoDS_Shape is null":
  Root: Boolean operation (cut/union/intersect) produced empty solid.
  Fixes:
  - Shapes don't overlap → translate one shape to overlap region before boolean
  - Shape is inverted → use .union() instead of .cut() or vice versa
  - Use .add() for union instead of .union() for workplane-based shapes

"ImportError" | "ModuleNotFoundError":
  Root: External library not available.
  Fixes:
  - cq_gears → replace with manual involute gear via cq.Workplane polygon approximation
  - cq_warehouse → replace with manual ISO thread using helix + trapezoid profile sweep
  - Always use only: cadquery, math, numpy (if truly needed)

"ZeroDivisionError" | "math domain error":
  Root: Division by zero or invalid math input.
  Fixes:
  - Check that all denominators are non-zero before division
  - Wrap math.sqrt, math.log in max(x, 1e-10) guards
  - Ensure radius/size parameters are positive

YOUR JOB:
1. Identify the ROOT CAUSE from the exact error message
2. Apply the appropriate fix from the pattern library
3. If pattern not recognized: try a simpler alternative implementation
4. Fixed code MUST still implement the original feature plan

RESPOND WITH VALID JSON ONLY (no markdown):
{
  "root_cause": "Fillet radius 5mm exceeds wall thickness of 3mm",
  "fix_applied": "Reduced fillet to 1mm, applied only to vertical edges with '|Z' filter",
  "fixed_code": "import cadquery as cq\\n\\nresult = (cq.Workplane('XY')\\n  .box(80, 40, 3)\\n  .edges('|Z').fillet(1)\\n)\\nshow_object(result)"
}"""


def run_repair_specialist(api_key: str, model: str,
                           code: str, error: str, plan: str,
                           previous_repair_attempts: int = 0) -> dict:
    """
    Attempt deep repair of failing CadQuery code.
    Returns {"fixed_code": str, "root_cause": str, "fix_applied": str}.
    """
    if not error:
        return {"fixed_code": code, "root_cause": "Unknown error", "fix_applied": "No change"}

    llm = ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0.2)

    retry_note = ""
    if previous_repair_attempts > 0:
        retry_note = (f"\n\nNOTE: {previous_repair_attempts} previous repair attempt(s) also failed. "
                      "Try a fundamentally DIFFERENT implementation approach while preserving all features from the plan.")

    prompt = f"""FEATURE PLAN (what the shape must be):
{plan}

FAILING CODE:
```python
{code if code else "(no code generated)"}
```

ERROR MESSAGE (exact):
{error}{retry_note}

Diagnose the root cause and write complete fixed code."""

    result = llm.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=prompt)])
    raw = result.content if isinstance(result.content, str) else str(result.content)
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```$", "", raw.strip())

    try:
        data = json.loads(raw.strip())
    except Exception:
        # Model may have returned code block directly — try to extract it
        code_match = re.search(r"(import cadquery.*?show_object\(.*?\))", raw, re.DOTALL)
        data = {
            "fixed_code": code_match.group(1).strip() if code_match else "",
            "root_cause": "Parse error in repair specialist response",
            "fix_applied": raw[:300],
        }

    return data
