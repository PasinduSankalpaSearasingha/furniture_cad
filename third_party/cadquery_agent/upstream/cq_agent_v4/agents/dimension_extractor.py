"""
Dimension Extractor agent.

Works in two modes:
  WITH image  — reads proportions, counts features, estimates mm from visual ratios
  WITHOUT image — parses text for explicit numbers, infers engineering standards,
                  fills missing dimensions with reasonable defaults
"""

import base64
import json
import os
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

SYSTEM_WITH_IMAGE = """You are a CAD dimension extraction specialist.

You are given an image of a 3D shape or part. No scale bar may be present.

YOUR ONLY JOB: Extract every visible dimension, proportion, and feature count.

RULES:
- Study overall proportions first (width:height:depth ratios)
- Count EVERY visible feature: holes, ribs, slots, fillets, steps, pockets
- If no scale reference: estimate absolute mm based on what the part LOOKS LIKE it would be in real life
  (a bolt hole looks like M4-M8, a machine bracket looks like 50-150mm wide, etc.)
- Be specific about positions: "4 holes at corners, ~10% from each edge"
- Never say "unknown" — always give a best estimate with confidence level

RESPOND WITH VALID JSON ONLY (no markdown, no explanation):
{
  "has_image": true,
  "overall": {
    "width_mm": <number>,
    "height_mm": <number>,
    "depth_mm": <number>,
    "width_height_ratio": <number>,
    "confidence": "high|medium|low"
  },
  "features": [
    {
      "type": "hole|slot|pocket|rib|boss|fillet|chamfer|thread|step|text",
      "count": <number>,
      "size_mm": <number or null>,
      "depth_mm": <number or null>,
      "position": "<description>",
      "notes": "<optional>"
    }
  ],
  "shape_type": "<bracket|enclosure|gear|shaft|plate|...>",
  "notes": "<anything important the planner should know>"
}"""

SYSTEM_WITHOUT_IMAGE = """You are a CAD dimension extraction specialist.

You are given a TEXT description of a shape to build. No image is provided.

YOUR ONLY JOB: Extract all dimensions from the text and infer missing ones.

RULES:
1. Extract any EXPLICIT dimensions stated in the text (e.g. "80mm wide", "M6 holes")
2. Apply ENGINEERING STANDARDS for named features:
   - M3 hole = 3mm, M4 = 4mm, M5 = 5mm, M6 = 6mm, M8 = 8mm, M10 = 10mm
   - Standard PCB = 60×40mm, Arduino = 68×53mm, Raspberry Pi = 85×56mm
   - Standard bracket thickness = 4-6mm
   - Fillet radius = typically 1-3mm for small parts, 3-8mm for large parts
3. For missing dimensions, infer from SHAPE TYPE:
   - Small bracket: ~80×40×5mm
   - Enclosure: ~100×60×40mm
   - Gear: module=1-2, 20-40 teeth
   - Shaft: diameter 6-25mm, length 2-5x diameter
4. Mark each dimension as "explicit" (stated in text) or "inferred" (your estimate)

RESPOND WITH VALID JSON ONLY (no markdown, no explanation):
{
  "has_image": false,
  "overall": {
    "width_mm": <number>,
    "height_mm": <number>,
    "depth_mm": <number>,
    "confidence": "high|medium|low"
  },
  "features": [
    {
      "type": "hole|slot|pocket|rib|boss|fillet|chamfer|thread|step",
      "count": <number>,
      "size_mm": <number or null>,
      "depth_mm": <number or null>,
      "position": "<description>",
      "source": "explicit|inferred",
      "notes": "<optional>"
    }
  ],
  "explicit_dimensions": ["<list of dimensions found directly in text>"],
  "inferred_dimensions": ["<list of dimensions you estimated>"],
  "shape_type": "<bracket|enclosure|gear|shaft|plate|...>",
  "notes": "<anything important the planner should know>"
}"""


def run_dimension_extractor(api_key: str, model: str,
                             user_request: str, image_path: str = "") -> dict:
    """
    Returns a dimension hints dict to be passed to the planner.
    Works with or without a reference image.
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
             "text": f"USER REQUEST: {user_request}\n\nAnalyse this image and extract all dimensions:"},
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
    else:
        content = [
            {"type": "text",
             "text": f"USER REQUEST: {user_request}\n\nExtract all dimensions from this description:"},
        ]

    result = llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=content),
    ])

    raw = result.content if isinstance(result.content, str) else str(result.content)

    # Strip markdown code fences if model wraps in ```json
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```$", "", raw.strip())

    try:
        hints = json.loads(raw.strip())
    except json.JSONDecodeError:
        # Fallback: return minimal hints so pipeline doesn't break
        hints = {
            "has_image": has_image,
            "overall": {"width_mm": None, "height_mm": None, "depth_mm": None,
                        "confidence": "low"},
            "features": [],
            "notes": raw[:500],
        }

    return hints


def format_hints_for_planner(hints: dict) -> str:
    """Convert dimension hints dict to a readable string for the planner prompt."""
    if not hints:
        return ""

    lines = ["=== DIMENSION EXTRACTOR OUTPUT ==="]

    overall = hints.get("overall", {})
    if overall.get("width_mm"):
        lines.append(f"Overall size: {overall.get('width_mm')}mm (W) × "
                     f"{overall.get('height_mm')}mm (H) × "
                     f"{overall.get('depth_mm')}mm (D)  "
                     f"[confidence: {overall.get('confidence', '?')}]")

    if hints.get("shape_type"):
        lines.append(f"Shape type: {hints['shape_type']}")

    features = hints.get("features", [])
    if features:
        lines.append("Features detected:")
        for f in features:
            src = f" [{f.get('source', '')}]" if f.get("source") else ""
            size = f", size={f.get('size_mm')}mm" if f.get("size_mm") else ""
            depth = f", depth={f.get('depth_mm')}mm" if f.get("depth_mm") else ""
            pos = f", at: {f.get('position')}" if f.get("position") else ""
            lines.append(f"  - {f.get('count', 1)}x {f.get('type')}{size}{depth}{pos}{src}")

    if hints.get("explicit_dimensions"):
        lines.append(f"Explicit in text: {', '.join(hints['explicit_dimensions'])}")

    if hints.get("inferred_dimensions"):
        lines.append(f"Inferred: {', '.join(hints['inferred_dimensions'])}")

    if hints.get("notes"):
        lines.append(f"Notes: {hints['notes']}")

    lines.append("=== USE THESE DIMENSIONS IN YOUR PLAN ===")
    return "\n".join(lines)
