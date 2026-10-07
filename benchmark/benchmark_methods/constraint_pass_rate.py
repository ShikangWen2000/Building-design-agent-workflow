"""Measure how often designs of the active benchmark space pass the Step 2 constraints.

Random genomes of the active space (STEP2_BENCHMARK_SPACE) are decoded and
checked with the stage's own constraint pre-check; nothing is simulated. The
output lists, per design, the failed checks with requested and realized WWR,
and a summary by failed check, window type and shading type. It is the
evidence behind the ranges of `valid_range_v3` in envelope_search_space.py.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from envelope_search_space import SEARCH_SPACE_VERSION, decode_genome, normalize_genome, random_genome
from precheck_step2_case import precheck_rejection
from run_one_llm_step2_case import read_json, rebind_copied_step1_geometry_evidence, write_json


def failed_checks(row: dict[str, Any]) -> dict[str, Any]:
    try:
        report = json.loads(row.get("physical_constraints_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return {check["name"]: check.get("data") for check in report.get("checks") or []
            if not check.get("ok", True) and check.get("severity") == "error"}


def run_worker(index: int, designs: list[tuple[str, dict[str, Any]]], args: argparse.Namespace) -> list[dict[str, Any]]:
    root = args.probe_root / f"worker_{index:02d}"
    workflow_root = root / "workflow_output"
    step1_target = workflow_root / "step1_massing"
    if not step1_target.exists():
        shutil.copytree(args.step1_root, step1_target)
    rebind_copied_step1_geometry_evidence(args.step1_root, step1_target)
    context = read_json(args.project_context)
    context["output_root"] = str(workflow_root)
    context_path = root / "config" / "project_context.json"
    write_json(context_path, context)
    env = os.environ.copy()
    env.update({"AUTOMATED_DESIGN_PROJECT_CONTEXT": str(context_path), "AUTOMATED_DESIGN_OUTPUT_ROOT": str(workflow_root),
                "AUTOMATED_DESIGN_ALLOWED_OUTPUT_ROOT": str(args.probe_root.parent), "PYTHONDONTWRITEBYTECODE": "1"})
    records = []
    for case_id, genome in designs:
        spec = decode_genome(genome, method="probe", candidate_index=len(records) + 1, case_id=case_id)
        spec_path = root / "specs" / f"{case_id}.json"
        write_json(spec_path, spec)
        log_path = root / "logs" / f"{case_id}.log"
        rejected = precheck_rejection(spec_path, case_id, env, log_path)
        verdict = read_json(log_path.with_suffix(".json")) if log_path.with_suffix(".json").exists() else {}
        records.append({
            "case_id": case_id, "passed": verdict.get("passed"), "error": verdict.get("error"),
            "failed_checks": failed_checks(rejected or {}),
            "window_types": spec["orientation_window_type"], "orientation_wwr": spec["orientation_wwr"],
            "shading_types": {system["orientations"][0]: system["type"] for system in spec["shading_systems"]},
            "genome": genome,
        })
        # The exported models are not needed once the verdict is recorded.
        for folder in (workflow_root / "step2_envelope_layout" / "osm_cases", workflow_root / "osm_exports"):
            for path in folder.glob(f"*{case_id}*"):
                shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-root", type=Path, required=True)
    parser.add_argument("--step1-root", type=Path, required=True)
    parser.add_argument("--project-context", type=Path, required=True)
    parser.add_argument("--count", type=int, default=48)
    parser.add_argument("--seed", type=int, default=20261002)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    args.probe_root = args.probe_root.resolve()
    args.step1_root = args.step1_root.resolve()
    args.project_context = args.project_context.resolve()
    args.probe_root.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    designs = [(f"probe_{index:03d}", normalize_genome(random_genome(rng))) for index in range(1, args.count + 1)]
    shares = [designs[worker::args.workers] for worker in range(args.workers)]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda item: run_worker(item[0], item[1], args), enumerate(shares)))
    records = sorted((record for share in results for record in share), key=lambda record: record["case_id"])

    with (args.probe_root / "probe_results.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    decided = [record for record in records if record["passed"] is not None]
    by_check = collections.Counter(name for record in decided for name in record["failed_checks"])
    by_window: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    by_shading: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for record in decided:
        mismatched = set((record["failed_checks"].get("realized_orientation_wwr_matches_request") or {}).get("mismatches") or {})
        for orientation, window_type in record["window_types"].items():
            by_window[window_type][0] += 1
            by_window[window_type][1] += orientation in mismatched
        for shading in set(record["shading_types"].values()) or {"none"}:
            by_shading[shading][0] += 1
            by_shading[shading][1] += not record["passed"]
    summary = {
        "search_space": SEARCH_SPACE_VERSION, "seed": args.seed, "designs": len(records),
        "passed": sum(1 for record in decided if record["passed"]), "inconclusive": len(records) - len(decided),
        "failed_checks": dict(by_check),
        "facades_by_window_type": {name: {"facades": total, "wwr_mismatch": bad} for name, (total, bad) in by_window.items()},
        "designs_by_shading_type_used": {name: {"designs": total, "rejected": bad} for name, (total, bad) in by_shading.items()},
    }
    write_json(args.probe_root / "probe_summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
