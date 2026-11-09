"""
v4 Multi-agent orchestration graph.

Nodes:
  recall_memory         — search past shapes for context
  dimension_extractor   — extract/infer all dimensions (with or without image)
  assembly_decomposer   — detect multi-part assemblies, split into parts
  plan                  — planner agent: builds feature plan
  skill_selector        — pick best 1-3 skill templates for this plan
  code                  — coder agent: writes + validates code
  code_critic           — logical plan-vs-code mismatch check (post-execution)
  repair_specialist     — deep error fixer, activated when coder exhausts retries
  render                — renders shape to SVG projections
  dimension_validator   — proportion comparison (image vs SVG)
  review                — reviewer agent: visual inspection
  human_approval        — interrupt: asks user to approve/reject
  save_memory           — memory curator + saves shape to JSON store

Full-mode flow:
  START → recall → dim_extract → assembly → plan → skill_select
       → code → code_critic → render → dim_validate → review
       → [REJECTED → code] | [APPROVED → human_approval]
       → save_memory → END

Fast-mode flow (visual_review=False):
  START → recall → dim_extract → assembly → plan → skill_select
       → code → code_critic → save_memory → END

Repair path (code fails after all retries):
  code → repair_specialist → render/save_memory
"""

import os
import sys

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .state import AgentState

MAX_VISUAL_LOOPS  = 2
MAX_CRITIC_LOOPS  = 1   # code_critic gets one shot to send back corrections


# ---------------------------------------------------------------------------
# Node: recall_memory
# ---------------------------------------------------------------------------

def recall_memory_node(state: AgentState) -> dict:
    from .memory.store import recall_similar
    recalled = recall_similar(state["user_request"], top_n=3)
    return {"recalled_shapes": recalled}


# ---------------------------------------------------------------------------
# Node: dimension_extractor
# ---------------------------------------------------------------------------

def dimension_extractor_node(state: AgentState) -> dict:
    from .agents.dimension_extractor import run_dimension_extractor
    hints = run_dimension_extractor(
        api_key=state["api_key"],
        model=state["model"],
        user_request=state["user_request"],
        image_path=state.get("image_path", ""),
    )
    return {"dimension_hints": hints}


# ---------------------------------------------------------------------------
# Node: assembly_decomposer
# ---------------------------------------------------------------------------

def assembly_decomposer_node(state: AgentState) -> dict:
    from .agents.assembly_decomposer import run_assembly_decomposer
    result = run_assembly_decomposer(
        api_key=state["api_key"],
        model=state["model"],
        user_request=state["user_request"],
        image_path=state.get("image_path", ""),
    )
    return {
        "is_assembly": result.get("is_assembly", False),
        "sub_shapes":  result.get("parts", []),
    }


# ---------------------------------------------------------------------------
# Node: plan
# ---------------------------------------------------------------------------

def plan_node(state: AgentState) -> dict:
    from .agents.planner import run_planner
    from .agents.assembly_decomposer import format_assembly_context

    # Build assembly context if decomposer found multiple parts
    assembly_ctx = ""
    if state.get("sub_shapes"):
        assembly_ctx = format_assembly_context({
            "is_assembly": state.get("is_assembly", False),
            "parts": state.get("sub_shapes", []),
            "assembly_type": "",
            "assembly_note": "",
        })

    plan_text = run_planner(
        api_key=state["api_key"],
        model=state["model"],
        user_request=state["user_request"],
        recalled_shapes=state.get("recalled_shapes", []),
        image_path=state.get("image_path", ""),
        dimension_hints=state.get("dimension_hints", {}),
        assembly_context=assembly_ctx,
    )
    return {"plan": plan_text}


# ---------------------------------------------------------------------------
# Node: skill_selector
# ---------------------------------------------------------------------------

def skill_selector_node(state: AgentState) -> dict:
    from .agents.skill_selector import run_skill_selector
    import os

    # Locate the skill_registry.json relative to agent_dir
    agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    registry_path = os.path.join(agent_dir, "skills", "skill_registry.json")

    recommendations = run_skill_selector(
        api_key=state["api_key"],
        model=state["model"],
        plan=state.get("plan", ""),
        skill_registry_path=registry_path,
    )
    return {"skill_recommendations": recommendations}


# ---------------------------------------------------------------------------
# Node: code
# ---------------------------------------------------------------------------

def code_node(state: AgentState) -> dict:
    from .agents.coder import run_coder
    result = run_coder(
        api_key=state["api_key"],
        model=state["model"],
        plan=state.get("plan", ""),
        visual_critique=state.get("visual_critique", ""),
        recalled_shapes=state.get("recalled_shapes", []),
        image_path=state.get("image_path", ""),
        dimension_hints=state.get("dimension_hints", {}),
        skill_recommendations=state.get("skill_recommendations", []),
        code_critic_feedback=state.get("code_critic_feedback", ""),
    )
    return {
        "generated_code":    result["code"],
        "execution_success": result["success"],
        "execution_error":   result.get("error", ""),
        "repair_attempts":   state.get("repair_attempts", 0) + 1,
        # Clear previous critic feedback so it doesn't accumulate
        "code_critic_feedback": "",
    }


