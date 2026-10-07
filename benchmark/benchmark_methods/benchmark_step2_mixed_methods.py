from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from envelope_search_space import (
    crossover_genomes,
    decode_genome,
    genome_to_vector,
    mutate_genome,
    normalize_genome,
    random_genome,
    search_space_metadata,
    space_filling_genome,
    vector_to_genome,
)
from precheck_step2_case import precheck_rejection
from run_one_llm_step2_case import prune_large_outputs, rebind_copied_step1_geometry_evidence


REPO_ROOT = Path(__file__).resolve().parents[1]
METHODS = ("genetic_algorithm", "random_search", "space_filling_search", "bayesian_optimization")
PREFIXES = {
    "genetic_algorithm": "ga",
    "random_search": "random",
    "space_filling_search": "space",
    "bayesian_optimization": "bo",
}
# Valid evaluations required before the Gaussian-process surrogate replaces the
# space-filling warm-start used to seed Bayesian optimization.
BAYESIAN_INIT_VALID = 8
# After this many consecutive constraint-rejected proposals, fall back to a
# random diversity-injection draw so the surrogate does not spend the remaining
# fixed proposal budget against one infeasible boundary.
BAYESIAN_DIVERSITY_AFTER = 5


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def score(row: dict[str, Any]) -> float:
    try:
        value = float(row.get("selected_total_eui_kwh_m2") or 999999.0)
    except (TypeError, ValueError):
        return 999999.0
    if row.get("status") != "completed":
        return 999999.0
    if str(row.get("physical_constraints_ok", "")).lower() not in ("true", "1"):
        return 999999.0
    if int(float(row.get("fatal_count") or 0)):
        return 999999.0
    return value


class Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.run_root = Path(args.run_root).resolve()
        self.step1_root = Path(args.step1_root).resolve()
        self.proposal_budget = int(args.proposal_budget)
        self.repeat_index = int(args.repeat_index)
        self.seed = int(args.seed)
        self.timeout = int(args.timeout)
        self.run_interface = bool(args.run_interface)
        self.skip_precheck = bool(args.skip_precheck)
        self.bench_root = self.run_root / "benchmark_methods"
        self.status_path = self.bench_root / "benchmark_status.json"
        self.base_context = self._load_hong_kong_context(Path(args.project_context).resolve())

    def _load_hong_kong_context(self, path: Path) -> dict[str, Any]:
        payload = read_json(path)
        if not payload:
            raise FileNotFoundError(f"Project context not found: {path}")
        if payload.get("location") != "hong_kong":
            raise ValueError("This benchmark is restricted to location='hong_kong'.")
        return payload

    def update_status(self, **values: Any) -> None:
        payload = read_json(self.status_path, {}) or {}
        payload.update(values)
        payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        write_json(self.status_path, payload)

    def method_root(self, method: str) -> Path:
        return self.bench_root / method

    def prepare_method(self, method: str) -> tuple[Path, Path, Path]:
        root = self.method_root(method)
        workflow_root = root / "workflow_output"
        for sub in ("source", "configs", "generated_candidates", "logs", "summaries"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        target_step1 = workflow_root / "step1_massing"
        if not target_step1.exists():
            shutil.copytree(self.step1_root, target_step1)
        # Step 2 accepts the handoff only when its evidence binds the geometry
        # file of this method's own output root, as in the LLM runner.
        rebind_copied_step1_geometry_evidence(self.step1_root, target_step1)
        context = dict(self.base_context)
        context["output_root"] = str(workflow_root)
        context_path = root / "configs" / "project_context.json"
        write_json(context_path, context)
        shutil.copy2(Path(__file__), root / "source" / Path(__file__).name)
        shutil.copy2(
            Path(__file__).with_name("envelope_search_space.py"),
            root / "source" / "envelope_search_space.py",
        )
        shutil.copy2(
            Path(__file__).with_name("bayesian_optimization.py"),
            root / "source" / "bayesian_optimization.py",
        )
        write_json(
            root / "configs" / "method_config.json",
            {
                "method": method,
                "location": "hong_kong",
                "proposal_budget": self.proposal_budget,
                "repeat_index": self.repeat_index,
                "seed": self.seed,
                "fixed_step1_handoff": str(self.step1_root),
                "workflow_output_root": str(workflow_root),
                "shared_search_space": search_space_metadata(),
                "budget_rule": (
                    "Each method stops after the same number of proposals. Interface rejection, "
                    "simulation failure, timeout, and successful simulation all consume one proposal."
                ),
            },
        )
        return root, workflow_root, context_path

    def run_command(self, cmd: list[str], env: dict[str, str], log_path: Path, timeout: int) -> int:
        started = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            code, output = result.returncode, result.stdout
        except subprocess.TimeoutExpired as exc:
            code = 124
            output = (exc.stdout or "") + f"\nTIMEOUT after {timeout}s\n"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "COMMAND: "
            + " ".join(cmd)
            + f"\nELAPSED_SECONDS: {time.time() - started:.2f}\n"
            + output,
            encoding="utf-8",
            errors="replace",
        )
        return code

    def result_row(self, workflow_root: Path, case_id: str) -> dict[str, Any]:
        results = workflow_root / "step2_envelope_layout" / "envelope_eui_results.csv"
        for row in read_rows(results):
            if row.get("case_id") == case_id:
                return row
        return {"case_id": case_id, "status": "missing_result"}

    def existing_evaluations(self, root: Path) -> list[dict[str, Any]]:
        path = root / "summaries" / "evaluations.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def evaluate(
        self,
        method: str,
        generated_index: int,
        valid_index: int,
        genome: dict[str, Any],
        root: Path,
        workflow_root: Path,
        context_path: Path,
        note: str,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        case_id = f"{PREFIXES[method]}_{generated_index:03d}"
        spec = decode_genome(
            genome,
            method=method,
            candidate_index=generated_index,
            case_id=case_id,
        )
        candidate_dir = root / "generated_candidates" / case_id
        spec_path = candidate_dir / "design_spec.json"
        write_json(spec_path, spec)
        write_json(
            candidate_dir / "candidate_metadata.json",
            {
                "case_id": case_id,
                "method": method,
                "generated_index": generated_index,
                "target_valid_index": valid_index,
                "note": note,
                "genome": normalize_genome(genome),
            },
        )
        env = os.environ.copy()
        env["AUTOMATED_DESIGN_PROJECT_CONTEXT"] = str(context_path)
        env["AUTOMATED_DESIGN_OUTPUT_ROOT"] = str(workflow_root)
        env["AUTOMATED_DESIGN_ALLOWED_OUTPUT_ROOT"] = str(self.run_root.parent)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        interface_code = 0
        # A proposal the stage rejects before simulation is recorded from the
        # pre-check, without spending the coil-sizing run on it.
        rejected = None if self.skip_precheck else precheck_rejection(
            spec_path, case_id, env, root / "logs" / f"{case_id}_precheck.log")
        if rejected is None and self.run_interface:
            self.update_status(method=method, case_id=case_id, phase="interface")
            interface_code = self.run_command(
                [
                    sys.executable,
                    str(REPO_ROOT / "steps" / "step2_envelope" / "stage2_envelope_interface.py"),
                    "--json-spec",
                    str(spec_path),
                    "--case-id",
                    case_id,
                ],
                env,
                root / "logs" / f"{case_id}_interface.log",
                300,
            )
        energy_code = -1
        if rejected is None and interface_code == 0:
            self.update_status(method=method, case_id=case_id, phase="energyplus")
            energy_code = self.run_command(
                [
                    sys.executable,
                    str(REPO_ROOT / "steps" / "step2_envelope" / "stage2_envelope_energyplus.py"),
                    "--json-spec",
                    str(spec_path),
                    "--case-id",
                    case_id,
                    "--overwrite",
                ],
                env,
                root / "logs" / f"{case_id}_energyplus.log",
                self.timeout,
            )
        prune_large_outputs(workflow_root)
        row = rejected if rejected is not None else self.result_row(workflow_root, case_id)
        evaluation = {
            "case_id": case_id,
            "method": method,
            "precheck_rejected": rejected is not None,
            "generated_index": generated_index,
            "target_valid_index": valid_index,
            "interface_returncode": interface_code,
            "energyplus_returncode": energy_code,
            "score": score(row),
            "valid": score(row) < 999999.0,
            "result": row,
            "spec_path": str(spec_path),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "proposal_consumed": True,
        }
        with (root / "summaries" / "evaluations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(evaluation, ensure_ascii=False) + "\n")
        write_json(candidate_dir / "evaluation.json", evaluation)
        return evaluation

    def _next_ga_genome(
        self,
        rng: random.Random,
        valid_records: list[dict[str, Any]],
        valid_genomes: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        if len(valid_records) < 10:
            return random_genome(rng), "initial mixed-variable population"
        ranked = sorted(
            zip(valid_records, valid_genomes),
            key=lambda pair: float(pair[0].get("score", 999999.0)),
        )
        parents = [genome for _, genome in ranked[: min(8, len(ranked))]]
        if len(parents) >= 2 and rng.random() < 0.85:
            left, right = rng.sample(parents, 2)
            return mutate_genome(crossover_genomes(left, right, rng), rng), "categorical inheritance plus continuous crossover/mutation"
        return random_genome(rng), "diversity injection"

    def _next_bo_genome(
        self,
        rng: random.Random,
        generated_index: int,
        valid_records: list[dict[str, Any]],
        valid_genomes: list[dict[str, Any]],
        invalid_genomes: list[dict[str, Any]],
        failure_streak: int,
    ) -> tuple[dict[str, Any], str]:
        if len(valid_records) < BAYESIAN_INIT_VALID:
            # Index by the generated attempt so a rejected warm-start point is
            # never regenerated identically on the next try.
            genome = space_filling_genome(generated_index - 1, self.seed + self.repeat_index)
            return genome, "Bayesian optimization low-discrepancy warm-start"
        if failure_streak >= BAYESIAN_DIVERSITY_AFTER:
            # The acquisition optimizer is stuck against the feasibility boundary;
            # inject a random draw to recover a feasible reference point.
            return random_genome(rng), "Bayesian optimization diversity injection after constraint stall"
        # Imported lazily so that NumPy/SciPy are only required when this method runs.
        from bayesian_optimization import propose_next

        valid_vectors = [genome_to_vector(genome) for genome in valid_genomes]
        valid_scores = [float(record.get("score", 999999.0)) for record in valid_records]
        # Feed constraint-rejected candidates to the surrogate as penalty
        # observations (worse than any feasible design) so Expected Improvement
        # learns to avoid the infeasible shading-projection region instead of
        # repeatedly proposing into it.
        span = (max(valid_scores) - min(valid_scores)) or 1.0
        penalty_score = max(valid_scores) + span
        invalid_vectors = [genome_to_vector(genome) for genome in invalid_genomes if genome]
        vectors = valid_vectors + invalid_vectors
        scores = valid_scores + [penalty_score] * len(invalid_vectors)
        proposal = propose_next(vectors, scores, rng)
        return vector_to_genome(proposal), "Gaussian-process expected-improvement proposal (constraint-penalized)"

    def run_method(self, method: str) -> dict[str, Any]:
        root, workflow_root, context_path = self.prepare_method(method)
        existing = self.existing_evaluations(root)
        if existing and method == "random_search":
            raise RuntimeError("A random search cannot be continued; start it in an empty folder.")
        generated_index = len(existing)
        valid_records = [item for item in existing if item.get("valid")]
        valid_genomes = [
            read_json(root / "generated_candidates" / item["case_id"] / "candidate_metadata.json", {}).get("genome")
            for item in valid_records
        ]
        # Bayesian optimization additionally needs the rejected genomes (as
        # penalty observations) and the trailing constraint-failure streak so a
        # resumed run keeps avoiding the infeasible region it last stalled in.
        invalid_genomes: list[dict[str, Any]] = []
        failure_streak = 0
        if method == "bayesian_optimization":
            invalid_genomes = [
                read_json(root / "generated_candidates" / item["case_id"] / "candidate_metadata.json", {}).get("genome")
                for item in existing
                if not item.get("valid")
            ]
            for item in reversed(existing):
                if item.get("valid"):
                    break
                failure_streak += 1
        rng = random.Random(self.seed + self.repeat_index * 1000 + METHODS.index(method) * 100)
        # Fair primary budget: every proposal consumes budget, including an
        # interface rejection, simulation failure, or timeout.  Valid-only
        # replacement budgets systematically reward methods that propose more
        # infeasible candidates.
        while generated_index < self.proposal_budget:
            generated_index += 1
            if method == "random_search":
                genome, note = random_genome(rng), "independent uniform mixed-variable sample"
            elif method == "space_filling_search":
                genome = space_filling_genome(generated_index - 1, self.seed + self.repeat_index)
                note = "balanced categorical rotation with low-discrepancy continuous coordinates"
            elif method == "bayesian_optimization":
                genome, note = self._next_bo_genome(
                    rng, generated_index, valid_records, valid_genomes, invalid_genomes, failure_streak
                )
            else:
                genome, note = self._next_ga_genome(rng, valid_records, valid_genomes)
            evaluation = self.evaluate(
                method,
                generated_index,
                len(valid_records) + 1,
                genome,
                root,
                workflow_root,
                context_path,
                note,
            )
            if evaluation["valid"]:
                valid_records.append(evaluation)
                valid_genomes.append(normalize_genome(genome))
                failure_streak = 0
            else:
                failure_streak += 1
                if method == "bayesian_optimization":
                    invalid_genomes.append(normalize_genome(genome))
            self.write_method_summary(method, root, workflow_root)
        return self.write_method_summary(method, root, workflow_root)

    def write_method_summary(self, method: str, root: Path, workflow_root: Path) -> dict[str, Any]:
        evaluations = self.existing_evaluations(root)
        valid = [item for item in evaluations if item.get("valid")]
        best = min(valid, key=lambda item: float(item["score"])) if valid else None
        summary = {
            "method": method,
            "generated_candidate_count": len(evaluations),
            "valid_completed_count": len(valid),
            "invalid_candidate_count": len(evaluations) - len(valid),
            "proposal_budget": self.proposal_budget,
            "budget_reached": len(evaluations) >= self.proposal_budget,
            "valid_fraction": (len(valid) / len(evaluations)) if evaluations else 0.0,
            "total_elapsed_seconds": round(sum(float(item.get("elapsed_seconds") or 0.0) for item in evaluations), 3),
            "best_case_id": best.get("case_id") if best else None,
            "best_selected_total_eui_kwh_m2": best.get("score") if best else None,
            "results_csv": str(workflow_root / "step2_envelope_layout" / "envelope_eui_results.csv"),
        }
        write_json(root / "summaries" / "method_summary.json", summary)
        return summary

    def aggregate(self) -> None:
        summaries = []
        for method in METHODS:
            root = self.method_root(method)
            workflow_root = root / "workflow_output"
            if root.exists():
                summaries.append(self.write_method_summary(method, root, workflow_root))
        eligible = [item for item in summaries if item.get("best_selected_total_eui_kwh_m2") is not None]
        overall = min(eligible, key=lambda item: float(item["best_selected_total_eui_kwh_m2"])) if eligible else None
        write_json(
            self.bench_root / "benchmark_summary.json",
            {
                "location": "hong_kong",
                "shared_search_space": search_space_metadata(),
                "methods": summaries,
                "overall_best": overall,
            },
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hong Kong Step 2 benchmark over a shared hierarchical mixed-variable envelope space.")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--step1-root", required=True, help="Path to the selected Hong Kong step1_massing output folder.")
    parser.add_argument("--project-context", required=True, help="Hong Kong project_context.json used as the baseline template.")
    parser.add_argument("--methods", nargs="+", default=list(METHODS), choices=list(METHODS) + ["all"])
    parser.add_argument(
        "--proposal-budget", "--budget", dest="proposal_budget", type=int, default=50,
        help="Proposals per method. Every proposal consumes budget, including invalid and failed cases; --budget is a compatibility alias.",
    )
    parser.add_argument("--repeat-index", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260619)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument(
        "--run-interface",
        action="store_true",
        help="Also run the matplotlib preview interface before EnergyPlus. The EnergyPlus runner performs the authoritative validation.",
    )
    parser.add_argument("--skip-precheck", action="store_true",
                        help="Run the full stage for every proposal, including those it rejects before simulation.")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--skip-aggregate", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    benchmark = Benchmark(args)
    if args.aggregate_only:
        benchmark.aggregate()
        return 0
    methods = METHODS if "all" in args.methods else tuple(args.methods)
    benchmark.update_status(phase="starting", methods=list(methods))
    for method in methods:
        benchmark.update_status(phase="method_start", method=method)
        benchmark.run_method(method)
        benchmark.update_status(phase="method_complete", method=method)
    if not args.skip_aggregate:
        benchmark.aggregate()
    benchmark.update_status(phase="complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
