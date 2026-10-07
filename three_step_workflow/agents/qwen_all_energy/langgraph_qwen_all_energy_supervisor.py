from __future__ import annotations

import hashlib

import argparse
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from llm_workflow_qwen_pilot_supervisor import audit_result_eligible
from llm_workflow_qwen_pilot_supervisor import (
    STEP2_DESIGN_KEYS,
    DEFAULT_NUM_CTX,
    DEFAULT_NUM_PREDICT,
    DEFAULT_SEED,
    active_climate_prompt_context,
    append_attempt_event,
    climate_prompt_section,
    ask_json,
    compact_handoff,
    count_logged_proposals,
    design_parameter_signature,
    enrich_step1_handoff,
    existing_design_signatures,
    generation_error_rows,
    read_json,
    read_text,
    require_step1_spec,
    run_command,
    run_step3_cases,
    select_step2_best,
    staged_exploration_instruction,
    summarize_previous_designs,
    summarize_stage_feedback,
    write_json,
)
from llm_workflow_qwen_step1_driver import BASE_URL, MODEL, create_project_context
from open_design_agent import (STEP2_SCRIPT_FILE, materialize_script, step2_registry, step2_spec_from_proposal,
                               step2_task_text)
from physical_constraints import check_hvac_selection_service_quality
from audit_contract import sha256_file


COMMAND_WHITELIST = {
    "create_project_context": "Create one run-specific project_context.json and output root.",
    "stage1_massing_interface": "Validate Step 1 massing and render the massing image.",
    "stage1_massing_energyplus": "Run fixed WWR=0.56, timestep=6 Step 1 energy screen.",
    "stage1_select_best_by_energy": "Select lowest valid fixed-envelope Step 1 EUI.",
    "stage2_envelope_energyplus": "Run Step 2 detailed envelope EnergyPlus.",
    "stage2_select_best_from_feedback": "Select global lowest valid Step 2 EUI.",
    "stage3_hvac_energyplus": "Run Step 3 HVAC EnergyPlus.",
}

AGENT_SHELL = "LangGraph all-energy deterministic workflow"
MODEL_NAME = f"{os.environ.get('AGENT_LLM_BACKEND', 'ollama')}/{MODEL}"
HARNESS_VERSION = "open-design-agent-v1"
PROMPT_PROTOCOL_VERSION = "open-design-prompts-v1"
EXPERIMENT_PROTOCOL_VERSION = "2.0"
DEFAULT_OUTPUT_ROOT_BASE = r"E:\LLM_Output\qwen_hk_all_energy"
DEFAULT_REPO = Path(__file__).resolve().parents[2]
DEFAULT_USER_REQUIREMENTS = (
    "Active-location all-energy supervised agent design: Step 1 twenty valid massings, "
    "Step 2 ten valid envelopes, Step 3 ten valid HVAC cases."
)
STEP1_FIXED_WWR = "0.56"
STEP1_FIXED_WWR_VALUE = 0.56
BASELINE_ROW_ID = "baseline_osm_timestep_6"
GENERATED_ORIGINAL_CONTROL_ID = "generated_original_control"
# Reference rows in the Step 1 results table; neither is a selectable design.
STEP1_REFERENCE_ROW_IDS = {BASELINE_ROW_ID, GENERATED_ORIGINAL_CONTROL_ID}
VALID_STEP1_INTERFACE_STATUSES = {"ready_for_simulation", "valid_interface_record"}
CASE_NAMESPACE = os.environ.get("AGENT_CASE_NAMESPACE", "qwen_hk")
STEP1_CANDIDATE_PREFIX = f"candidate_{CASE_NAMESPACE}_"
STEP2_CASE_PREFIX = f"envelope_{CASE_NAMESPACE}_"
STEP3_CASE_PREFIX = f"hvac_{CASE_NAMESPACE}_"
MODEL_CONDITIONS = ("full_feedback", "no_feedback", "schema_only")


class State(TypedDict):
    repo: str
    location: str
    output_root_base: str
    output_root: str
    fixed_step1_handoff: str
    user_requirements: str
    step1_count: int
    step2_count: int
    step3_count: int
    step1_proposal_cap: int | None
    step2_proposal_cap: int | None
    step3_proposal_cap: int | None
    log_dir: str
    step1_valid_ids: list[str]
    step1_best_id: str
    step2_valid_ids: list[str]
    step2_best_id: str
    step3_valid_ids: list[str]
    invocation_started_at: str
    condition: str