# ---------------------------------------------------------------------------
# Node: code_critic
# ---------------------------------------------------------------------------

def code_critic_node(state: AgentState) -> dict:
    from .agents.code_critic import run_code_critic, format_critic_feedback

    code = state.get("generated_code", "")
    plan = state.get("plan", "")

    if not code or not plan:
        return {"code_critic_feedback": "", "critic_loops": state.get("critic_loops", 0)}

    result = run_code_critic(
        api_key=state["api_key"],
        model=state["model"],
        plan=plan,
        code=code,
    )
    feedback = format_critic_feedback(result) if result.get("issues_found") else ""
    return {
        "code_critic_feedback": feedback,
        "critic_loops": state.get("critic_loops", 0) + 1,
    }


# ---------------------------------------------------------------------------
# Node: repair_specialist
# ---------------------------------------------------------------------------

def repair_specialist_node(state: AgentState) -> dict:
    from .agents.repair_specialist import run_repair_specialist
    from .tools.cadquery_tools import execute_cadquery

    result = run_repair_specialist(
        api_key=state["api_key"],
        model=state["model"],
        code=state.get("generated_code", ""),
        error=state.get("execution_error", "Unknown error"),
        plan=state.get("plan", ""),
        previous_repair_attempts=state.get("repair_attempts", 0) - 1,
    )

    fixed_code = result.get("fixed_code", "")
    success = False
    error = ""

    if fixed_code:
        # Validate the repair specialist's fix
        exec_result = execute_cadquery.invoke({"code": fixed_code})
        success = "SUCCESS" in exec_result
        if not success:
            error = exec_result

    return {
        "generated_code":    fixed_code if fixed_code else state.get("generated_code", ""),
        "execution_success": success,
        "execution_error":   error,
        "repair_used":       True,
        "repair_attempts":   state.get("repair_attempts", 0) + 1,
    }


# ---------------------------------------------------------------------------
# Node: render
# ---------------------------------------------------------------------------

def render_node(state: AgentState) -> dict:
    code = state.get("generated_code", "")
    if not code or not state.get("execution_success"):
        return {"svg_paths": {}}

    from .tools.cadquery_tools import render_shape
    import json

    raw = render_shape.invoke({"code": code})
    if raw.startswith("RENDER_FAILED"):
        return {"svg_paths": {}}

    try:
        paths = json.loads(raw)
        return {"svg_paths": paths}
    except Exception:
        return {"svg_paths": {}}


# ---------------------------------------------------------------------------
# Node: dimension_validator
# ---------------------------------------------------------------------------

def dimension_validator_node(state: AgentState) -> dict:
    from .agents.dimension_validator import run_dimension_validator

    result = run_dimension_validator(
        api_key=state["api_key"],
        model=state["model"],
        svg_paths=state.get("svg_paths", {}),
        image_path=state.get("image_path", ""),
        dimension_hints=state.get("dimension_hints", {}),
    )
    return {"dimension_validation": result}


# ---------------------------------------------------------------------------
# Node: review
# ---------------------------------------------------------------------------

def review_node(state: AgentState) -> dict:
    svg_paths = state.get("svg_paths", {})
    if not svg_paths:
        return {
            "shape_approved":  True,
            "visual_critique": "No render available — skipping visual review.",
        }

    from .agents.reviewer import run_reviewer
    from .agents.dimension_validator import format_dimension_corrections

    # Append dimension corrections to the reviewer's context
    dim_corrections = format_dimension_corrections(state.get("dimension_validation", {}))

    result = run_reviewer(
        api_key=state["api_key"],
        model=state["model"],
        user_request=state["user_request"],
        plan=state.get("plan", ""),
        svg_paths=svg_paths,
        image_path=state.get("image_path", ""),
        extra_context=dim_corrections,
    )
    return {
        "shape_approved":  result["approved"],
        "visual_critique": result["critique"],
        "visual_loops":    state.get("visual_loops", 0) + 1,
    }


# ---------------------------------------------------------------------------
# Node: human_approval (interrupt)
# ---------------------------------------------------------------------------

def human_approval_node(state: AgentState) -> dict:
    svg_paths      = state.get("svg_paths", {})
    visual_critique = state.get("visual_critique", "")
    code           = state.get("generated_code", "")
    code_failed    = not bool(code)

    response = interrupt({
        "message":         "Code generation failed — no shape to show." if code_failed
                           else "Shape looks correct. Do you approve loading this code?",
        "svg_paths":       svg_paths,
        "visual_critique": visual_critique,
        "code_preview":    "\n".join(code.splitlines()[:6]) if code else "",
        "code_failed":     code_failed,
    })

    if response.get("cancel"):
        return {
            "human_approved": False,
            "human_feedback": "CANCELLED",
            "final_message":  "Cancelled by user.",
        }

    approved = response.get("approved", True)
    feedback = response.get("feedback", "")

    if not approved:
        rejection_msg = f"\n\nHUMAN REJECTED (attempt {state.get('repair_attempts', 0)})."
        rejection_msg += f" Feedback: {feedback}" if feedback else " Try a completely different approach."
        new_critique = visual_critique + rejection_msg
    else:
        new_critique = visual_critique

    return {
        "human_approved":  approved,
        "human_feedback":  feedback,
        "visual_critique": new_critique,
    }


