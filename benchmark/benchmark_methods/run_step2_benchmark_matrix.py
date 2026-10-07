"""Run the Step 2 optimizer and local-agent ablation matrix with bounded concurrency.

Each job is one method (or ablation mode) at one repeat/seed in its own output
root. Jobs already finished (summary file present) are skipped, so the matrix
can be resumed. The manifest records the repository commit and model digest.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OPTIMIZERS = ("genetic_algorithm", "random_search", "space_filling_search", "bayesian_optimization")
LLM_MODES = ("full_feedback", "no_simulation_feedback", "schema_only")
# Job groups of --methods. The two model groups need the model server; the others only simulate.
GROUPS = ("engineer_rule", "llm_genome", "llm_physical", "optimizers")



def tool_output(command: list[str], cwd: Path | None = None) -> str:
    """Output of a command; empty when the tool is not installed.

    A computer that runs only the simulation jobs needs neither git nor the
    model server.
    """
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True).stdout
    except OSError:
        return ""


def free_memory_gb() -> float | None:
    """Available physical memory, or None when psutil is not installed."""
    try:
        import psutil
    except ImportError:
        return None
    return psutil.virtual_memory().available / 2 ** 30


def parallel_limit(run_root: Path, default: int, groups: list[str] | None = None) -> int:
    """Parallel limit from <run-root>/matrix_control.json, so a running matrix can be re-paced.

    `max_parallel_<group>` applies to a launcher that runs that job group and
    takes precedence over `max_parallel`, which applies to every launcher.
    """
    try:
        control = json.loads((run_root / "matrix_control.json").read_text(encoding="utf-8"))
        keys = [f"max_parallel_{group}" for group in groups or []] + ["max_parallel"]
        return max(0, int(next(control[key] for key in keys if key in control)))
    except (OSError, ValueError, TypeError, StopIteration, AttributeError):
        return default


def jobs(args: argparse.Namespace) -> list[dict]:
    """Jobs of the selected groups and repeats, the engineer rule first, then repeat by repeat."""
    groups = set(getattr(args, "methods", None) or GROUPS)
    root = args.run_root / "llm_ablation" / "deterministic"
    out = [{
        "name": "engineer_rule", "group": "engineer_rule",
        "done": root / "engineer_rule" / "ablation_summary.json",
        "command": [sys.executable, str(HERE / "local_agent_ablation.py"), "--mode", "engineer_rule",
                    "--agent-root", str(root), "--step1-root", str(args.step1_root),
                    "--project-context", str(args.project_context), "--proposal-budget", str(args.budget),
                    "--simulation-timeout", str(args.simulation_timeout)],
    }]
    for repeat in range(getattr(args, "first_repeat", 1), args.repeats + 1):
        seed = 1000 * repeat
        # The model writes normalized genes (llm_ablation) or physical values (llm_physical).
        for folder, representation, mode in [(folder, representation, mode)
                                             for folder, representation in (("llm_ablation", "genome"),
                                                                            ("llm_physical", "physical"))
                                             for mode in (getattr(args, "llm_modes", None) or LLM_MODES)]:
            root = args.run_root / folder / f"seed{seed}"
            out.append({
                "name": f"{mode}{'_physical' if representation == 'physical' else ''}_seed{seed}",
                "group": f"llm_{representation}",
                "done": root / mode / "ablation_summary.json",
                "command": [sys.executable, str(HERE / "local_agent_ablation.py"), "--mode", mode,
                            "--representation", representation,
                            "--backend", "ollama", "--model", args.model,
                            "--agent-root", str(root), "--step1-root", str(args.step1_root),
                            "--project-context", str(args.project_context),
                            "--proposal-budget", str(args.budget), "--seed", str(seed),
                            # Several runs share one model server; allow for the queue.
                            "--model-timeout", "3600",
                            "--simulation-timeout", str(args.simulation_timeout)],
            })
        for method in OPTIMIZERS:
            root = args.run_root / "optimizers" / f"{method}_r{repeat}"
            out.append({
                "name": f"{method}_r{repeat}", "group": "optimizers",
                "done": root / "benchmark_methods" / "benchmark_summary.json",
                "command": [sys.executable, str(HERE / "benchmark_step2_mixed_methods.py"),
                            "--run-root", str(root), "--step1-root", str(args.step1_root),
                            "--project-context", str(args.project_context), "--methods", method,
                            "--proposal-budget", str(args.budget), "--repeat-index", str(repeat),
                            "--timeout", str(args.simulation_timeout)],
            })
    return [job for job in out if job["group"] in groups]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--step1-root", type=Path, required=True)
    parser.add_argument("--project-context", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.8:latest")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--budget", type=int, default=50)
    parser.add_argument("--max-parallel", type=int, default=6)
    parser.add_argument("--methods", nargs="+", choices=GROUPS, default=list(GROUPS),
                        help="Job groups to run; the model groups can be started separately from the simulation-only ones.")
    parser.add_argument("--first-repeat", type=int, default=1, help="Run repeats from this one on.")
    parser.add_argument("--llm-modes", nargs="+", choices=LLM_MODES, default=list(LLM_MODES),
                        help="Model conditions of the llm_genome and llm_physical groups.")
    parser.add_argument("--min-free-memory-gb", type=float, default=8.0,
                        help="Start no new job while less physical memory than this is available.")
    parser.add_argument("--min-free-disk-gb", type=float, default=25.0,
                        help="Start no new job while the drive of --run-root has less free space than this.")
    parser.add_argument("--search-space", default=os.environ.get("STEP2_BENCHMARK_SPACE", "valid_range_v3"),
                        help="Decoder shared by every method: valid_range_v3 or hierarchical_mixed_v2.")
    parser.add_argument("--simulation-timeout", type=int, default=5400,
                        help="Seconds per proposal; a design with many windows and boards simulates slowly under load.")
    args = parser.parse_args()
    args.run_root.mkdir(parents=True, exist_ok=True)
    logs = args.run_root / "logs"
    logs.mkdir(exist_ok=True)

    commit = tool_output(["git", "rev-parse", "HEAD"], REPO).strip()
    dirty = bool(tool_output(["git", "status", "--porcelain"], REPO).strip())
    digest = tool_output(["ollama", "show", args.model, "--modelfile"])
    manifest_path = args.run_root / "matrix_manifest.json"
    try:
        launches = json.loads(manifest_path.read_text(encoding="utf-8")).get("launches", [])
    except (OSError, ValueError):
        launches = []
    launch = {
        "started": datetime.now().isoformat(timespec="seconds"), "repository_commit": commit,
        "working_tree_dirty": dirty, "model": args.model,
        "model_from_line": next((line for line in digest.splitlines() if line.startswith("FROM")), None),
        "search_space": args.search_space,
        "repeats": args.repeats, "proposal_budget": args.budget, "max_parallel": args.max_parallel,
        "step1_root": str(args.step1_root), "project_context": str(args.project_context),
        "methods": list(args.methods), "first_repeat": args.first_repeat,
        "jobs": [job["name"] for job in jobs(args)],
    }
    # The top level describes the latest launch; "launches" keeps every launch of this matrix.
    manifest_path.write_text(json.dumps({**launch, "launches": [*launches, launch]}, indent=2), encoding="utf-8")

    pending = [job for job in jobs(args) if not job["done"].exists()]
    running: dict[str, tuple[subprocess.Popen, dict, float]] = {}
    status_path = args.run_root / "matrix_status.json"
    finished: dict[str, dict] = {}
    while pending or running:
        started_this_pass = 0
        # A few jobs per pass, so the memory check sees the load of the jobs just started.
        while pending and started_this_pass < 4 and len(running) < parallel_limit(args.run_root, args.max_parallel, args.methods):
            memory = free_memory_gb()
            if running and memory is not None and memory < args.min_free_memory_gb:
                break
            if shutil.disk_usage(args.run_root).free / 2 ** 30 < args.min_free_disk_gb:
                break
            started_this_pass += 1
            job = pending.pop(0)
            log = open(logs / f"{job['name']}.log", "a", encoding="utf-8")
            process = subprocess.Popen(job["command"], cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                                       env={**os.environ, "STEP2_BENCHMARK_SPACE": args.search_space})
            running[job["name"]] = (process, job, time.time())
            print(f"{datetime.now():%H:%M} start {job['name']}", flush=True)
        for name, (process, job, started) in list(running.items()):
            if process.poll() is not None:
                finished[name] = {"returncode": process.returncode, "hours": round((time.time() - started) / 3600, 2),
                                  "complete": job["done"].exists()}
                del running[name]
                print(f"{datetime.now():%H:%M} end {name} rc={process.returncode} complete={job['done'].exists()}", flush=True)
        status_path.write_text(json.dumps({"running": sorted(running), "pending": [j["name"] for j in pending],
                                           "finished": finished}, indent=2), encoding="utf-8")
        time.sleep(20)
    print("MATRIX_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
