"""Engineering functions and CLI used by the real MCP stdio server.

Run ``python -m hvac_toolkit.mcp_server`` for MCP clients; this module retains
the same operations as a convenient human/debugging CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
WORKSPACE_ROOT = HERE.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
SHARED_DIR = WORKSPACE_ROOT / "steps" / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

from project_context import load_active_config  # noqa: E402
from hvac_toolkit.schema import normalize_tool_plan, validate_tool_plan  # noqa: E402
from audit_contract import eligible as result_eligible
from physical_constraints import check_hvac_selection_service_quality

def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (WORKSPACE_ROOT / p).resolve()


def _print_json(payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if isinstance(payload, dict):
        if (payload.get("ok") is False or
                ("status" in payload and payload["status"] not in {"completed", "applied"}) or
                ("eligible_count" in payload and payload["eligible_count"] == 0)):
            raise SystemExit(1)


def inspect_osm_hvac_topology(osm_path: str | Path) -> dict[str, Any]:
    config = load_active_config()
    openstudio = Path(config["openstudio_exe"])
    input_osm = _resolve(osm_path)
    env = os.environ.copy()
    env["INPUT_OSM"] = str(input_osm)
    proc = subprocess.run(
        [str(openstudio), "execute_ruby_script", str(HERE / "ruby" / "extract_hvac_topology.rb")],
        cwd=str(WORKSPACE_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
    )
    if proc.returncode != 0:
        return {"ok": False, "errors": ["inspect_failed"], "log_tail": proc.stdout[-4000:]}
    payload = json.loads(proc.stdout)
    payload["ok"] = True
    return payload


def validate_hvac_tool_plan(plan: dict[str, Any]) -> dict[str, Any]:
    validation = validate_tool_plan(plan).as_dict()
    validation["normalized_plan"] = normalize_tool_plan(plan) if validation["ok"] else None
    return validation


def apply_hvac_tool_plan(plan: dict[str, Any], input_osm: str | Path, output_root: str | Path) -> dict[str, Any]:
    from hvac_toolkit.runner import apply_tool_plan
    return apply_tool_plan(plan, _resolve(input_osm), _resolve(output_root))


def simulate_hvac_case(
    *,
    case_id: str,
    osm_path: str | Path,
    output_root: str | Path,
    plan: dict[str, Any],
    timesteps_per_hour: int | None = None,
) -> dict[str, Any]:
    from hvac_toolkit.runner import run_simulation, verify_idf_tool_effects
    if str(plan.get("case_id")) != case_id:
        raise ValueError("Simulation case_id must match the verified HVAC tool plan case_id")
    result = run_simulation(case_id, _resolve(osm_path), _resolve(output_root), timesteps_per_hour=timesteps_per_hour)
    verification = verify_idf_tool_effects(plan, Path(result["run_dir"]))
    result["tool_effect_verification"] = verification
    if not verification["ok"]:
        result["status"] = "tool_effect_verification_failed"
    return result


def run_hvac_tool_plan(plan: dict[str, Any], input_osm: str | Path, output_root: str | Path) -> dict[str, Any]:
    case_id = str(plan.get("case_id") or "hvac_toolkit_case")
    applied = apply_hvac_tool_plan(plan, input_osm, output_root)
    payload: dict[str, Any] = {"case_id": case_id, "applied": applied}
    if applied.get("status") != "applied" or not applied.get("osm_path"):
        payload["status"] = "apply_failed"
        return payload
    result = simulate_hvac_case(case_id=case_id, osm_path=applied["osm_path"], output_root=output_root, plan=plan)
    payload["result"] = result
    payload["status"] = result.get("status")
    return payload


def rank_hvac_candidates(results: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = []
    rejected = []
    for row in results:
        result = row.get("result", row)
        verification = result.get("tool_effect_verification") or {}
        service_quality = check_hvac_selection_service_quality(result)
        if result_eligible(result) and verification.get("ok") is True and service_quality["ok"]:
            eligible.append(row)
        else:
            rejected.append({**row, "selection_service_quality": service_quality})
    best = min(
        eligible,
        key=lambda row: float(row.get("result", row)["selected_total_eui_kwh_m2"]),
        default=None,
    )
    return {"best": best, "eligible_count": len(eligible), "rejected_count": len(rejected), "eligible": eligible, "rejected": rejected}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audited JSON HVAC workflow tools (CLI companion to mcp_server).")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_p = sub.add_parser("inspect")
    inspect_p.add_argument("--input-osm", required=True)

    validate_p = sub.add_parser("validate")
    validate_p.add_argument("--plan-file", required=True)

    apply_p = sub.add_parser("apply")
    apply_p.add_argument("--plan-file", required=True)
    apply_p.add_argument("--input-osm", required=True)
    apply_p.add_argument("--output-root", required=True)

    simulate_p = sub.add_parser("simulate")
    simulate_p.add_argument("--case-id", required=True)
    simulate_p.add_argument("--osm-path", required=True)
    simulate_p.add_argument("--output-root", required=True)
    simulate_p.add_argument("--plan-file", required=True)
    simulate_p.add_argument("--timesteps-per-hour", type=int, choices=[6], default=6)

    run_p = sub.add_parser("run-plan")
    run_p.add_argument("--plan-file", required=True)
    run_p.add_argument("--input-osm", required=True)
    run_p.add_argument("--output-root", required=True)

    rank_p = sub.add_parser("rank")
    rank_p.add_argument("--results-file", required=True)

    args = parser.parse_args()
    if args.command == "inspect":
        _print_json(inspect_osm_hvac_topology(args.input_osm))
    elif args.command == "validate":
        plan = json.loads(_resolve(args.plan_file).read_text(encoding="utf-8"))
        _print_json(validate_hvac_tool_plan(plan))
    elif args.command == "apply":
        plan = json.loads(_resolve(args.plan_file).read_text(encoding="utf-8"))
        _print_json(apply_hvac_tool_plan(plan, args.input_osm, args.output_root))
    elif args.command == "simulate":
        plan = json.loads(_resolve(args.plan_file).read_text(encoding="utf-8"))
        _print_json(
            simulate_hvac_case(
                case_id=args.case_id,
                osm_path=args.osm_path,
                output_root=args.output_root,
                plan=plan,
                timesteps_per_hour=args.timesteps_per_hour,
            )
        )
    elif args.command == "run-plan":
        plan = json.loads(_resolve(args.plan_file).read_text(encoding="utf-8"))
        _print_json(run_hvac_tool_plan(plan, args.input_osm, args.output_root))
    elif args.command == "rank":
        rows = json.loads(_resolve(args.results_file).read_text(encoding="utf-8"))
        _print_json(rank_hvac_candidates(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
