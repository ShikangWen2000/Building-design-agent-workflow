"""Summarize a Step 2 benchmark matrix written by run_step2_benchmark_matrix.py.

Reads every optimizer repeat and every local-agent ablation run under the
matrix root and writes, under <run-root>/matrix_summary (or --output):

- runs.csv: one row per run (proposals, valid count and fraction, best EUI);
- methods.csv: one row per method or mode, aggregated over its runs;
- comparisons.csv: pairwise comparison of the methods' best EUI and valid
  fraction per run (two-sided Mann-Whitney U and Cliff's delta);
- best_so_far.csv: best valid EUI after each proposal, one row per run and
  proposal index;
- summary.json: the same content.

Every proposal counts, valid or not, as in the runners.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import re
import statistics
from pathlib import Path
from typing import Any

OPTIMIZERS = ("genetic_algorithm", "random_search", "space_filling_search", "bayesian_optimization")
LLM_MODES = ("full_feedback", "no_simulation_feedback", "schema_only", "engineer_rule")
# The same model modes with proposals written in physical values instead of normalized genes.
PHYSICAL_MODES = tuple(f"{mode}_physical" for mode in LLM_MODES[:3])
METHODS = (*OPTIMIZERS, *LLM_MODES[:3], *PHYSICAL_MODES, "engineer_rule")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_record(method: str, run: str, proposals: list[dict[str, Any]], source: Path) -> dict[str, Any]:
    """proposals: ordered evaluations with `valid`, `score`, `case_id` and `elapsed_seconds`."""
    best = None
    best_case = None
    trajectory: list[float | None] = []
    for item in proposals:
        if item.get("valid") and item.get("score") is not None and (best is None or float(item["score"]) < best):
            best, best_case = float(item["score"]), item.get("case_id")
        trajectory.append(best)
    valid = sum(1 for item in proposals if item.get("valid"))
    return {
        "method": method, "run": run, "proposals": len(proposals), "valid": valid,
        "valid_fraction": round(valid / len(proposals), 4) if proposals else 0.0,
        "best_eui_kwh_m2": best, "best_case_id": best_case,
        "simulation_hours": round(sum(float(item.get("elapsed_seconds") or 0.0) for item in proposals) / 3600.0, 2),
        "source": str(source), "best_so_far": trajectory,
    }


def collect(run_root: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for path in sorted((run_root / "optimizers").glob("*/benchmark_methods/*/summaries/evaluations.jsonl")):
        method = path.parents[1].name
        match = re.search(r"_r(\d+)$", path.parents[3].name)
        if method in OPTIMIZERS:
            runs.append(run_record(method, f"r{match.group(1)}" if match else path.parents[3].name, read_jsonl(path), path))
    for path in sorted([*(run_root / "llm_ablation").glob("*/*/ablation_evaluations.jsonl"),
                        *(run_root / "llm_physical").glob("*/*/ablation_evaluations.jsonl")]):
        mode = path.parent.name
        if mode not in LLM_MODES:
            continue
        if path.parents[2].name == "llm_physical":
            mode = f"{mode}_physical"
        proposals = [
            {**(row.get("evaluation") or {}), "case_id": row.get("case_id"),
             "elapsed_seconds": row.get("simulation_elapsed_seconds")}
            for row in read_jsonl(path)
        ]
        runs.append(run_record(mode, path.parents[1].name, proposals, path))
    return runs


def method_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for method in METHODS:
        group = [run for run in runs if run["method"] == method]
        if not group:
            continue
        bests = [run["best_eui_kwh_m2"] for run in group if run["best_eui_kwh_m2"] is not None]
        rows.append({
            "method": method, "runs": len(group), "runs_with_valid_case": len(bests),
            "mean_valid_fraction": round(statistics.mean(run["valid_fraction"] for run in group), 4),
            "best_eui_mean": round(statistics.mean(bests), 4) if bests else None,
            "best_eui_sd": round(statistics.stdev(bests), 4) if len(bests) > 1 else None,
            "best_eui_min": min(bests) if bests else None,
            "best_eui_max": max(bests) if bests else None,
        })
    return rows


def cliffs_delta(a: list[float], b: list[float]) -> float:
    """Share of pairs with a > b minus the share with a < b."""
    pairs = [(x > y) - (x < y) for x in a for y in b]
    return round(sum(pairs) / len(pairs), 4)


def comparison_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs without a valid case have no best EUI and are left out of that comparison."""
    try:
        from scipy.stats import mannwhitneyu
    except ImportError:  # the summary tables do not need scipy
        mannwhitneyu = None
    methods = [method for method in METHODS if any(run["method"] == method for run in runs)]
    rows = []
    for metric in ("best_eui_kwh_m2", "valid_fraction"):
        for first, second in itertools.combinations(methods, 2):
            a = [run[metric] for run in runs if run["method"] == first and run[metric] is not None]
            b = [run[metric] for run in runs if run["method"] == second and run[metric] is not None]
            if not a or not b:
                continue
            p_value = None
            if mannwhitneyu is not None and len(set(a + b)) > 1:
                p_value = round(float(mannwhitneyu(a, b, alternative="two-sided").pvalue), 4)
            rows.append({"metric": metric, "method_a": first, "method_b": second, "n_a": len(a), "n_b": len(b),
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
        raise SystemExit(f"No optimizer or ablation evaluations found under {run_root}")
    methods = method_rows(runs)
    write_csv(output / "runs.csv", runs, ["method", "run", "proposals", "valid", "valid_fraction",
                                          "best_eui_kwh_m2", "best_case_id", "simulation_hours", "source"])
    write_csv(output / "methods.csv", methods, list(methods[0]))
    comparisons = comparison_rows(runs)
    if comparisons:
        write_csv(output / "comparisons.csv", comparisons, list(comparisons[0]))
    write_csv(output / "best_so_far.csv", [
        {"method": run["method"], "run": run["run"], "proposal_index": index, "best_eui_kwh_m2": value}
        for run in runs for index, value in enumerate(run["best_so_far"], start=1)
    ], ["method", "run", "proposal_index", "best_eui_kwh_m2"])
    (output / "summary.json").write_text(json.dumps({"runs": runs, "methods": methods, "comparisons": comparisons}, indent=2), encoding="utf-8")
    for row in methods:
        print(row)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
