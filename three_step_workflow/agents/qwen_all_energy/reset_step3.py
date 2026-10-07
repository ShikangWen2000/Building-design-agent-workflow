"""Set the Step 3 of finished runs aside, so Step 3 runs again from an empty history.

Used when the Step 3 rules change: Steps 1 and 2 of a run are kept (results, LLM call records, tokens); the
Step 3 cases, their LLM call records and attempt events move to <run>/<archive>/ and the run is no longer
marked done. Nothing is deleted.

    python reset_step3.py --archive <archive name> <run folder> [<run folder> ...]
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

FOLDERS = ("step3_hvac", "step3_hvac_toolkit")
FILES = ("ablation_job_done.json", "supervisor_summary.json", "langgraph_all_energy_supervisor_summary.json",
         "energy_decision_record.json")


def reset(run: Path, archive_name: str) -> dict:
    archive = run / archive_name
    if archive.exists():
        raise SystemExit(f"{archive} exists; this run was reset before")
    archive.mkdir()
    moved = {"folders": [], "files": [], "candidates": 0, "events": 0, "calls": 0, "logs": 0}
    for name in FOLDERS:
        if (run / name).exists():
            shutil.move(str(run / name), str(archive / name))
            moved["folders"].append(name)
    for name in FILES:
        if (run / name).exists():
            shutil.move(str(run / name), str(archive / name))
            moved["files"].append(name)
    for folder, pattern, key in (("agent_candidates", "hvac_*", "candidates"), ("supervisor_logs", "hvac_*", "logs"),
                                 ("supervisor_logs", "step3_*", "logs")):
        for path in sorted((run / folder).glob(pattern)) if (run / folder).exists() else []:
            (archive / folder).mkdir(exist_ok=True)
            shutil.move(str(path), str(archive / folder / path.name))
            moved[key] += 1
    log = run / "attempt_log.jsonl"
    if log.exists():
        lines = log.read_text(encoding="utf-8").splitlines()
        step3 = [line for line in lines if json.loads(line).get("stage") == "step3"]
        log.write_text("".join(line + chr(10) for line in lines if json.loads(line).get("stage") != "step3"), encoding="utf-8")
        (archive / "attempt_log_step3.jsonl").write_text("".join(line + chr(10) for line in step3), encoding="utf-8")
        moved["events"] = len(step3)
    raw = run / "llm_raw_outputs"
    calls = raw / "ollama_calls.jsonl"
    if calls.exists():
        lines = calls.read_text(encoding="utf-8").splitlines()
        is_step3 = [str(json.loads(line).get("label") or "").startswith("step3") for line in lines]
        calls.write_text("".join(line + chr(10) for line, flag in zip(lines, is_step3) if not flag), encoding="utf-8")
        (archive / "llm_raw_outputs").mkdir(exist_ok=True)
        (archive / "llm_raw_outputs" / "ollama_calls_step3.jsonl").write_text(
            "".join(line + chr(10) for line, flag in zip(lines, is_step3) if flag), encoding="utf-8")
        for path in sorted(raw.glob("*_step3_*.json")):
            shutil.move(str(path), str(archive / "llm_raw_outputs" / path.name))
            moved["calls"] += 1
    (archive / "reset_record.json").write_text(json.dumps(moved, indent=2), encoding="utf-8")
    return moved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True, help="Name of the archive folder created inside each run.")
    parser.add_argument("runs", nargs="+", type=Path)
    args = parser.parse_args()
    for run in args.runs:
        print(run.name, json.dumps(reset(run.resolve(), args.archive)))
