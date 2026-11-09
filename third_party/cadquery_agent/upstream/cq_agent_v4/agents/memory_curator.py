"""
Memory Curator agent — decides how to save a new shape to memory.
Prevents duplicates, generates better tags, decides: SAVE_NEW | UPDATE | SKIP.
"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

SYSTEM = """You are a CadQuery Memory Curator.

Before saving a new shape to the shape memory database, you:
1. Check if a similar shape already exists
2. Decide: SAVE_NEW | UPDATE_EXISTING | SKIP (duplicate)
3. Generate better tags than simple keyword extraction

GOOD TAG EXAMPLES:
  "L-shaped mounting bracket with M4 holes" → ["bracket", "L-shape", "mounting", "M4", "holes", "medium", "filleted"]
  "Involute spur gear 20 teeth module 2" → ["gear", "spur", "involute", "20-teeth", "module-2", "mechanical"]
  "Arduino enclosure with lid" → ["enclosure", "arduino", "lid", "electronics", "snap-fit", "assembly"]

TAG RULES:
- 5-10 tags per shape
- Include: shape category, key features, size class (small/medium/large), material hint if known
- Use hyphenated compound tags: "M4-holes" not "M4" and "holes" separately
- Skip stop words: a, an, the, with, and, for, of

DECISION RULES:
- SAVE_NEW: no similar shape exists (different shape type, different features, or different size)
- UPDATE_EXISTING: same shape type AND same main features, but this version has better code
- SKIP: nearly identical to existing entry (same request, same features, similar code)

RESPOND WITH VALID JSON ONLY (no markdown):
{
  "action": "SAVE_NEW",
  "update_index": null,
  "reason": "No bracket with L-shape in memory — saving as new",
  "improved_tags": ["bracket", "L-shape", "mounting", "M4-holes", "medium", "filleted"],
  "improved_description": "L-shaped aluminum mounting bracket with 4×M4 corner holes and 2mm fillets"
}"""


def run_memory_curator(api_key: str, model: str,
                        user_request: str, code: str,
                        existing_memory: list) -> dict:
    """
    Curate a new memory entry before saving.
    existing_memory: list of shape dicts from shapes_memory.json.
    Returns: {"action", "update_index", "improved_tags", "improved_description", "reason"}.
    """
    if not code:
        return {
            "action": "SKIP",
            "update_index": None,
            "reason": "No code to save",
            "improved_tags": [],
            "improved_description": "",
        }

    llm = ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0.0)

    # Compact memory summary — last 15 entries, 1 line each
    mem_lines = ["EXISTING MEMORY ENTRIES:"]
    for i, entry in enumerate(existing_memory[-15:]):
        tags = ", ".join(entry.get("tags", []))
        req = entry.get("request", "")[:80]
        mem_lines.append(f"  [{i}] {req} | tags: {tags}")
    mem_text = "\n".join(mem_lines)

    code_preview = code[:400].replace("\n", "\\n")
    prompt = f"""{mem_text}

NEW SHAPE TO SAVE:
Request: {user_request}
Code preview: {code_preview}

Decide whether to save, update, or skip. Generate improved tags."""

    result = llm.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=prompt)])
    raw = result.content if isinstance(result.content, str) else str(result.content)
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```$", "", raw.strip())

    try:
        data = json.loads(raw.strip())
    except Exception:
        # Fallback: always save with basic tags
        stop = {"a", "an", "the", "with", "and", "or", "for", "of", "to", "in", "on", "at"}
        tags = [w for w in user_request.lower().split() if w not in stop and len(w) > 2][:8]
        data = {
            "action": "SAVE_NEW",
            "update_index": None,
            "reason": "Fallback — save as new",
            "improved_tags": tags,
            "improved_description": user_request,
        }

    return data