def env_for(output_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["AUTOMATED_DESIGN_OUTPUT_ROOT"] = str(output_root)
    context_path = output_root / "active_project_context.json"
    if context_path.exists():
        env["AUTOMATED_DESIGN_PROJECT_CONTEXT"] = str(context_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def active_context_payload(state: State, output_root: Path) -> dict[str, Any]:
    """Create the compact, machine-readable contract shared by all three stages."""
    return {
        "context_version": "all-energy-3step-v4",
        "location": state["location"],
        "output_root": str(output_root),
        "design_requirements": {
            "user_requirements": state["user_requirements"],
            "llm_analysis": (
                "Optimize one active-location office design sequentially. Read only current-run evidence, "
                "keep cross-stage handoffs immutable, and select the lowest eligible site EUI."
            ),
        },
        "objective": {
            "primary_metric": "selected_total_eui_kwh_m2",
            "direction": "minimize",
            "eligibility": "completed, physical_constraints_ok=true, severe_count=0, fatal_count=0",
            "baseline_role": "report comparison only; never block or replace the best generated handoff",
        },
        "frozen_simulation": {
            "timestep_per_hour": 6,
            "thermostat_setpoints": "inherit active baseline; the agent may not tune them",
            "infiltration_flow_per_exterior_area_m3_s_m2": "inherit active baseline",
            "step3_new_system_cooling_sizing_factor": 1.05,
            "availability_prestart_hours": 0.0,
            "artificial_precooling": False,
            "internal_loads_schedules_infiltration_weather": "inherit active baseline unchanged",
        },
        "stage_scope": {
            "step1": "massing geometry only; fixed WWR=0.56, offset windows, no shading",
            "step2": "envelope only; freeze selected Step 1 geometry and HVAC",
            "step3": "HVAC only; freeze selected Step 2 massing, envelope, loads, schedules and weather",
        },
        "handoff_policy": {
            "step1_to_step2": "automatic lowest eligible Step 1 fixed-envelope EUI",
            "step2_to_step3": "automatic lowest eligible Step 2 EUI",
            "no_cross_run_history": True,
        },
        "hvac_constraints": {
            "ventilation": (
                "every zone is ventilated through a DOAS, a 100% outdoor-air loop whose cooling is separate from "
                "zone terminal cooling; in the simulation each zone must receive its occupants' minimum outdoor air"
            ),
            "families": ["doas_fcu", "doas_chilled_beam", "doas_radiant"],
            "comfort_and_humidity": "report occupied PMV and RH alongside EUI; failed physical/comfort checks are ineligible",
            "final_selection_service_gate": (
                "valid occupied PMV evidence, a passing PMV baseline comparison and the minimum-ventilation check; "
                "air-thermostat unmet hours are diagnostic only"
            ),
        },
        "experiment_condition": state["condition"],
        "proposal_targets": {
            "step1": state["step1_count"],
            "step2": state["step2_count"],
            "step3": state["step3_count"],
        },
        "proposal_caps": {
            "step1": state["step1_proposal_cap"],
            "step2": state["step2_proposal_cap"],
            "step3": state["step3_proposal_cap"],
        },
    }


def write_active_context(state: State, output_root: Path) -> Path:
    path = output_root / "active_project_context.json"
    write_json(path, active_context_payload(state, output_root))
    os.environ["AUTOMATED_DESIGN_PROJECT_CONTEXT"] = str(path)
    return path


def agent_candidate_file(output_root: Path, case_id: str, filename: str) -> Path:
    return output_root / "agent_candidates" / case_id / filename


def step1_massing_feedback_path(output_root: Path, candidate_id: str) -> Path:
    return output_root / "step1_massing" / "llm_iterations" / candidate_id / "feedback.json"


def step1_energy_feedback_path(output_root: Path, candidate_id: str) -> Path:
    return output_root / "step1_massing_energy" / "llm_iterations" / candidate_id / "feedback.json"


def step2_feedback_path(output_root: Path, case_id: str) -> Path:
    return output_root / "step2_envelope_layout" / "llm_iterations" / case_id / "feedback.json"


def step3_feedback_path(output_root: Path, case_id: str) -> Path:
    return output_root / "step3_hvac" / "llm_iterations" / case_id / "feedback.json"


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def is_valid_step1_massing_feedback(feedback: dict[str, Any]) -> bool:
    return feedback.get("status") in VALID_STEP1_INTERFACE_STATUSES and _truthy(feedback.get("physical_constraints_ok"))


def is_valid_energy_feedback(feedback: dict[str, Any]) -> bool:
    from optimization_feedback import valid_energy_result
    return valid_energy_result(feedback)


def resolve_node_paths(state: State) -> tuple[Path, Path, Path]:
    return (
        Path(state["repo"]).resolve(),
        Path(state["output_root"]).resolve(),
        Path(state["log_dir"]).resolve(),
    )


def install_fixed_step1_handoff(source_root: Path, output_root: Path) -> None:
    if not source_root.exists():
        raise RuntimeError(f"Fixed Step 1 handoff does not exist: {source_root}")
    required = [
        source_root / "step1_massing" / "organized_current" / "best_massing.json",
        source_root / "step1_massing_energy" / "massing_energy_results.csv",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Fixed Step 1 handoff is missing required files: " + "; ".join(missing))

    for folder_name in ("step1_massing", "step1_massing_energy"):
        source = source_root / folder_name
        if source.exists():
            shutil.copytree(
                source,
                output_root / folder_name,
                dirs_exist_ok=True,
                ignore=lambda directory, names: [
                    name for name in names if not (Path(directory) / name).exists()
                ],
            )
    for filename in ("candidate_llm_012_energy_result.json", "file_manifest.json"):
        source = source_root / filename
        if source.exists():
            shutil.copy2(source, output_root / filename)

    best_path = output_root / "step1_massing" / "organized_current" / "best_massing.json"
    rebind_fixed_step1_geometry_evidence(source_root, output_root)
    enrich_step1_handoff(best_path)
    best_massing = read_json(best_path)
    candidate_id = str(best_massing.get("candidate_id") or "").strip()
    if candidate_id:
        compatibility_dir = output_root / "step1_massing" / "llm_code_batch" / candidate_id
        compatibility_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best_path, compatibility_dir / "geometry.json")
    print(f"[All_Energy LangGraph] Installed fixed Step 1 handoff from: {source_root}", flush=True)


def rebind_fixed_step1_geometry_evidence(source_root: Path, output_root: Path) -> None:
    """Point the copied handoff's evidence at the copied geometry file.

    Step 2 accepts a Step 1 handoff only when its evidence binds the geometry
    file of the active output root. The digest is unchanged; every other
    evidence entry keeps pointing at the fixed source run.
    """
    best_path = output_root / "step1_massing" / "organized_current" / "best_massing.json"
    payload = read_json(best_path)
    candidate_id = str(payload.get("candidate_id") or "").strip()
    energy_result = payload.get("energy_result")
    if not candidate_id or not isinstance(energy_result, dict):
        raise RuntimeError("Fixed Step 1 handoff has no selected candidate with an energy result.")
    relative = Path("step1_massing") / "llm_iterations" / candidate_id / "geometry.json"
    source_geometry = (source_root / relative).resolve()
    target_geometry = (output_root / relative).resolve()
    evidence = json.loads(energy_result.get("evidence_json") or "{}")
    digest = evidence.get(str(source_geometry), evidence.get(str(target_geometry)))
    if not digest or not target_geometry.is_file() or sha256_file(target_geometry) != digest:
        raise RuntimeError("Cannot bind the fixed Step 1 handoff: the copied geometry does not match its evidence.")
    evidence.pop(str(source_geometry), None)
    evidence[str(target_geometry)] = digest
    energy_result["evidence_json"] = json.dumps(evidence, sort_keys=True)
    write_json(best_path, payload)


def _polygon_summary(points: Any) -> dict[str, Any]:
    if not isinstance(points, list) or len(points) < 3:
        return {"vertex_count": 0}
    xy: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return {"vertex_count": len(points)}
        try:
            xy.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            return {"vertex_count": len(points)}
    twice_area = sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1])
    )
    return {
        "vertex_count": len(xy),
        "gross_area_m2": round(abs(twice_area) / 2.0, 2),
        "bbox_m": {
            "min_x": round(min(x for x, _ in xy), 2),
            "max_x": round(max(x for x, _ in xy), 2),
            "min_y": round(min(y for _, y in xy), 2),
            "max_y": round(max(y for _, y in xy), 2),
        },
        "vertex_mean_centroid_m": {
            "x": round(sum(x for x, _ in xy) / len(xy), 2),
            "y": round(sum(y for _, y in xy) / len(xy), 2),
        },
    }


def _compact_massing_tiers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    massing = spec.get("massing") or {}
    tiers = massing.get("parametric_tiers") or massing.get("tiers")
    if not isinstance(tiers, list):
        return []
    compact: list[dict[str, Any]] = []
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        item = {
                "story_start": tier.get("story_start"),
                "story_end": tier.get("story_end"),
                "design_note": str(tier.get("design_note") or "")[:240],
            }
        if "footprint" in tier:
            item["footprint_summary"] = _polygon_summary(tier.get("footprint"))
        else:
            item["parameters"] = {
                key: tier.get(key) for key in (
                    "shape", "sides", "aspect_ratio", "area_weight", "corner_cut_fraction",
                    "wing_fraction", "offset_x_m", "offset_y_m",
                ) if key in tier
            }
        compact.append(item)
    return compact


