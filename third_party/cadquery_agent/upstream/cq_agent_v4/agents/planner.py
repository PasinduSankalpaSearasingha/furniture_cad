"""
Planner agent — analyses the user request, checks memory, calls plan_shape.
Returns a structured feature plan the coder must follow.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

SYSTEM = """You are a CadQuery 3D CAD planning expert.

Your job: produce a complete, exact feature plan so the coder can reproduce the shape precisely.

You have access to:
  plan_shape(features)   — call this to commit to the feature list

IF AN IMAGE IS PROVIDED:
- The image is the PRIMARY specification. Text request is secondary.
- Study the image carefully: count every visible feature (holes, ribs, pockets, steps, fillets).
- Estimate relative dimensions from proportions in the image.
- Your plan must describe ONLY what is visible in the image — do NOT add features not shown.
- Do NOT generalise ("some holes") — be exact ("4 circular holes, evenly spaced, ~diameter 1/8 of body width").

STEPS:
1. If image provided: describe every visible geometric feature first.
2. Cross-check with text request for any stated dimensions.
3. Call plan_shape() with the complete feature list.
4. Reply with ONE sentence summarising the plan.

RULES:
- Exact over approximate — estimate from image proportions if no dimensions given.
- Do NOT omit visible features. Do NOT add invisible ones.
- Do NOT write any CadQuery code — that's the coder's job."""


def run_planner(api_key: str, model: str, user_request: str,
                recalled_shapes: list, image_path: str = "",
                dimension_hints: dict = None,
                assembly_context: str = "") -> str:
    """Run the planner agent. Returns the plan text."""
    from langgraph.prebuilt import create_react_agent
    from ..tools.skill_tools import plan_shape
    from .dimension_extractor import format_hints_for_planner
    import inspect, base64, os

    llm = ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0.1)
    system_msg = SystemMessage(content=SYSTEM)

    params = inspect.signature(create_react_agent).parameters
    kwargs = {"prompt": system_msg} if "prompt" in params else \
             {"state_modifier": system_msg} if "state_modifier" in params else {}
    agent = create_react_agent(llm, [plan_shape], **kwargs)

    # Build context message
    memory_ctx = ""
    if recalled_shapes:
        memory_ctx = "\n\nSIMILAR PAST SHAPES FROM MEMORY:\n"
        for s in recalled_shapes:
            memory_ctx += f"- Request: {s['request']}\n  Tags: {', '.join(s.get('tags', []))}\n"

    dim_ctx = ""
    if dimension_hints:
        dim_ctx = "\n\n" + format_hints_for_planner(dimension_hints)

    asm_ctx = f"\n\n{assembly_context}" if assembly_context else ""

    content = [{"type": "text",
                "text": f"User request: {user_request}{memory_ctx}{dim_ctx}{asm_ctx}"}]

    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")
        content.append({"type": "image_url",
                         "image_url": {"url": f"data:{mime};base64,{b64}"}})

    result = agent.invoke({"messages": [HumanMessage(content=content)]})
    msgs = result.get("messages", [])

    # Return the last AI text
    for msg in reversed(msgs):
        from langchain_core.messages import AIMessage
        if isinstance(msg, AIMessage):
            c = msg.content
            return c if isinstance(c, str) else str(c)
    return "Plan completed."
