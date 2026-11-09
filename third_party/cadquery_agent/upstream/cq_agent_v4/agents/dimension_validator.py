"""
Dimension Validator agent — compares proportions between reference image and rendered SVG.
Focused on MEASUREMENTS only, not feature presence (that's the Reviewer's job).
Only runs when a reference image is provided.
"""

import base64
import json
import os
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

SYSTEM = """You are a CAD Dimension Validator.

You see: (1) REFERENCE IMAGE and (2) RENDERED SVG of the generated shape.

YOUR ONLY JOB: Compare proportions and sizes NUMERICALLY. Do NOT comment on feature presence.

WHAT TO MEASURE:
- Overall width:height ratio in front/isometric view
- Relative feature sizes (hole diameter vs body width)
- Depth vs width ratio (from isometric or side view)
- Spacing ratios (gap between features vs body size)

HOW TO MEASURE WITHOUT A RULER:
- Count pixel widths at key points in both images
- Compare ratios: if reference width is 2x its height, generated should also be 2:1
- Report as ratios, not absolute mm (SVG has no scale)

RESPOND WITH VALID JSON ONLY (no markdown):
{
  "proportions_match": false,
  "ref_width_height": 2.1,
  "gen_width_height": 3.4,
  "ref_feature_ratio": 0.12,
  "gen_feature_ratio": 0.09,
  "corrections": [
    "Shape is ~60% too wide relative to height — reduce width or increase height",
    "Holes appear ~25% too small relative to body — increase hole diameter"
  ],
  "confidence": "high|medium|low",
  "notes": "Isometric view used for comparison. Depth ratio could not be verified."
}

If proportions look correct: {"proportions_match": true, "corrections": [], "confidence": "medium"}
If no reference image available: {"proportions_match": true, "corrections": [], "confidence": "low", "notes": "No reference image"}"""


def run_dimension_validator(api_key: str, model: str,
                             svg_paths: dict, image_path: str = "",
                             dimension_hints: dict = None) -> dict:
    """
    Compare rendered SVG proportions against reference image.
    Returns correction dict; only meaningful when image_path is provided.
    """
    if not image_path or not os.path.exists(image_path) or not svg_paths:
        return {"proportions_match": True, "corrections": [], "confidence": "low",
                "notes": "No reference image or no SVG available"}

    llm = ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0.0)

    def _encode(path: str):
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp",
                "svg": "image/svg+xml"}.get(ext, "image/png")
        return b64, mime

    content = [{"type": "text", "text": "IMAGE 1 — REFERENCE (target proportions):"}]
    try:
        ref_b64, ref_mime = _encode(image_path)
        content.append({"type": "image_url",
                         "image_url": {"url": f"data:{ref_mime};base64,{ref_b64}"}})
    except Exception:
        return {"proportions_match": True, "corrections": [], "confidence": "low"}

    # Pick best SVG view: isometric shows 3D proportions best
    svg_path = (svg_paths.get("isometric") or svg_paths.get("front") or
                next(iter(svg_paths.values()), None))
    if svg_path and os.path.exists(str(svg_path)):
        content.append({"type": "text", "text": "IMAGE 2 — GENERATED SHAPE (compare proportions):"})
        try:
            svg_b64, svg_mime = _encode(str(svg_path))
            content.append({"type": "image_url",
                             "image_url": {"url": f"data:{svg_mime};base64,{svg_b64}"}})
        except Exception:
            pass

    if dimension_hints:
        overall = dimension_hints.get("overall", {})
        if overall.get("width_mm"):
            content.append({"type": "text",
                             "text": f"Extracted target: {overall['width_mm']}×{overall['height_mm']}×{overall['depth_mm']}mm"})

    content.append({"type": "text", "text": "Measure proportions in both images and report mismatches."})

    result = llm.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=content)])
    raw = result.content if isinstance(result.content, str) else str(result.content)
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```$", "", raw.strip())

    try:
        data = json.loads(raw.strip())
    except Exception:
        data = {"proportions_match": True, "corrections": [], "confidence": "low"}

    return data


def format_dimension_corrections(data: dict) -> str:
    """Format validator output as a text block for the reviewer/coder prompt."""
    if not data or data.get("proportions_match") or not data.get("corrections"):
        return ""

    lines = [
        "=== DIMENSION VALIDATOR: PROPORTION MISMATCH ===",
        f"Confidence: {data.get('confidence', '?')}",
    ]
    rw = data.get("ref_width_height")
    gw = data.get("gen_width_height")
    if rw and gw:
        lines.append(f"Width:Height — target {rw:.2f}:1, generated {gw:.2f}:1")
    rf = data.get("ref_feature_ratio")
    gf = data.get("gen_feature_ratio")
    if rf and gf:
        lines.append(f"Feature:Body ratio — target {rf:.2f}, generated {gf:.2f}")
    lines.append("Proportion corrections needed:")
    for fix in data.get("corrections", []):
        lines.append(f"  RESIZE: {fix}")
    if data.get("notes"):
        lines.append(f"Note: {data['notes']}")
    lines.append("=== ADJUST DIMENSIONS TO MATCH TARGET PROPORTIONS ===")
    return "\n".join(lines)
