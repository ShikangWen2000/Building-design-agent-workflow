"""Summarize the runs written by run_agent_ablation_matrix.py.

Reads `run_manifest.json` and `attempt_log.jsonl` of every run under the matrix
root and writes, under <run-root>/matrix_summary (or --output):

- runs.csv: one row per run and stage (proposals, valid count and fraction,
  best EUI);
- conditions.csv: one row per condition and stage, aggregated over the seeds;
- comparisons.csv: pairwise condition comparison of the best EUI per stage
  (two-sided Mann-Whitney U and Cliff's delta);
- best_so_far.csv: best valid EUI after each proposal;
- summary.json: the same content.

Every LLM call counts as one proposal, valid or not.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import statistics
from pathlib import Path
from typing import Any

CONDITIONS = ("schema_only", "no_feedback", "full_feedback")
STAGES = ("step1", "step2", "step3")


def read_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def stage_record(events: list[dict[str, Any]], stage: str) -> dict[str, Any] | None:
    """Proposals of one stage in call order, each with the EUI of its valid simulation, if any."""
    valid_eui: dict[str, float] = {}
    for event in events:
        if (event.get("stage") == stage and event.get("event_type") == "simulation_validation"
                and event.get("outcome") == "valid" and event.get("selected_total_eui_kwh_m2") is not None):
            valid_eui[str(event.get("candidate_id"))] = float(event["selected_total_eui_kwh_m2"])
    proposals = [event for event in events if event.get("stage") == stage and event.get("event_type") == "llm_generation"]
    if not proposals:
        return None
    best = None
    best_case = None
    trajectory: list[float | None] = []
    counted: set[str] = set()
    for event in proposals:
        case_id = str(event.get("candidate_id"))
        # A regenerated proposal for the same case is a new call; the case result counts once.
        if event.get("outcome") == "parsed" and case_id in valid_eui and case_id not in counted:
            counted.add(case_id)
            if best is None or valid_eui[case_id] < best:
                best, best_case = valid_eui[case_id], case_id
        trajectory.append(best)
    return {
        "proposals": len(proposals), "valid": len(counted),
        "valid_fraction": round(len(counted) / len(proposals), 4),
        "unparsed": sum(1 for event in proposals if event.get("outcome") != "parsed"),
        "best_eui_kwh_m2": best, "best_case_id": best_case,
        "llm_seconds": round(sum(float(event.get("duration_seconds") or 0.0) for event in proposals), 1),
        "best_so_far": trajectory,
    }


def collect(run_root: Path) -> list[dict[str, Any]]:
    runs = []
    for manifest_path in sorted(run_root.glob("*/run_manifest.json")):
        log_path = manifest_path.parent / "attempt_log.jsonl"
        if not log_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        events = read_events(log_path)
        for stage in STAGES:
            record = stage_record(events, stage)
            if record:
                runs.append({"condition": manifest.get("condition"), "seed": manifest.get("random_seed"),
                             "run": manifest_path.parent.name, "stage": stage, **record})
    return runs


def condition_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for stage, condition in itertools.product(STAGES, CONDITIONS):
        group = [run for run in runs if run["stage"] == stage and run["condition"] == condition]
        if not group:
            continue
        bests = [run["best_eui_kwh_m2"] for run in group if run["best_eui_kwh_m2"] is not None]
        rows.append({
            "stage": stage, "condition": condition, "runs": len(group), "runs_with_valid_case": len(bests),
            "mean_valid_fraction": round(statistics.mean(run["valid_fraction"] for run in group), 4),
            "best_eui_mean": round(statistics.mean(bests), 4) if bests else None,
            "best_eui_sd": round(statistics.stdev(bests), 4) if len(bests) > 1 else None,
            "best_eui_min": min(bests) if bests else None, "best_eui_max": max(bests) if bests else None,
        })
    return rows


def cliffs_delta(a: list[float], b: list[float]) -> float:
    """Share of pairs with a > b minus the share with a < b; negative means a has the lower EUI."""
    pairs = [(x > y) - (x < y) for x in a for y in b]
    return round(sum(pairs) / len(pairs), 4)


def comparison_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from scipy.stats import mannwhitneyu
    except ImportError:  # the summary tables do not need scipy
        mannwhitneyu = None
    rows = []
    for stage in STAGES:
        for first, second in itertools.combinations(CONDITIONS, 2):
            a = [run["best_eui_kwh_m2"] for run in runs
                 if run["stage"] == stage and run["condition"] == first and run["best_eui_kwh_m2"] is not None]
            b = [run["best_eui_kwh_m2"] for run in runs
                 if run["stage"] == stage and run["condition"] == second and run["best_eui_kwh_m2"] is not None]
            if not a or not b:
                continue
            p_value = None
            if mannwhitneyu is not None:
                p_value = round(float(mannwhitneyu(a, b, alternative="two-sided").pvalue), 4)
            rows.append({"stage": stage, "condition_a": first, "condition_b": second, "n_a": len(a), "n_b": len(b),
                         "mann_whitney_p_two_sided": p_value, "cliffs_delta_a_vs_b": cliffs_delta(a, b)})
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    output = (args.output or run_root / "matrix_summary").resolve()
    output.mkdir(parents=True, exist_ok=True)

    runs = collect(run_root)
    if not runs:
        raise SystemExit(f"No run with an attempt log found under {run_root}")
    conditions = condition_rows(runs)
    comparisons = comparison_rows(runs)
    write_csv(output / "runs.csv", runs, ["condition", "seed", "run", "stage", "proposals", "valid", "valid_fraction",
                                          "unparsed", "best_eui_kwh_m2", "best_case_id", "llm_seconds"])
    write_csv(output / "conditions.csv", conditions, list(conditions[0]))
    if comparisons:
        write_csv(output / "comparisons.csv", comparisons, list(comparisons[0]))
    write_csv(output / "best_so_far.csv", [
        {"condition": run["condition"], "seed": run["seed"], "stage": run["stage"],
         "proposal_index": index, "best_eui_kwh_m2": value}
        for run in runs for index, value in enumerate(run["best_so_far"], start=1)
    ], ["condition", "seed", "stage", "proposal_index", "best_eui_kwh_m2"])
    (output / "summary.json").write_text(json.dumps(
        {"runs": runs, "conditions": conditions, "comparisons": comparisons}, indent=2), encoding="utf-8")
    for row in conditions:
        print(row)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
