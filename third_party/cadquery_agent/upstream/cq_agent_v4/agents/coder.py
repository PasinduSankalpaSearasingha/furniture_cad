"""
Coder agent — writes and validates CadQuery code.
Uses the plan from planner + optional visual critique from reviewer.
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

SYSTEM = """You are an expert CadQuery programmer.

PRIME DIRECTIVE: If a reference image is provided, your code must reproduce that EXACT shape.
The image overrides everything else. Do not simplify, do not add features not in the image.

You have a PLAN (feature list) that you MUST implement completely.
You also have tools to search the skill library and validate your code.

TOOLS:
  search_skills(query)           — find a matching template
  list_skills()                  — see all 27 templates
  use_skill(name, params_json)   — render a template as starting code
  execute_cadquery(code)         — validate code; returns SUCCESS or error

WORKFLOW:
1. Search skills for the main shape keyword.
2. If match: use use_skill() as the base, then extend it.
3. Write/modify code to implement EVERY feature in the plan.
4. Call execute_cadquery(code) — repeat until SUCCESS (max 5 tries).
5. If FAILED: read the exact error, fix it. Do NOT remove features.

CADQUERY PATTERNS:
# Multi-body union:
  a = cq.Workplane("XY").box(w, h, t)
  b = cq.Workplane("XZ").box(w, d, t).translate((0, 0, t/2))
  result = a.union(b)

# Holes on face:
  result = result.faces(">Z").workplane().pushPoints(pts).hole(dia)

# Pocket:
  result = result.faces(">Z").workplane().rect(pw, ph).cutBlind(-depth)

# Fillet (ALWAYS last):
  result = result.edges("|Z").fillet(r)

# Stepped shaft:
  result = cq.Workplane("XY").circle(r1).extrude(h1)
  result = result.faces(">Z").workplane().circle(r2).extrude(h2)

# Shell (hollow inward):
  result = cq.Workplane("XY").box(60,40,30).faces(">Z").shell(-2)

CONVENTIONS:
  import cadquery as cq      (always)
  result = ...               (final shape)
  show_object(result)        (last line)"""


def run_coder(api_key: str, model: str, plan: str,
              visual_critique: str = "", recalled_shapes: list = None,
              image_path: str = "", dimension_hints: dict = None,
              skill_recommendations: list = None,
              code_critic_feedback: str = "") -> dict:
    """Run the coder agent. Returns {"code": str, "success": bool, "messages": list}."""
    from langgraph.prebuilt import create_react_agent
    from langchain_google_genai import ChatGoogleGenerativeAI
    from ..tools.cadquery_tools import execute_cadquery
    from ..tools.skill_tools import search_skills, list_skills, use_skill
    from .dimension_extractor import format_hints_for_planner
    import inspect, base64, os

    llm = ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0.1)
    system_msg = SystemMessage(content=SYSTEM)
    tools = [search_skills, list_skills, use_skill, execute_cadquery]

    params = inspect.signature(create_react_agent).parameters
    kwargs = {"prompt": system_msg} if "prompt" in params else \
             {"state_modifier": system_msg} if "state_modifier" in params else {}
    agent = create_react_agent(llm, tools, **kwargs)

    # Build the prompt with plan + optional critique + optional memory
    prompt_parts = [f"FEATURE PLAN (implement ALL of these):\n{plan}"]

    if dimension_hints:
        prompt_parts.append("\n" + format_hints_for_planner(dimension_hints))

    if skill_recommendations:
        from .skill_selector import format_skill_recommendations
        prompt_parts.append("\n" + format_skill_recommendations(skill_recommendations))

    if code_critic_feedback:
        prompt_parts.append(f"\n{code_critic_feedback}")

    if visual_critique:
        prompt_parts.append(
            f"\nVISUAL REVIEW FEEDBACK (fix these issues):\n{visual_critique}"
        )

    if recalled_shapes:
        mem_text = "\nSIMILAR PAST SHAPES (for reference):\n"
        for s in (recalled_shapes or [])[:2]:
            mem_text += f"Request: {s['request']}\nCode snippet:\n{s['code'][:400]}\n---\n"
        prompt_parts.append(mem_text)

    prompt_parts.append(
        "\nWrite complete CadQuery code implementing the full plan above, "
        "then call execute_cadquery to validate it."
    )

    # Build message content — add image if provided so coder can see target shape
    content = [{"type": "text", "text": "\n".join(prompt_parts)}]
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")
        content.insert(0, {"type": "text",
                            "text": "REFERENCE IMAGE (reproduce this exact shape):"})
        content.insert(1, {"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}})

    result = agent.invoke({"messages": [HumanMessage(content=content)]})
    messages = result.get("messages", [])

    code = _extract_code(messages)
    error = _extract_last_error(messages) if not code else ""
    return {
        "code":    code,
        "success": bool(code),
        "error":   error,
        "messages": messages,
    }


def _extract_last_error(messages: list) -> str:
    """Return the last error from execute_cadquery tool calls."""
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            content = str(msg.content)
            if "SUCCESS" not in content and len(content) > 10:
                return content[:500]
    return ""


def _extract_code(messages: list) -> str:
    """Find the last code that execute_cadquery reported SUCCESS on."""
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, ToolMessage) and "SUCCESS" in str(msg.content):
            tc_id = getattr(msg, "tool_call_id", None)
            for j in range(i - 1, -1, -1):
                prev = messages[j]
                if isinstance(prev, AIMessage) and prev.tool_calls:
                    for tc in prev.tool_calls:
                        if tc.get("name") == "execute_cadquery":
                            if tc_id is None or tc.get("id") == tc_id:
                                return tc["args"].get("code", "")
    return ""