# ---------------------------------------------------------------------------
# Node: save_memory  (uses Memory Curator)
# ---------------------------------------------------------------------------

def save_memory_node(state: AgentState) -> dict:
    from .memory.store import recall_similar, save_shape, update_shape
    from .agents.memory_curator import run_memory_curator

    code = state.get("generated_code", "")
    final_code = code

    if code:
        request = state.get("user_request", "")

        # Load existing memory for the curator to inspect
        existing = recall_similar("", top_n=50)  # all recent entries

        curation = run_memory_curator(
            api_key=state["api_key"],
            model=state["model"],
            user_request=request,
            code=code,
            existing_memory=existing,
        )

        action = curation.get("action", "SAVE_NEW")
        tags   = curation.get("improved_tags", [])
        desc   = curation.get("improved_description", request)

        if action == "SKIP":
            pass  # duplicate — don't save
        elif action == "UPDATE_EXISTING":
            idx = curation.get("update_index")
            if idx is not None:
                update_shape(idx, request=desc, code=code, tags=tags)
            else:
                save_shape(request=desc, code=code, tags=tags)
        else:  # SAVE_NEW
            save_shape(request=desc, code=code, tags=tags)

    return {
        "final_code":    final_code,
        "final_message": "Done — shape generated, validated, approved and saved to memory.",
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route_after_code_exec(state: AgentState) -> str:
    """After code_node: if code failed → repair; else → code_critic."""
    if not state.get("execution_success") and state.get("generated_code", "") == "":
        return "repair_specialist"
    return "code_critic"


def route_after_code_critic(state: AgentState) -> str:
    """After code_critic: re-send to coder if issues found and budget allows."""
    has_issues  = bool(state.get("code_critic_feedback"))
    critic_loops = state.get("critic_loops", 0)
    if has_issues and critic_loops <= MAX_CRITIC_LOOPS:
        return "code"
    if state.get("visual_review", True):
        return "render"
    return "save_memory"


def route_after_repair(state: AgentState) -> str:
    """After repair_specialist: continue to render or save."""
    if state.get("visual_review", True):
        return "render"
    return "save_memory"


def route_after_review(state: AgentState) -> str:
    loops = state.get("visual_loops", 0)
    if state.get("shape_approved") or loops >= MAX_VISUAL_LOOPS:
        return "human_approval"
    return "code"


def route_after_human(state: AgentState) -> str:
    if state.get("human_approved", True):
        return "save_memory"
    if state.get("human_feedback") == "CANCELLED":
        return "save_memory"
    return "code"


# ---------------------------------------------------------------------------
# Build graph
# ---------------------------------------------------------------------------

def build_graph():
    g = StateGraph(AgentState)

    g.add_node("recall_memory",         recall_memory_node)
    g.add_node("dimension_extractor",   dimension_extractor_node)
    g.add_node("assembly_decomposer",   assembly_decomposer_node)
    g.add_node("plan",                  plan_node)
    g.add_node("skill_selector",        skill_selector_node)
    g.add_node("code",                  code_node)
    g.add_node("code_critic",           code_critic_node)
    g.add_node("repair_specialist",     repair_specialist_node)
    g.add_node("render",                render_node)
    g.add_node("dimension_validator",   dimension_validator_node)
    g.add_node("review",                review_node)
    g.add_node("human_approval",        human_approval_node)
    g.add_node("save_memory",           save_memory_node)

    # Linear prefix (always runs)
    g.add_edge(START,                "recall_memory")
    g.add_edge("recall_memory",      "dimension_extractor")
    g.add_edge("dimension_extractor","assembly_decomposer")
    g.add_edge("assembly_decomposer","plan")
    g.add_edge("plan",               "skill_selector")
    g.add_edge("skill_selector",     "code")

    # Code → critic or repair
    g.add_conditional_edges("code", route_after_code_exec, {
        "code_critic":      "code_critic",
        "repair_specialist":"repair_specialist",
    })

    # Critic loop or continue
    g.add_conditional_edges("code_critic", route_after_code_critic, {
        "code":        "code",
        "render":      "render",
        "save_memory": "save_memory",
    })

    # Repair → render or save
    g.add_conditional_edges("repair_specialist", route_after_repair, {
        "render":      "render",
        "save_memory": "save_memory",
    })

    # Visual pipeline
    g.add_edge("render",             "dimension_validator")
    g.add_edge("dimension_validator","review")

    g.add_conditional_edges("review", route_after_review, {
        "human_approval": "human_approval",
        "code":           "code",
    })
    g.add_conditional_edges("human_approval", route_after_human, {
        "save_memory": "save_memory",
        "code":        "code",
    })

    g.add_edge("save_memory", END)

    checkpointer = MemorySaver()
    return g.compile(checkpointer=checkpointer)


graph = build_graph()
