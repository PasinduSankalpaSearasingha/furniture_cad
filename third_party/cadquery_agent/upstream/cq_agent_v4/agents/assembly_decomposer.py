"""
Assembly Decomposer agent — detects if the input describes a multi-part assembly.
If yes: splits into individual part descriptions so each gets its own plan+code cycle.
Works with or without a reference image.
"""

import base64
import json
import os
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

SYSTEM_WITH_IMAGE = """You are a CAD Assembly Decomposer.

You see an image and a text description.

DECIDE: Is this a SINGLE PART or a MULTI-PART ASSEMBLY?

SINGLE PART: one solid body (may have features like holes, fillets, pockets, ribs)
  Examples: gear, bracket, enclosure lid, shaft, PCB standoff

ASSEMBLY: multiple distinct solids that exist as separate pieces
  Examples: bolt + nut, housing + lid, motor mount + bearings, plate + standoffs + cover

SIGNS OF ASSEMBLY in image:
- Visible gap or joint lines between components
- Different materials/colors for different pieces
- Parts that could be separated and still function independently
- "Exploded view" style gaps

RESPOND WITH VALID JSON ONLY (no markdown):
{
  "is_assembly": false,
  "part_count": 1,
  "assembly_type": "none|bolted|welded|press_fit|snap_fit|adhesive|constrained",
  "confidence": "high|medium|low",
  "parts": [
    {
      "name": "base_plate",
      "description": "Rectangular plate 100x60x5mm with 4 corner M4 holes",
      "primary_shape": "box|cylinder|sphere|bracket|gear|...",
      "relative_position": "at origin"
    }
  ],
  "assembly_note": "How parts connect or where they sit relative to each other"
}"""

SYSTEM_WITHOUT_IMAGE = """You are a CAD Assembly Decomposer.

You read a TEXT description to determine if it describes a single part or assembly.

ASSEMBLY KEYWORDS (suggest multi-part): "with lid", "and bolts", "housing assembly",
  "two parts", "multiple", "attach", "connect", "joint", "plus", "assembly",
  "box with cover", "base and", "top and bottom"

SINGLE PART KEYWORDS: shape adjectives only — "hollowed", "with holes", "filleted",
  "chamfered", "stepped", "swept", single shape name without connectors

RESPOND WITH VALID JSON ONLY (no markdown):
{
  "is_assembly": false,
  "part_count": 1,
  "assembly_type": "none",
  "confidence": "high|medium|low",
  "parts": [
    {
      "name": "main_body",
      "description": "<restate the single shape description cleanly>",
      "primary_shape": "<box|cylinder|sphere|bracket|...>",
      "relative_position": "at origin"
    }
  ],
  "assembly_note": ""
}"""


def run_assembly_decomposer(api_key: str, model: str,
                             user_request: str, image_path: str = "") -> dict:
    """
    Detect if input is an assembly and decompose into parts.
    Returns a dict with is_assembly, parts list, and assembly_note.
    """
    has_image = bool(image_path and os.path.exists(image_path))
    llm = ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0.0)

    system = SYSTEM_WITH_IMAGE if has_image else SYSTEM_WITHOUT_IMAGE

    if has_image:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")
        content = [
            {"type": "text",
             "text": f"USER REQUEST: {user_request}\n\nIs this a single part or multi-part assembly?"},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
    else:
        content = [{"type": "text",
                    "text": f"USER REQUEST: {user_request}\n\nIs this a single part or multi-part assembly?"}]

    result = llm.invoke([SystemMessage(content=system), HumanMessage(content=content)])
    raw = result.content if isinstance(result.content, str) else str(result.content)
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```$", "", raw.strip())

    try:
        data = json.loads(raw.strip())
    except Exception:
        data = {
            "is_assembly": False,
            "part_count": 1,
            "assembly_type": "none",
            "confidence": "low",
            "parts": [{"name": "main", "description": user_request,
                       "primary_shape": "unknown", "relative_position": "at origin"}],
            "assembly_note": "",
        }

    return data


def format_assembly_context(data: dict) -> str:
    """Format decomposition result as a text block for the planner prompt."""
    if not data:
        return ""

    if not data.get("is_assembly"):
        # Single part — optionally return the cleaned description
        parts = data.get("parts", [])
        if parts:
            desc = parts[0].get("description", "")
            shape = parts[0].get("primary_shape", "")
            if desc and desc != parts[0].get("name"):
                return f"Shape identified as: {shape} — {desc}"
        return ""

    lines = [
        "=== ASSEMBLY DECOMPOSER ===",
        f"MULTI-PART ASSEMBLY DETECTED: {data.get('part_count', '?')} parts",
        f"Assembly type: {data.get('assembly_type', 'unknown')} "
        f"(confidence: {data.get('confidence', '?')})",
        "",
        "Parts to build (build each separately, then assemble):",
    ]
    for i, part in enumerate(data.get("parts", []), 1):
        lines.append(f"  Part {i} — '{part['name']}':")
        lines.append(f"    Shape: {part.get('primary_shape', '?')}")
        lines.append(f"    Description: {part.get('description', '?')}")
        lines.append(f"    Position: {part.get('relative_position', 'unknown')}")

    if data.get("assembly_note"):
        lines.append(f"\nAssembly instructions: {data['assembly_note']}")

    lines.append("")
    lines.append("Use cadquery .union() or cadquery-massembly MAssembly to combine parts.")
    lines.append("=== BUILD EACH PART THEN COMBINE ===")
    return "\n".join(lines)