def summarize_step1_energy_feedback(output_root: Path, limit: int = 60) -> str:
    rows: list[str] = []
    energy_dir = output_root / "step1_massing_energy" / "llm_iterations"
    candidate_ids = {path.parent.name for path in energy_dir.glob("*/feedback.json")}
    candidate_ids.update(
        path.parent.name for path in (output_root / "agent_candidates").glob("*/massing_spec.json")
    )
    for candidate_id in sorted(candidate_ids):
        feedback_path = energy_dir / candidate_id / "feedback.json"
        feedback = read_json(feedback_path) if feedback_path.exists() else {}
        massing_path = step1_massing_feedback_path(output_root, candidate_id)
        massing_feedback = read_json(massing_path) if massing_path.exists() else {}
        spec_path = agent_candidate_file(output_root, candidate_id, "massing_spec.json")
        spec = read_json(spec_path) if spec_path.exists() else {}
        rows.append(
            json.dumps(
                {
                    "candidate_id": candidate_id,
                    "geometry_role": feedback.get("geometry_role", "candidate"),
                    "selectable": feedback.get("selectable", True),
                    "design": {
                        "description": str(spec.get("description") or "")[:500],
                        "area_tolerance_m2": spec.get("area_tolerance_m2"),
                        "rotation_deg": (spec.get("massing") or {}).get("rotation_deg"),
                        "tiers": _compact_massing_tiers(spec),
                    },
                    "status": feedback.get("status", massing_feedback.get("status", "generated_without_feedback")),
                    "selected_total_eui_kwh_m2": feedback.get("selected_total_eui_kwh_m2"),
                    "optimization_diagnostics": feedback.get("optimization_diagnostics"),
                    "heating_eui_kwh_m2": feedback.get("heating_eui_kwh_m2"),
                    "cooling_eui_kwh_m2": feedback.get("cooling_eui_kwh_m2"),
                    "fan_eui_kwh_m2": feedback.get("fan_eui_kwh_m2"),
                    "delta_vs_original_pct": feedback.get("delta_vs_original_pct"),
                    "delta_vs_generated_original_pct": feedback.get("delta_vs_generated_original_pct"),
                    "min_core_to_facade_depth_m": feedback.get("min_core_to_facade_depth_m"),
                    "max_core_area_share": feedback.get("max_core_area_share"),
                    "tier_count": feedback.get("tier_count"),
                    "max_vertices_per_tier": feedback.get("max_vertices_per_tier"),
                    "facade_perimeter_story_m": feedback.get("facade_perimeter_story_m"),
                    "exposed_roof_area_proxy_m2": feedback.get("exposed_roof_area_proxy_m2"),
                    "severe_count": feedback.get("severe_count"),
                    "fatal_count": feedback.get("fatal_count"),
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(rows[-limit:])


def summarize_step1_designs(output_root: Path, limit: int = 60) -> str:
    """Parameters of this run's earlier Step 1 proposals, without any result."""
    rows = []
    for path in sorted((output_root / "agent_candidates").glob("*/massing_spec.json")):
        spec = read_json(path)
        rows.append(json.dumps({
            "candidate_id": path.parent.name,
            "design": {
                "area_tolerance_m2": spec.get("area_tolerance_m2"),
                "rotation_deg": (spec.get("massing") or {}).get("rotation_deg"),
                "tiers": [{key: value for key, value in tier.items() if key != "design_note"}
                          for tier in _compact_massing_tiers(spec)],
            },
        }, ensure_ascii=False))
    return "\n".join(rows[-limit:])


def step1_energy_prompt(
    repo: Path,
    output_root: Path,
    candidate_id: str,
    valid_count: int = 0,
    target_count: int = 20,
    condition: str = "full_feedback",
) -> str:
    contract = read_text(repo / "steps" / "step1_massing_energy" / "prompt_massing_contract.json")
    previous = summarize_step1_energy_feedback(output_root) if condition == "full_feedback" else ""
    building = (_maybe_read_json(repo / "config" / "block_m.json") or {}).get("building", {})
    min_core_depth = float(building.get("min_core_to_facade_depth_m", 4.5))
    max_core_share = float(building.get("max_core_area_share", 0.30))
    climate = active_climate_prompt_context(repo, output_root)
    if valid_count == 0:
        adaptive_brief = (
            "Choose your own credible reference geometry. Do not optimize against an assumed winning family before evidence exists."
        )
    elif valid_count < max(6, int(target_count * 0.4)):
        adaptive_brief = (
            "From the registry, identify the least-tested meaningful geometry family or solar-orientation hypothesis and design "
            "one non-duplicate experiment for it. You—not this prompt—must choose the family and parameters."
        )
    elif valid_count < max(12, int(target_count * 0.7)):
        adaptive_brief = (
            "Choose a strong and a weak prior case, infer one causal geometric variable worth testing, and generate a controlled "
            "comparison while retaining population diversity."
        )
    else:
        adaptive_brief = (
            "Decide whether evidence supports local refinement or a family switch. If refining, change one continuous variable; "
            "if progress has stalled, generate a distinct evidence-based hypothesis."
        )
    if condition == "full_feedback":
        if valid_count == 0:
            strategy = (
                "EXPLORATION phase (0 valid cases in this independent run). Establish one credible "
                "architectural reference without pushing floor area, site bounds, or cantilever limits. "
                "Do not use results from any other run."
            )
        else:
            fraction = valid_count / max(target_count, 1)
            if fraction < 0.40:
                strategy = (
                    "DIVERSE EXPLORATION phase. Test a geometry family, aspect ratio, rotation, or tier schedule "
                    "not represented in prior valid cases. Do not refine the incumbent yet."
                )
            elif fraction < 0.70:
                strategy = (
                    "CONTROLLED COMPARISON phase. Use one strong and one weak prior case to isolate one or two "
                    "causal geometry variables; retain diversity across shape families."
                )
            else:
                strategy = (
                    "REFINEMENT phase. Refine a supported low-EUI family with small parameter changes, while "
                    "reserving at least one remaining case for a distinct geometry family."
                )
    elif condition == "no_feedback":
        strategy = (
            "Your previous designs of this run are listed below without results. Propose a massing that differs "
            "from them; no validation or simulation results are available."
        )
    elif condition == "schema_only":
        strategy = "Return one valid massing using only the task, hard constraints, and JSON contract."
    else:
        raise ValueError(f"Unsupported model condition: {condition}")
    # The adaptive brief and the result-interpretation rules refer to the
    # registry, so only full_feedback receives them. no_feedback keeps the
    # domain facts; schema_only keeps the task, constraints and contract.
    adaptive_line = f"- Adaptive generator brief: {adaptive_brief}" if condition == "full_feedback" else ""
    interpretation_rules = {
        "full_feedback": (
            "- Use optimization_diagnostics when present to distinguish zone sensible cooling, window solar gain, "
            "facade/glass area and roof/soffit exposure. Fixed WWR does not fix total glass area. Thermal loads are not electricity.\n"
            "- Fixed lighting plus plug-load EUI is not affected by massing. Judge geometry learning primarily from "
            "cooling, fan, and pump changes as well as total EUI; never claim fixed-load savings."
        ),
        "no_feedback": (
            "- Fixed WWR does not fix total glass area. Fixed lighting plus plug-load EUI is not affected by massing; "
            "never claim fixed-load savings."
        ),
        "schema_only": "",
    }[condition]
    refinement_rules = ""
    history_section = ""
    if condition == "full_feedback":
        refinement_rules = """
- Do not assume the current best massing is the global optimum; a repeatedly selected incumbent may be a local optimum caused by a greedy search.
- Treat the original baseline EUI as the performance reference even though it is not a selectable generated massing. If every generated case is worse than the baseline, explicitly treat the gap as unresolved and prioritize hypotheses that reduce it; do not describe the least-bad generated case as an energy improvement.
- The registry row generated_original_control is the original building (its stories, story heights, floor plates and cores) rebuilt by this generator with the same windows, zoning and infiltration as candidates; it is also the baseline model. It is a non-selectable reference: delta_vs_generated_original_pct compares a candidate with the original massing under the candidate window protocol.
- During exploration, use the registry to identify an untested geometric hypothesis rather than copying its lowest-EUI footprint.
- In `description`, cite one reference case, identify the exact geometric variables changed, and explain what new hypothesis the case tests.
- A lower tier that extends beyond an upper tier creates an exposed setback roof; it does not shade the upper facade above it. Do not claim setback self-shading unless an upper volume or explicit geometry actually blocks sun to a lower facade.
- Compare candidate vertical facade exposure as perimeter multiplied by story count, and account separately for every exposed roof or terrace created by setbacks.
- Do not improve EUI by pushing net floor area toward either tolerance boundary. Treat 16315 m2 as the target, not a variable.
- Do not use progressively wider upper tiers as a surrogate shading device. Unsupported expansion beyond 1.5 m between adjacent tiers is invalid.
""".strip()
        history_section = f"""
Attempted Step 1 design-and-result registry (regenerated before every LLM call):
{previous or "(none yet)"}

Use the registry as compact evidence. Do not exhaust the generation budget re-deriving every prior
geometry or narrating a long comparison. Identify one evidence-based new massing move, verify its
area/core/story constraints, and emit the final JSON promptly.
""".strip()
    elif condition == "no_feedback":
        history_section = f"""
Your previous Step 1 designs in this run (validation and simulation results intentionally withheld):
{summarize_step1_designs(output_root) or "(none yet)"}
""".strip()
    return f"""
Return one Step 1 massing JSON object for candidate_id {candidate_id}.
Do not include markdown or explanation. The final answer must start with {{.
EXPERIMENT_CONDITION={condition}

Goal:
- Office massing for the active project climate, within the declared project constraints.
- Primary objective: find the lowest selected_total_eui_kwh_m2 among physically valid Step 1 massing designs.
- Optimize early-stage fixed-envelope energy performance through massing only.
- The supervisor will render every massing and run a fast EnergyPlus screen using fixed WWR=0.56, offset_window baseline windows, no shading, timestep=6.
- You may only improve massing geometry, height tiering, orientation, and floorplate proportions. Do not tune WWR/HVAC/added shading in Step 1.
- Rotation is a real design variable and is propagated to the exported OSM.

Mandatory experiment strategy:
- {strategy}
{adaptive_line}
{refinement_rules}
- Treat EnergyPlus as deterministic. Different EUI values for the same tier schedule and footprint coordinates indicate a pipeline inconsistency, not a stochastic opportunity to improve the design by rerunning it.

Hard constraints:
- Site is 45 m x 45 m centered at (0,0), coordinate range -22.5..22.5.
- Because parametric tiers are rescaled to the exact floor-area target, estimate the post-scaling footprint bounds before emitting JSON. Aim for at least 0.30 m clearance from every site edge; a nominal pre-scaling fit is not sufficient.
- 18 stories use zero-based, end-exclusive ranges: story_start=0 and story_end=18 covers all 18 floors.
- Never use story_start=1/story_end=18 for the full tower; that covers only 17 floors and is invalid.
- Tier ranges must cover every floor exactly once with no gaps or overlaps, e.g. 0..6, 6..12, 12..18.
- Core area is 252 m2 per floor.
- The fixed central core is a 21.0 m x 12.0 m rectangle centered at (0,0); every tier footprint must fully contain this rectangle.
- Do not carve, notch, taper, or shift any footprint so that the central core protrudes outside the massing.
- Every tier must keep at least {min_core_depth:g} m of clear office depth between the fixed core and the facade, measured as the minimum distance between the core rectangle and the footprint boundary.
- The 252 m2 core may occupy at most {max_core_share:.0%} of any tier's gross floor-plate area; a tier shrunk towards the core is not a usable office floor.
- Target net floor area is 16315 m2.
- Net area = sum((gross footprint area - 252) * (story_end - story_start)).
- For tiered designs, calculate each polygon area so the total net area lands between 16290 and 16340 m2.
- Set top-level area_tolerance_m2 to 25.0.
- Use one to four tiers. Each generated polygon is limited to 12 vertices and every facade segment must be at least 1.0 m.
- Adjacent upper tiers may cantilever by at most 1.5 m beyond the tier directly below, and at most one transition may expand upward.
- Use `massing.parametric_tiers`, not raw coordinate arrays. Choose shape, aspect_ratio, area_weight, offsets, and rotation; the interface scales all tiers to exactly 16315 m2 net area.
- Allowed shapes are rectangle, chamfered_rectangle, regular_polygon (5-12 sides), and cross (wing_fraction 0.55-0.85). Cross is the only supported concave family. Do not approximate circles with many short wall segments.
- Avoid unusable slivers, jaggedness, and repeated inverted setbacks.
{interpretation_rules}

{climate_prompt_section(climate, condition)}

{history_section}

Output field/type contract (schema describes the output, do not return the schema):
{contract}
""".strip()


def valid_step1_energy_case(output_root: Path, candidate_id: str) -> bool:
    massing_feedback_path = step1_massing_feedback_path(output_root, candidate_id)
    if not massing_feedback_path.exists():
        return False
    massing_feedback = read_json(massing_feedback_path)
    if not is_valid_step1_massing_feedback(massing_feedback):
        return False

    feedback_path = step1_energy_feedback_path(output_root, candidate_id)
    if not feedback_path.exists():
        return False
    return is_valid_energy_feedback(read_json(feedback_path))


def valid_step2_energy_case(output_root: Path, case_id: str) -> bool:
    feedback_path = step2_feedback_path(output_root, case_id)
    if not feedback_path.exists():
        return False
    return is_valid_energy_feedback(read_json(feedback_path))


def valid_step3_energy_case(output_root: Path, case_id: str) -> bool:
    feedback_path = step3_feedback_path(output_root, case_id)
    if not feedback_path.exists():
        return False
    return is_valid_energy_feedback(read_json(feedback_path))


def scan_valid_step1_ids(output_root: Path) -> list[str]:
    energy_dir = output_root / "step1_massing_energy" / "llm_iterations"
    return [
        path.name
        for path in sorted(energy_dir.glob(f"{STEP1_CANDIDATE_PREFIX}*"))
        if path.is_dir() and valid_step1_energy_case(output_root, path.name)
    ]


def scan_valid_step2_ids(output_root: Path) -> list[str]:
    step2_dir = output_root / "step2_envelope_layout" / "llm_iterations"
    return [
        path.name
        for path in sorted(step2_dir.glob(f"{STEP2_CASE_PREFIX}*"))
        if path.is_dir() and valid_step2_energy_case(output_root, path.name)
    ]


def scan_valid_step3_ids(output_root: Path) -> list[str]:
    step3_dir = output_root / "step3_hvac" / "llm_iterations"
    return [
        path.name
        for path in sorted(step3_dir.glob(f"{STEP3_CASE_PREFIX}*"))
        if path.is_dir() and valid_step3_energy_case(output_root, path.name)
    ]


def generate_step1_energy_cases(
    repo: Path,
    output_root: Path,
    count: int,
    env: dict[str, str],
    log_dir: Path,
    condition: str = "full_feedback",
    proposal_cap: int | None = None,
) -> list[str]:
    valid_ids = scan_valid_step1_ids(output_root)[:count]
    attempted_signatures = existing_design_signatures(output_root, "step1")
    existing_candidate_dirs = list((output_root / "agent_candidates").glob(f"{STEP1_CANDIDATE_PREFIX}*"))
    existing_numbers = [
        int(path.name.removeprefix(STEP1_CANDIDATE_PREFIX))
        for path in existing_candidate_dirs
        if path.name.removeprefix(STEP1_CANDIDATE_PREFIX).isdigit()
    ]
    next_candidate_number = max(existing_numbers, default=0) + 1
    if valid_ids:
        print(f"Step 1 existing valid cases: {len(valid_ids)}/{count}", flush=True)
    attempts = 0
    prior_proposals = count_logged_proposals(output_root, "step1")
    max_attempts = (
        max(proposal_cap - prior_proposals, 0)
        if proposal_cap is not None
        else (count - len(valid_ids)) * 5
    )
    while len(valid_ids) < count and attempts < max_attempts:
        attempts += 1
        candidate_id = f"{STEP1_CANDIDATE_PREFIX}{next_candidate_number + attempts - 1:03d}"
        print(f"\n=== Step 1 energy attempt {attempts}: {candidate_id} ===", flush=True)
        try:
            spec = ask_json(
                step1_energy_prompt(
                    repo, output_root, candidate_id, len(valid_ids), count, condition
                ),
                log_label=f"step1_energy_{candidate_id}",
            )
            require_step1_spec(spec)
        except Exception as exc:
            if "CLAUDE_CAPACITY_LIMIT" in str(exc):
                raise
            print(f"Qwen Step 1 JSON generation failed: {exc}", flush=True)
            if "502" in str(exc):
                time.sleep(20)
            continue
        signature = design_parameter_signature("step1", spec)
        if signature in attempted_signatures:
            print(f"Step 1 duplicate geometry parameters blocked before simulation: {candidate_id}", flush=True)
            continue
        attempted_signatures.add(signature)
        spec_path = agent_candidate_file(output_root, candidate_id, "massing_spec.json")
        write_json(spec_path, spec)

        code = run_command(
            [
                sys.executable,
                "steps/step1_massing_energy/stage1_massing_interface.py",
                "--json-spec",
                str(spec_path),
                "--candidate-id",
                candidate_id,
            ],
            cwd=repo,
            env=env,
            log_path=log_dir / f"{candidate_id}_interface.log",
        )
        if code != 0:
            append_attempt_event(
                output_root,
                {
                    "event_type": "simulation_validation",
                    "condition": condition,
                    "stage": "step1",
                    "attempt_id": candidate_id,
                    "candidate_id": candidate_id,
                    "outcome": "invalid",
                    "command_exit_code": code,
                    "physical_constraints_ok": False,
                    "error_category": "massing_interface",
                    "evidence_path": str(log_dir / f"{candidate_id}_interface.log"),
                    "details": {"phase": "massing_interface"},
                },
            )
            continue
        massing_feedback_path = step1_massing_feedback_path(output_root, candidate_id)
        massing_feedback = read_json(massing_feedback_path) if massing_feedback_path.exists() else {}
        if not is_valid_step1_massing_feedback(massing_feedback):
            print(f"Step 1 massing invalid before energy simulation: {candidate_id}", flush=True)
            append_attempt_event(
                output_root,
                {
                    "event_type": "simulation_validation",
                    "condition": condition,
                    "stage": "step1",
                    "attempt_id": candidate_id,
                    "candidate_id": candidate_id,
                    "outcome": "invalid",
                    "command_exit_code": code,
                    "physical_constraints_ok": False,
                    "error_category": "physical_constraints",
                    "evidence_path": str(massing_feedback_path),
                    "details": {"phase": "massing_interface", "feedback_status": massing_feedback.get("status")},
                },
            )
            continue

        code = run_command(
            [
                sys.executable,
                "steps/step1_massing_energy/stage1_massing_energyplus.py",
                "--candidate",
                candidate_id,
                "--wwr",
                STEP1_FIXED_WWR,
                "--overwrite",
            ],
            cwd=repo,
            env=env,
            log_path=log_dir / f"{candidate_id}_step1_energy.log",
        )
        energy_feedback_path = step1_energy_feedback_path(output_root, candidate_id)
        energy_feedback = read_json(energy_feedback_path) if energy_feedback_path.exists() else {}
        is_valid = code == 0 and valid_step1_energy_case(output_root, candidate_id)
        append_attempt_event(
            output_root,
            {
                "event_type": "simulation_validation",
                "condition": condition,
                "stage": "step1",
                "attempt_id": candidate_id,
                "candidate_id": candidate_id,
                "outcome": "valid" if is_valid else "invalid",
                "command_exit_code": code,
                "physical_constraints_ok": is_valid_step1_massing_feedback(massing_feedback) and code == 0,
                "selected_total_eui_kwh_m2": energy_feedback.get("selected_total_eui_kwh_m2"),
                "error_category": None if is_valid else "energy_simulation_or_constraints",
                "evidence_path": str(energy_feedback_path),
                "details": {"phase": "step1_energy", "feedback_status": energy_feedback.get("status")},
            },
        )
        if is_valid:
            valid_ids.append(candidate_id)
            print(f"Step 1 energy valid cases: {len(valid_ids)}/{count}", flush=True)
        else:
            print(f"Step 1 energy case did not become valid: {candidate_id}", flush=True)
    if not valid_ids:
        raise RuntimeError(f"Produced no valid Step 1 energy cases after {attempts} attempts.")
    if len(valid_ids) < count and proposal_cap is None:
        raise RuntimeError(f"Only produced {len(valid_ids)} valid Step 1 energy cases after {attempts} attempts.")
    return valid_ids


def select_step1_best_by_energy(repo: Path, output_root: Path, env: dict[str, str], log_dir: Path) -> str:
    csv_path = output_root / "step1_massing_energy" / "massing_energy_results.csv"
    if not csv_path.exists():
        raise RuntimeError(f"Missing Step 1 energy results CSV: {csv_path}")
    candidates: list[dict[str, Any]] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("candidate_id") in STEP1_REFERENCE_ROW_IDS:
                continue
            if row.get("status") != "completed":
                continue
            if not row.get("selected_total_eui_kwh_m2"):
                continue
            massing_feedback_path = step1_massing_feedback_path(output_root, row["candidate_id"])
            if not massing_feedback_path.exists():
                continue
            massing_feedback = read_json(massing_feedback_path)
            if not is_valid_step1_massing_feedback(massing_feedback):
                continue
            candidates.append(row)
    if not candidates:
        raise RuntimeError("No completed Step 1 energy candidates to select.")
    candidates.sort(key=lambda row: float(row["selected_total_eui_kwh_m2"]))
    best_id = candidates[0]["candidate_id"]
    best_path = output_root / "step1_massing" / "organized_current" / "best_massing.json"
    if not best_path.exists():
        raise RuntimeError("Step 1 energy runner did not write the automatic best_massing handoff")
    selected = read_json(best_path)
    if str(selected.get("candidate_id")) != best_id:
        raise RuntimeError(
            f"Step 1 handoff mismatch: CSV best is {best_id}, but best_massing.json is "
            f"{selected.get('candidate_id')}"
        )
    enrich_step1_handoff(best_path)
    compatibility_dir = output_root / "step1_massing" / "llm_code_batch" / best_id
    compatibility_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best_path, compatibility_dir / "geometry.json")
    write_json(
        output_root / "step1_massing_energy" / "organized_current" / "best_massing_energy.json",
        {
            "candidate_id": best_id,
            "selection_metric": "selected_total_eui_kwh_m2",
            "selected_total_eui_kwh_m2": float(candidates[0]["selected_total_eui_kwh_m2"]),
            "baseline_row_id": BASELINE_ROW_ID,
            "baseline_role": "descriptive_original_model_only_not_a_handoff_threshold",
        },
    )
    print(
        f"Step 1 energy global best: {best_id} total={float(candidates[0]['selected_total_eui_kwh_m2']):.4f} kWh/m2",
        flush=True,
    )
    return best_id


def step2_energy_prompt(
    repo: Path,
    output_root: Path,
    case_id: str,
    valid_count: int = 0,
    target_count: int = 10,
    condition: str = "full_feedback",
) -> str:
    """Step 2 of the open workflow: the model writes the OpenStudio Ruby script that builds its envelope.

    full_feedback sees every earlier proposal with its results and failed rules, the latest script and the
    best valid script; no_feedback sees the same proposals and latest script without any result;
    schema_only sees the task, rules and API only."""
    step1_best = read_json(output_root / "step1_massing" / "organized_current" / "best_massing.json")
    climate = active_climate_prompt_context(repo, output_root)
    timestep_per_hour = int(os.environ.get("STEP2_TIMESTEP_PER_HOUR", "6"))
    energy_results = ""
    if condition == "full_feedback":
        energy_results = read_text(output_root / "step1_massing_energy" / "massing_energy_results.csv", max_chars=5000)
        fraction = valid_count / max(target_count, 1)
        if fraction < 0.40:
            strategy = ("DIVERSE EXPLORATION phase. Test distinct window and shading hypotheses that are not yet "
                        "represented in prior valid cases; do not repeat an executed script.")
        elif fraction < 0.70:
            strategy = ("CONTROLLED COMPARISON phase. Change one aspect at a time (window ratio distribution, window "
                        "layout or shading) relative to a cited prior case so its effect is identifiable.")
        else:
            strategy = ("REFINEMENT phase. Refine supported cases with small changes, but keep at least one "
                        "contrasting facade hypothesis rather than converging every remaining case to the incumbent.")
        history = f"""Step 1 fixed-envelope energy CSV excerpt:
{energy_results}

Earlier Step 2 proposals of this run with their results (regenerated before every call):
{step2_registry(output_root, with_results=True)}

Feedback interpretation:
- Fix a failed script or a failed rule first, using the script log and the rule data.
- Use the end uses to see what an envelope change did; never interpret missing outputs as zero."""
    elif condition == "no_feedback":
        strategy = ("Your previous designs of this run are listed below without results. Propose a facade that "
                    "differs from them; no validation or simulation results are available.")
        history = f"""Your previous Step 2 proposals in this run (validation and simulation results intentionally withheld):
{step2_registry(output_root, with_results=False)}"""
    elif condition == "schema_only":
        strategy = "Return one valid envelope script using only the task, the rules and the API below."
        history = ""
    else:
        raise ValueError(f"Unsupported model condition: {condition}")
    return f"""
Return one Step 2 envelope JSON object for case_id {case_id}.
Do not include markdown or explanation. The final answer must start with {{.
EXPERIMENT_CONDITION={condition}

Step 2 scope:
- The selected Step 1 massing is fixed. Design the windows and the exterior shading by writing an OpenStudio
  Ruby script; there is no list of window or shading types.
- Do not change massing, floor count, site, core, program, schedules, HVAC or simulation settings; the
  model-level rules reject any change outside windows and added shading.
- The supervisor runs Step 2 EnergyPlus at {timestep_per_hour} timesteps per hour.
- Primary objective: find the lowest selected_total_eui_kwh_m2 among valid Step 2 envelope designs.
- This model has no daylight-responsive lighting controls: lighting EUI is fixed. Never claim a simulated
  daylight or lighting-energy benefit.
- First satisfy the rules, then improve energy. A case that fails a rule is ineligible.

Mandatory experiment strategy:
- {strategy}
- Treat EnergyPlus as deterministic; an unchanged envelope must not be rerun.
- In design_intent, state the hypothesis and what the script changes{" and cite the reference case" if condition == "full_feedback" else ""}.

Output JSON (no other fields):
{{"description": "...", "design_intent": "...", "envelope_script": "<the complete Ruby script as one JSON string>"}}
The supervisor writes envelope_script to {STEP2_SCRIPT_FILE} next to the spec and runs it on a copy of the base model.

{step2_task_text(repo)}

{climate_prompt_section(climate, condition)}

Selected Step 1 best massing:
{json.dumps(compact_handoff(step1_best), ensure_ascii=False)}

{history}
""".strip()


def verified_envelope_base(output_root: Path) -> bool:
    """True when the Step 2 base model on disk is the verified reproduction of the current Step 1 handoff."""
    base_dir = output_root / "step2_envelope_layout" / "envelope_base"
    record_path, osm_path = base_dir / "envelope_base.json", base_dir / "envelope_base.osm"
    handoff = output_root / "step1_massing" / "organized_current" / "best_massing.json"
    if not (record_path.exists() and osm_path.exists() and handoff.exists()):
        return False
    record = read_json(record_path)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    return (record.get("handoff_ok") is True and record.get("sha256") == digest(osm_path)
            and record.get("step1_handoff_sha256") == digest(handoff))


def run_step2_energy_cases(
    repo: Path,
    output_root: Path,
    count: int,
    env: dict[str, str],
    log_dir: Path,
    condition: str = "full_feedback",
    proposal_cap: int | None = None,
) -> list[str]:
    # A resumed run keeps its verified base model: the reproduction writes a model with new bytes, and the
    # evidence of every finished Step 2 case is bound to the bytes of the base it started from.
    if verified_envelope_base(output_root):
        print("Step 2 base model already verified for the current Step 1 handoff; kept", flush=True)
        control_code = 0
    else:
        control_code = run_command(
            [sys.executable, "steps/step2_envelope/stage2_envelope_energyplus.py", "--verify-step1-handoff"],
            cwd=repo, env=env, log_path=log_dir / "step2_handoff_verification.log",
        )
    if control_code != 0:
        raise RuntimeError("Step 1/2 reproduction control failed; inspect step2_envelope_layout/handoff_verification.json before optimization.")
    valid_ids = scan_valid_step2_ids(output_root)[:count]
    attempted_signatures = existing_design_signatures(output_root, "step2")
    existing_case_dirs = list((output_root / "agent_candidates").glob(f"{STEP2_CASE_PREFIX}*"))
    existing_numbers = [
        int(path.name.removeprefix(STEP2_CASE_PREFIX))
        for path in existing_case_dirs
        if path.name.removeprefix(STEP2_CASE_PREFIX).isdigit()
    ]
    next_case_number = max(existing_numbers, default=0) + 1
    if valid_ids:
        print(f"Step 2 existing valid cases: {len(valid_ids)}/{count}", flush=True)
    attempts = 0
    prior_proposals = count_logged_proposals(output_root, "step2")
    max_attempts = (
        max(proposal_cap - prior_proposals, 0)
        if proposal_cap is not None
        else max((count - len(valid_ids)) * 5, 0)
    )
    while len(valid_ids) < count and attempts < max_attempts:
        attempts += 1
        case_id = f"{STEP2_CASE_PREFIX}{next_case_number + attempts - 1:03d}"
        print(f"\n=== Step 2 attempt {attempts}: {case_id} ===", flush=True)
        try:
            spec = ask_json(
                step2_energy_prompt(
                    repo, output_root, case_id, len(valid_ids), count, condition
                ),
                log_label=f"step2_envelope_{case_id}",
            )
        except Exception as exc:
            if "CLAUDE_CAPACITY_LIMIT" in str(exc):
                raise
            print(f"Qwen Step 2 JSON generation failed: {exc}", flush=True)
            continue
        spec = step2_spec_from_proposal(spec)
        signature = design_parameter_signature("step2", spec)
        if signature in attempted_signatures:
            print(f"Step 2 duplicate envelope script blocked before simulation: {case_id}", flush=True)
            continue
        attempted_signatures.add(signature)
        spec_path = agent_candidate_file(output_root, case_id, "envelope_spec.json")
        spec = materialize_script(spec, "envelope_script", spec_path.parent, STEP2_SCRIPT_FILE)
        write_json(spec_path, spec)
        code = run_command(
            [
                sys.executable,
                "steps/step2_envelope/stage2_envelope_energyplus.py",
                "--json-spec",
                str(spec_path),
                "--case-id",
                case_id,
                "--overwrite",
            ],
            cwd=repo,
            env=env,
            log_path=log_dir / f"{case_id}_energyplus.log",
        )
        feedback_path = step2_feedback_path(output_root, case_id)
        feedback = read_json(feedback_path) if feedback_path.exists() else {}
        is_valid = code == 0 and valid_step2_energy_case(output_root, case_id)
        append_attempt_event(
            output_root,
            {
                "event_type": "simulation_validation",
                "condition": condition,
                "stage": "step2",
                "attempt_id": case_id,
                "candidate_id": case_id,
                "outcome": "valid" if is_valid else "invalid",
                "command_exit_code": code,
                "physical_constraints_ok": feedback.get("physical_constraints_ok"),
                "selected_total_eui_kwh_m2": feedback.get("selected_total_eui_kwh_m2"),
                "error_category": None if is_valid else "energy_simulation_or_constraints",
                "evidence_path": str(feedback_path),
                "details": {"phase": "step2_energy", "feedback_status": feedback.get("status")},
            },
        )
        if is_valid:
            valid_ids.append(case_id)
            print(f"Step 2 valid cases: {len(valid_ids)}/{count}", flush=True)
        else:
            print(f"Step 2 case did not become valid: {case_id}", flush=True)
    if not valid_ids:
        raise RuntimeError(f"Produced no valid Step 2 cases after {attempts} attempts.")
    if len(valid_ids) < count and proposal_cap is None:
        raise RuntimeError(f"Only produced {len(valid_ids)} valid Step 2 cases after {attempts} attempts.")
    return valid_ids


def _git_text(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _ollama_model_artifact() -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(BASE_URL.removesuffix("/v1") + "/api/tags", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    for model in payload.get("models", []):
        if model.get("name") == MODEL or model.get("model") == MODEL:
            return {
                "tag": MODEL,
                "digest": model.get("digest"),
                "size_bytes": model.get("size"),
                "modified_at": model.get("modified_at"),
                "details": model.get("details") or {},
            }
    return None


def update_run_manifest(state: State, status: str) -> None:
    """Create or update the common, paper-facing run manifest."""
    repo = Path(state["repo"]).resolve()
    output_root = Path(state["output_root"]).resolve()
    manifest_path = output_root / "run_manifest.json"
    existing = _maybe_read_json(manifest_path)
    config = _maybe_read_json(repo / "config" / "block_m.json")
    invocations = existing.get("invocations") if isinstance(existing.get("invocations"), list) else []
    invocation_started_at = state["invocation_started_at"]
    if status == "running" and not any(row.get("started_at") == invocation_started_at for row in invocations):
        invocations.append(
            {
                "started_at": invocation_started_at,
                "resume": bool(existing),
                "counts_requested": {
                    "step1": state["step1_count"],
                    "step2": state["step2_count"],
                    "step3": state["step3_count"],
                },
                "proposal_caps": {
                    "step1": state["step1_proposal_cap"],
                    "step2": state["step2_proposal_cap"],
                    "step3": state["step3_proposal_cap"],
                },
                "fixed_step1_handoff": state["fixed_step1_handoff"] or None,
            }
        )
    git_status = _git_text(repo, "status", "--porcelain")
    manifest: dict[str, Any] = {
        "schema_id": "agents/run_manifest.schema.json",
        "manifest_version": "1.0",
        "experiment_protocol_version": EXPERIMENT_PROTOCOL_VERSION,
        "run_id": existing.get("run_id") or output_root.name,
        "status": status,
        "timestamp": existing.get("timestamp") or invocation_started_at,
        "location": state["location"],
        "model_provider": os.environ.get("AGENT_LLM_BACKEND", "ollama"),
        "model_id": MODEL,
        "model_artifact": existing.get("model_artifact") or _ollama_model_artifact(),
        "agent_harness": "langgraph",
        "harness_version": HARNESS_VERSION,
        "workflow_git_commit": _git_text(repo, "rev-parse", "HEAD") or None,
        "workflow_git_dirty": bool(git_status),
        "prompt_protocol_version": PROMPT_PROTOCOL_VERSION,
        "condition": state["condition"],
        "budget": {
            "primary_unit": "total_proposals",
            "proposal_cap": sum(
                cap
                for cap in (
                    state["step1_proposal_cap"],
                    state["step2_proposal_cap"],
                    state["step3_proposal_cap"],
                )
                if cap is not None
            ) or None,
            "stage_proposal_caps": {
                "step1": state["step1_proposal_cap"],
                "step2": state["step2_proposal_cap"],
                "step3": state["step3_proposal_cap"],
            },
            "simulation_call_cap": None,
        },
        "generation_settings": {
            "temperature": 0.7,
            "top_p": 0.9,
            "num_ctx": DEFAULT_NUM_CTX,
            "num_predict_default": DEFAULT_NUM_PREDICT,
            "seed": DEFAULT_SEED,
            "seed_policy": "base seed plus deterministic SHA-256 offset from stage/candidate call label",
            "think": True,
            "format": "json",
        },
        "counts_requested": {
            "step1": state["step1_count"],
            "step2": state["step2_count"],
            "step3": state["step3_count"],
        },
        "random_seed": DEFAULT_SEED,
        "simulation_settings": {
            "config": "config/block_m.json",
            "weather_file": config.get("weather_file"),
            "openstudio_exe": config.get("openstudio_exe"),
            "step1_fixed_wwr": STEP1_FIXED_WWR_VALUE,
            "step1_timestep_per_hour": 6,
            "step2_timestep_per_hour": int(os.environ.get("STEP2_TIMESTEP_PER_HOUR", "6")),
            "step3_timestep_per_hour": int(os.environ.get("STEP3_TIMESTEP_PER_HOUR", "6")),
        },
        "hardware": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
        },
        "started_at": existing.get("started_at") or invocation_started_at,
        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds") if status in {"completed", "failed"} else None,
        "invocations": invocations,
    }
    if status in {"completed", "failed"}:
        manifest["counts_valid"] = {
            "step1": len(state["step1_valid_ids"]),
            "step2": len(state["step2_valid_ids"]),
            "step3": len(state["step3_valid_ids"]),
        }
    write_json(manifest_path, manifest)


def create_context_node(state: State) -> State:
    repo = Path(state["repo"]).resolve()
    os.environ["EXPERIMENT_CONDITION"] = state["condition"]
    if state.get("output_root"):
        output_root = Path(state["output_root"]).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        existing_manifest_path = output_root / "run_manifest.json"
        if existing_manifest_path.exists():
            existing_manifest = read_json(existing_manifest_path)
            existing_condition = existing_manifest.get("condition")
            existing_seed = existing_manifest.get("random_seed")
            if existing_condition not in (None, state["condition"]):
                raise RuntimeError(
                    f"Refusing to resume {existing_condition!r} as {state['condition']!r}; use a new output root."
                )
            if existing_seed not in (None, DEFAULT_SEED):
                raise RuntimeError(
                    f"Refusing to resume seed {existing_seed!r} with QWEN_SEED={DEFAULT_SEED}; "
                    "restore the original seed or use a new output root."
                )
        os.environ["AUTOMATED_DESIGN_OUTPUT_ROOT"] = str(output_root)
        context_path = output_root / "active_project_context.json"
        if context_path.exists():
            context = read_json(context_path)
            if context.get("location") != state["location"]:
                raise RuntimeError(
                    f"Refusing to resume location {context.get('location')!r} as {state['location']!r}."
                )
            os.environ["AUTOMATED_DESIGN_PROJECT_CONTEXT"] = str(context_path)
        else:
            write_active_context(state, output_root)
        print(f"[All_Energy LangGraph] Resuming output root: {output_root}", flush=True)
        if state.get("fixed_step1_handoff"):
            install_fixed_step1_handoff(Path(state["fixed_step1_handoff"]).resolve(), output_root)
        updated_state = {**state, "output_root": str(output_root), "log_dir": str(output_root / "supervisor_logs")}
        update_run_manifest(updated_state, "running")
        return updated_state
    output_root = create_project_context(repo, state["location"], state["output_root_base"], state["user_requirements"])
    output_root.mkdir(parents=True, exist_ok=True)
    os.environ["AUTOMATED_DESIGN_OUTPUT_ROOT"] = str(output_root)
    write_active_context(state, output_root)
    if state.get("fixed_step1_handoff"):
        install_fixed_step1_handoff(Path(state["fixed_step1_handoff"]).resolve(), output_root)
    print(f"[All_Energy LangGraph] Active output root: {output_root}", flush=True)
    updated_state = {**state, "output_root": str(output_root), "log_dir": str(output_root / "supervisor_logs")}
    update_run_manifest(updated_state, "running")
    return updated_state


def step1_node(state: State) -> State:
    if state["step1_count"] <= 0:
        return state
    repo, output_root, log_dir = resolve_node_paths(state)
    env = env_for(output_root)
    valid_ids = generate_step1_energy_cases(
        repo, output_root, state["step1_count"], env, log_dir, state["condition"], state["step1_proposal_cap"]
    )
    best_id = select_step1_best_by_energy(repo, output_root, env, log_dir)
    return {**state, "step1_valid_ids": valid_ids, "step1_best_id": best_id}


def step2_node(state: State) -> State:
    if state["step2_count"] <= 0:
        return state
    repo, output_root, log_dir = resolve_node_paths(state)
    valid_ids = run_step2_energy_cases(
        repo, output_root, state["step2_count"], env_for(output_root), log_dir, state["condition"], state["step2_proposal_cap"]
    )
    best_id = select_step2_best(output_root)
    return {**state, "step2_valid_ids": valid_ids, "step2_best_id": best_id}


def step3_node(state: State) -> State:
    if state["step3_count"] <= 0:
        return state
    repo, output_root, log_dir = resolve_node_paths(state)
    generated_valid_ids = run_step3_cases(
        repo,
        output_root,
        state["step3_count"],
        env_for(output_root),
        log_dir,
        state["condition"],
        state["step3_proposal_cap"],
    )
    valid_ids = [case_id for case_id in generated_valid_ids if valid_step3_energy_case(output_root, case_id)]
    if not valid_ids:
        raise RuntimeError("Step 3 produced no supervisor-valid cases.")
    if len(valid_ids) < state["step3_count"] and state["step3_proposal_cap"] is None:
        raise RuntimeError(
            f"Step 3 helper returned {len(generated_valid_ids)} generated-valid cases, "
            f"but only {len(valid_ids)} passed supervisor feedback validation."
        )
    return {**state, "step3_valid_ids": valid_ids}


def _maybe_read_json(path: Path) -> dict[str, Any]:
    return read_json(path) if path.exists() else {}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_target_status(summary_state: State) -> dict[str, dict[str, int | bool]]:
    return {
        "step1": {
            "requested": summary_state["step1_count"],
            "valid": len(summary_state["step1_valid_ids"]),
            "met": summary_state["step1_count"] <= 0 or len(summary_state["step1_valid_ids"]) >= summary_state["step1_count"],
        },
        "step2": {
            "requested": summary_state["step2_count"],
            "valid": len(summary_state["step2_valid_ids"]),
            "met": summary_state["step2_count"] <= 0 or len(summary_state["step2_valid_ids"]) >= summary_state["step2_count"],
        },
        "step3": {
            "requested": summary_state["step3_count"],
            "valid": len(summary_state["step3_valid_ids"]),
            "met": summary_state["step3_count"] <= 0 or len(summary_state["step3_valid_ids"]) >= summary_state["step3_count"],
        },
    }


def require_valid_targets_met(
    target_status: dict[str, dict[str, int | bool]], proposal_caps: dict[str, int | None] | None = None
) -> None:
    proposal_caps = proposal_caps or {}
    missing = [
        f"{stage}: valid {status['valid']}/{status['requested']}"
        for stage, status in target_status.items()
        if not status["met"] and proposal_caps.get(stage) is None
    ]
    if missing:
        raise RuntimeError("Valid case target not met after feedback scan: " + "; ".join(missing))


def collect_energy_decision_records(output_root: Path, state: State) -> dict[str, Any]:
    """Summarize Qwen design choices and measured EUI for auditability."""
    step1_records: list[dict[str, Any]] = []
    for candidate_id in state["step1_valid_ids"]:
        spec = _maybe_read_json(agent_candidate_file(output_root, candidate_id, "massing_spec.json"))
        feedback = _maybe_read_json(step1_energy_feedback_path(output_root, candidate_id))
        step1_records.append(
            {
                "candidate_id": candidate_id,
                "qwen_description": spec.get("description"),
                "selected_total_eui_kwh_m2": feedback.get("selected_total_eui_kwh_m2"),
                "heating_eui_kwh_m2": feedback.get("heating_eui_kwh_m2"),
                "cooling_eui_kwh_m2": feedback.get("cooling_eui_kwh_m2"),
                "fan_eui_kwh_m2": feedback.get("fan_eui_kwh_m2"),
                "status": feedback.get("status"),
                "physical_constraints_ok": feedback.get("physical_constraints_ok"),
                "selected_as_best": candidate_id == state["step1_best_id"],
            }
        )

    step2_records: list[dict[str, Any]] = []
    for case_id in state["step2_valid_ids"]:
        spec = _maybe_read_json(agent_candidate_file(output_root, case_id, "envelope_spec.json"))
        feedback = _maybe_read_json(step2_feedback_path(output_root, case_id))
        step2_records.append(
            {
                "case_id": case_id,
                "qwen_description": spec.get("description"),
                "qwen_design_intent": spec.get("design_intent"),
                "envelope_script": spec.get("envelope_script"),
                "selected_total_eui_kwh_m2": feedback.get("selected_total_eui_kwh_m2"),
                "heating_eui_kwh_m2": feedback.get("heating_eui_kwh_m2"),
                "cooling_eui_kwh_m2": feedback.get("cooling_eui_kwh_m2"),
                "fan_eui_kwh_m2": feedback.get("fan_eui_kwh_m2"),
                "lighting_eui_kwh_m2": feedback.get("lighting_eui_kwh_m2"),
                "status": feedback.get("status"),
                "physical_constraints_ok": feedback.get("physical_constraints_ok"),
                "selected_as_best": case_id == state["step2_best_id"],
            }
        )

    step3_records: list[dict[str, Any]] = []
    for case_id in state["step3_valid_ids"]:
        spec = _maybe_read_json(agent_candidate_file(output_root, case_id, "hvac_spec.json"))
        feedback = _maybe_read_json(step3_feedback_path(output_root, case_id))
        tool_plan = spec.get("tool_plan") if isinstance(spec.get("tool_plan"), dict) else {}
        step3_records.append(
            {
                "case_id": case_id,
                "qwen_description": spec.get("description"),
                "qwen_design_intent": spec.get("design_intent"),
                "series_id": spec.get("series_id"),
                "uses_tool_plan": bool(tool_plan),
                "tool_plan_strategy_summary": tool_plan.get("strategy_summary") if tool_plan else None,
                "tool_plan_rationale": tool_plan.get("rationale") if tool_plan else None,
                "selected_total_eui_kwh_m2": feedback.get("selected_total_eui_kwh_m2"),
                "heating_eui_kwh_m2": feedback.get("heating_eui_kwh_m2"),
                "cooling_eui_kwh_m2": feedback.get("cooling_eui_kwh_m2"),
                "fan_eui_kwh_m2": feedback.get("fan_eui_kwh_m2"),
                "pump_eui_kwh_m2": feedback.get("pump_eui_kwh_m2"),
                "heat_rejection_eui_kwh_m2": feedback.get("heat_rejection_eui_kwh_m2"),
                "lighting_eui_kwh_m2": feedback.get("lighting_eui_kwh_m2"),
                "equipment_eui_kwh_m2": feedback.get("equipment_eui_kwh_m2"),
                "zone_condition_check_ok": feedback.get("zone_condition_check_ok"),
                "zone_temp_setpoint_unmet_hours": feedback.get("zone_temp_setpoint_unmet_hours"),
                "zone_temp_setpoint_unmet_pct": feedback.get("zone_temp_setpoint_unmet_pct"),
                "zone_temp_setpoint_match_pct_ventilation_hours": feedback.get(
                    "zone_temp_setpoint_match_pct_ventilation_hours"
                ),
                "zone_rh_max_pct": feedback.get("zone_rh_max_pct"),
                "zone_rh_hours_above_70": feedback.get("zone_rh_hours_above_70"),
                "zone_rh_pct_above_70": feedback.get("zone_rh_pct_above_70"),
                "zone_rh_below_70_pct_ventilation_hours": feedback.get(
                    "zone_rh_below_70_pct_ventilation_hours"
                ),
                "pmv_occupied_comfort_pct": feedback.get("pmv_occupied_comfort_pct"),
                "pmv_occupied_cold_pct": feedback.get("pmv_occupied_cold_pct"),
                "pmv_occupied_hot_pct": feedback.get("pmv_occupied_hot_pct"),
                "original_pmv_occupied_comfort_pct": feedback.get(
                    "original_pmv_occupied_comfort_pct"
                ),
                "severe_count": feedback.get("severe_count"),
                "fatal_count": feedback.get("fatal_count"),
                "status": feedback.get("status"),
                "physical_constraints_ok": feedback.get("physical_constraints_ok"),
                "selection_service_quality": check_hvac_selection_service_quality(feedback),
            }
        )
    valid_step3 = [
        row
        for row in step3_records
        if row.get("physical_constraints_ok")
        and _float_or_none(row.get("selected_total_eui_kwh_m2")) is not None
        and row["selection_service_quality"]["ok"]
    ]
    step3_best_id = ""
    if valid_step3:
        best_step3 = min(valid_step3, key=lambda row: float(row["selected_total_eui_kwh_m2"]))
        step3_best_id = str(best_step3["case_id"])
    for row in step3_records:
        row["selected_as_best"] = row["case_id"] == step3_best_id

    return {
        "selection_policy": {
            "step1": "Lowest selected_total_eui_kwh_m2 among physically valid fixed-envelope Step 1 massing cases.",
            "step2": "Lowest selected_total_eui_kwh_m2 among physically valid Step 2 envelope cases.",
            "step3": "Lowest selected_total_eui_kwh_m2 after physical validity and operational-service gates (valid occupied PMV evidence and a passing PMV baseline comparison).",
        },
        "step1": step1_records,
        "step2": step2_records,
        "step3": step3_records,
        "step3_best_id": step3_best_id,
    }


def summary_node(state: State) -> State:
    output_root = Path(state["output_root"]).resolve()
    previous_summary = _maybe_read_json(output_root / "supervisor_summary.json")
    summary_state = dict(state)
    summary_state["step1_valid_ids"] = scan_valid_step1_ids(output_root)
    if not summary_state["step1_best_id"]:
        best_step1 = _maybe_read_json(output_root / "step1_massing_energy" / "organized_current" / "best_massing_energy.json")
        summary_state["step1_best_id"] = previous_summary.get("step1_best_id", "") or str(best_step1.get("candidate_id") or "")
    summary_state["step2_valid_ids"] = scan_valid_step2_ids(output_root)
    if not summary_state["step2_best_id"]:
        best_step2 = _maybe_read_json(output_root / "step2_envelope_layout" / "organized_current" / "best_envelope.json")
        summary_state["step2_best_id"] = previous_summary.get("step2_best_id", "") or str(best_step2.get("case_id") or "")
    summary_state["step3_valid_ids"] = scan_valid_step3_ids(output_root)
    target_status = build_target_status(summary_state)
    require_valid_targets_met(
        target_status,
        {
            "step1": summary_state["step1_proposal_cap"],
            "step2": summary_state["step2_proposal_cap"],
            "step3": summary_state["step3_proposal_cap"],
        },
    )
    decision_records = collect_energy_decision_records(output_root, summary_state)
    summary = {
        "agent_shell": AGENT_SHELL,
        "model": MODEL_NAME,
        "output_root": str(output_root),
        "fixed_step1_handoff": summary_state["fixed_step1_handoff"],
        "step1_metric": "fixed-envelope timestep=6 selected_total_eui_kwh_m2",
        "step1_fixed_wwr": STEP1_FIXED_WWR_VALUE,
        "step1_valid_ids": summary_state["step1_valid_ids"],
        "step1_best_id": summary_state["step1_best_id"],
        "step2_valid_ids": summary_state["step2_valid_ids"],
        "step2_best_id": summary_state["step2_best_id"],
        "step3_valid_ids": summary_state["step3_valid_ids"],
        "step3_best_id": decision_records["step3_best_id"],
        "valid_case_targets": target_status,
        "selection_policy": decision_records["selection_policy"],
        "command_whitelist": COMMAND_WHITELIST,
    }
    write_json(output_root / "energy_decision_record.json", decision_records)
    write_json(output_root / "langgraph_all_energy_supervisor_summary.json", summary)
    write_json(output_root / "supervisor_summary.json", summary)
    update_run_manifest(summary_state, "completed")
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return state


def build_graph():
    graph = StateGraph(State)
    graph.add_node("create_context", create_context_node)
    graph.add_node("step1", step1_node)
    graph.add_node("step2", step2_node)
    graph.add_node("step3", step3_node)
    graph.add_node("summary", summary_node)
    graph.set_entry_point("create_context")
    graph.add_edge("create_context", "step1")
    graph.add_edge("step1", "step2")
    graph.add_edge("step2", "step3")
    graph.add_edge("step3", "summary")
    graph.add_edge("summary", END)
    return graph.compile()


def main() -> int:
    parser = argparse.ArgumentParser(description="LangGraph All_Energy Qwen/Ollama workflow supervisor.")
    parser.add_argument(
        "--repo",
        default=str(DEFAULT_REPO),
        help="Workflow repository path (defaults to the repository containing this agent).",
    )
    parser.add_argument("--location", default="hong_kong")
    parser.add_argument("--output-root-base", default=DEFAULT_OUTPUT_ROOT_BASE)
    parser.add_argument("--resume-output-root", help="Continue an existing output root instead of creating a new context.")
    parser.add_argument(
        "--fixed-step1-handoff",
        help="Copy a fixed Step 1 handoff folder into the active output root before running Step 2.",
    )
    parser.add_argument("--step1-count", type=int, default=20)
    parser.add_argument("--step2-count", type=int, default=10)
    parser.add_argument("--step3-count", type=int, default=10)
    parser.add_argument("--step1-proposal-cap", type=int)
    parser.add_argument("--step2-proposal-cap", type=int)
    parser.add_argument("--step3-proposal-cap", type=int)
    parser.add_argument("--condition", choices=MODEL_CONDITIONS, default="full_feedback")
    parser.add_argument("--user-requirements", default=DEFAULT_USER_REQUIREMENTS)
    args = parser.parse_args()
    for name in ("step1_proposal_cap", "step2_proposal_cap", "step3_proposal_cap"):
        value = getattr(args, name)
        if value is not None and value < 0:
            parser.error(f"--{name.replace('_', '-')} must be non-negative")
    state: State = {
        "repo": str(Path(args.repo).resolve()),
        "location": args.location,
        "output_root_base": args.output_root_base,
        "output_root": args.resume_output_root or "",
        "fixed_step1_handoff": args.fixed_step1_handoff or "",
        "user_requirements": args.user_requirements,
        "step1_count": args.step1_count,
        "step2_count": args.step2_count,
        "step3_count": args.step3_count,
        "step1_proposal_cap": args.step1_proposal_cap,
        "step2_proposal_cap": args.step2_proposal_cap,
        "step3_proposal_cap": args.step3_proposal_cap,
        "log_dir": "",
        "step1_valid_ids": [],
        "step1_best_id": "",
        "step2_valid_ids": [],
        "step2_best_id": "",
        "step3_valid_ids": [],
        "invocation_started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "condition": args.condition,
    }
    build_graph().invoke(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
