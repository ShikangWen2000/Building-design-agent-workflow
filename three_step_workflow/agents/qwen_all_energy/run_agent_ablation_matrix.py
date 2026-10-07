"""Run the three-step local-agent ablation: every condition at every seed.

Each job is one condition (`schema_only`, `no_feedback`, `full_feedback`) at one
seed in its own output root under --run-root. Every stage has a fixed proposal
budget: each LLM call consumes one proposal, valid or not, and the stage stops
when the budget is used. With --fixed-step1-handoff every job starts from the
same Step 1 design and runs Steps 2 and 3 only. A job that finished is skipped
on restart; an interrupted job resumes from its attempt log.
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
REPO = HERE.parents[1]
CONDITIONS = ("schema_only", "no_feedback", "full_feedback")
DONE = "ablation_job_done.json"



def free_memory_gb() -> float | None:
    """Available physical memory, or None when psutil is not installed."""
    try:
        import psutil
    except ImportError:
        return None
    return psutil.virtual_memory().available / 2 ** 30


def parallel_limit(run_root: Path, default: int) -> int:
    """`max_parallel` from <run-root>/matrix_control.json when that file sets it, so a running matrix can be re-paced."""
    try:
        return max(0, int(json.loads((run_root / "matrix_control.json").read_text(encoding="utf-8"))["max_parallel"]))
    except (OSError, ValueError, KeyError, TypeError):
        return default


def jobs(args: argparse.Namespace) -> list[dict]:
    out = []
    for seed in args.seeds:
        for condition in getattr(args, "conditions", None) or CONDITIONS:
            root = args.run_root / f"{condition}_seed{seed}"
            if args.fixed_step1_handoff:
                step1 = ["--step1-count", "0", "--fixed-step1-handoff", str(args.fixed_step1_handoff)]
            else:
                step1 = ["--step1-count", str(args.step1_valid or args.step1_budget),
                         "--step1-proposal-cap", str(args.step1_budget)]
            out.append({
                "name": root.name, "root": root, "seed": seed,
                "command": [
                    sys.executable, str(HERE / "langgraph_qwen_all_energy_supervisor.py"),
                    "--location", args.location, "--condition", condition,
                    "--resume-output-root", str(root), "--user-requirements", "", *step1,
                    "--step2-count", str(args.step2_valid or args.step2_budget),
                    "--step2-proposal-cap", str(args.step2_budget),
                    "--step3-count", str(args.step3_valid or args.step3_budget),
                    "--step3-proposal-cap", str(args.step3_budget),
                ],
            })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--location", default="hong_kong")
    parser.add_argument("--seeds", type=int, nargs="+", default=[901, 1901, 2901])
    parser.add_argument("--fixed-step1-handoff", type=Path,
                        help="Compact Step 1 handoff shared by every job; Step 1 is then not run.")
    parser.add_argument("--step1-budget", type=int, default=30)
    parser.add_argument("--step2-budget", type=int, default=20)
    parser.add_argument("--step3-budget", type=int, default=15)
    # A stage stops at its valid-case target or when its proposal budget is used up. Without a target the
    # target equals the budget (ablation: a fixed number of proposals).
    parser.add_argument("--step1-valid", type=int, help="Valid Step 1 cases to reach; default: the budget.")
    parser.add_argument("--step2-valid", type=int, help="Valid Step 2 cases to reach; default: the budget.")
    parser.add_argument("--step3-valid", type=int, help="Valid Step 3 cases to reach; default: the budget.")
    parser.add_argument("--model", default=os.environ.get("QWEN_MODEL", "qwen3.8:latest"))
    parser.add_argument("--backend", choices=("ollama", "claude_code"), default="ollama",
                        help="Model backend; claude_code runs Claude Code headless with every tool disabled.")
    parser.add_argument("--claude-exe", default=os.environ.get("CLAUDE_EXE", "claude"))
    parser.add_argument("--conditions", nargs="+", choices=CONDITIONS, default=list(CONDITIONS))
    parser.add_argument("--case-namespace", default=None,
                        help="Candidate-id namespace; default qwen_hk for ollama, claude_hk for claude_code.")
    parser.add_argument("--max-parallel", type=int, default=3)
    parser.add_argument("--min-free-memory-gb", type=float, default=8.0,
                        help="Start no new job while less physical memory than this is available.")
    parser.add_argument("--min-free-disk-gb", type=float, default=25.0,
                        help="Start no new job while the drive of --run-root has less free space than this.")
    args = parser.parse_args()
    args.run_root = args.run_root.resolve()
    if args.fixed_step1_handoff:
        args.fixed_step1_handoff = args.fixed_step1_handoff.resolve()
    args.run_root.mkdir(parents=True, exist_ok=True)
    logs = args.run_root / "logs"
    logs.mkdir(exist_ok=True)

    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, text=True, capture_output=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO, text=True, capture_output=True).stdout.strip())
    (args.run_root / "matrix_manifest.json").write_text(json.dumps({
        "started": datetime.now().isoformat(timespec="seconds"), "repository_commit": commit,
        "working_tree_dirty": dirty, "model": args.model, "backend": args.backend, "location": args.location,
        "conditions": list(args.conditions), "seeds": args.seeds,
        "fixed_step1_handoff": str(args.fixed_step1_handoff) if args.fixed_step1_handoff else None,
        "proposal_budget": {"step1": 0 if args.fixed_step1_handoff else args.step1_budget,
                            "step2": args.step2_budget, "step3": args.step3_budget},
        "valid_target": {"step1": 0 if args.fixed_step1_handoff else args.step1_valid or args.step1_budget,
                         "step2": args.step2_valid or args.step2_budget, "step3": args.step3_valid or args.step3_budget},
        "budget_rule": "every LLM call consumes one proposal of its stage",
        "max_parallel": args.max_parallel, "jobs": [job["name"] for job in jobs(args)],
    }, indent=2), encoding="utf-8")

    pending = [job for job in jobs(args) if not (job["root"] / DONE).exists()]
    running: dict[str, tuple[subprocess.Popen, dict, float]] = {}
    finished: dict[str, dict] = {}
    status_path = args.run_root / "matrix_status.json"
    while pending or running:
        started_this_pass = 0
        # A few jobs per pass, so the memory check sees the load of the jobs just started.
        while pending and started_this_pass < 4 and len(running) < parallel_limit(args.run_root, args.max_parallel):
            memory = free_memory_gb()
            if running and memory is not None and memory < args.min_free_memory_gb:
                break
            if shutil.disk_usage(args.run_root).free / 2 ** 30 < args.min_free_disk_gb:
                break
            started_this_pass += 1
            job = pending.pop(0)
            job["root"].mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            # The supervisor sets the output root and context of its own run.
            for name in ("AUTOMATED_DESIGN_OUTPUT_ROOT", "AUTOMATED_DESIGN_PROJECT_CONTEXT"):
                env.pop(name, None)
            # The workflow refuses output roots outside its allowed base; the base is this matrix root.
            env["AUTOMATED_DESIGN_ALLOWED_OUTPUT_ROOT"] = str(args.run_root)
            env.update({"QWEN_SEED": str(job["seed"]), "QWEN_MODEL": args.model, "QWEN_REQUEST_TIMEOUT": "3600",
                        "STEP2_TIMESTEP_PER_HOUR": "6", "STEP3_TIMESTEP_PER_HOUR": "6", "PYTHONIOENCODING": "utf-8"})
            env.update({"AGENT_LLM_BACKEND": args.backend, "CLAUDE_EXE": args.claude_exe,
                        "AGENT_CASE_NAMESPACE": args.case_namespace
                        or ("claude_hk" if args.backend == "claude_code" else "qwen_hk")})
            log = open(logs / f"{job['name']}.log", "a", encoding="utf-8")
            process = subprocess.Popen(job["command"], cwd=REPO, stdout=log, stderr=subprocess.STDOUT, env=env)
            running[job["name"]] = (process, job, time.time())
            print(f"{datetime.now():%H:%M} start {job['name']}", flush=True)
        for name, (process, job, started) in list(running.items()):
            if process.poll() is not None:
                hours = round((time.time() - started) / 3600, 2)
                finished[name] = {"returncode": process.returncode, "hours": hours}
                if process.returncode == 0:
                    (job["root"] / DONE).write_text(json.dumps(finished[name]), encoding="utf-8")
                del running[name]
                print(f"{datetime.now():%H:%M} end {name} rc={process.returncode} hours={hours}", flush=True)
        status_path.write_text(json.dumps({"running": sorted(running), "pending": [j["name"] for j in pending],
                                           "finished": finished}, indent=2), encoding="utf-8")
        time.sleep(20)
    print("MATRIX_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
