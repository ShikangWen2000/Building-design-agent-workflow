from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from envelope_search_space import decode_genome, normalize_genome
from llm_physical_design_space import physical_design_to_spec
from precheck_step2_case import precheck_rejection


REPO_ROOT = Path(__file__).resolve().parents[1]


# Simulation by-products above this size are deleted after every proposal.
LARGE_OUTPUT_BYTES = 10 * 1024 * 1024
KEPT_OUTPUT_SUFFIXES = {".epw", ".jsonl", ".csv", ".png", ".md", ".log"}


def keeps_large_file(path: Path) -> bool:
    """Large files that stay: records, and the case models that result evidence binds."""
    suffix = path.suffix.lower()
    if suffix == ".osm":
        return path.parent.name in ("osm_cases", "organized_current")
    if suffix == ".json":
        return "threejs" not in path.name  # a render scene is rebuilt from the model
    return suffix in KEPT_OUTPUT_SUFFIXES


def prune_large_outputs(root: Path) -> int:
    """Delete files above LARGE_OUTPUT_BYTES under root; return the bytes freed."""
    freed = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and path.stat().st_size > LARGE_OUTPUT_BYTES and not keeps_large_file(path):
                size = path.stat().st_size
                path.unlink()
                freed += size
        except OSError:
            continue
    return freed


class _Rejected(Exception):
    """The Step 2 constraint pre-check rejected the proposal."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    temporary.write_text(text, encoding="utf-8")
    for retry in range(8):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            time.sleep(0.15 * (retry + 1))
    path.write_text(text, encoding="utf-8")
    try:
        temporary.unlink()
    except OSError:
        pass


def read_case_row(results_csv: Path, case_id: str) -> dict[str, Any]:
    if not results_csv.exists():
        return {"case_id": case_id, "status": "missing_result"}
    with results_csv.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row.get("case_id") == case_id:
                return dict(row)
    return {"case_id": case_id, "status": "missing_result"}


def valid_score(row: dict[str, Any]) -> float | None:
    try:
        value = float(row.get("selected_total_eui_kwh_m2") or "")
    except (TypeError, ValueError):
        return None
    if row.get("status") != "completed":
        return None
    if str(row.get("physical_constraints_ok", "")).lower() not in ("true", "1"):
        return None
    if int(float(row.get("fatal_count") or 0)):
        return None
    return value


def rebind_copied_step1_geometry_evidence(source_root: Path, target_root: Path) -> None:
    """Rebase only the copied geometry path while preserving its verified digest.

    Step 1 evidence uses absolute paths. A benchmark intentionally copies the
    read-only handoff into an isolated workflow root, so the geometry's absolute
    path changes even though its bytes do not. All other evidence remains bound
    to the archived fixed-build files and frozen repository inputs.
    """
    source_best = source_root / "organized_current" / "best_massing.json"
    target_best = target_root / "organized_current" / "best_massing.json"
    payload = read_json(target_best)
    candidate_id = payload.get("candidate_id")
    source_geometry = (source_root / "llm_iterations" / candidate_id / "geometry.json").resolve()
    target_geometry = (target_root / "llm_iterations" / candidate_id / "geometry.json").resolve()
    evidence = json.loads(payload.get("energy_result", {}).get("evidence_json") or "{}")
    old_key = str(source_geometry)
    new_key = str(target_geometry)
    expected = evidence.get(old_key)
    if expected is None and new_key in evidence:
        expected = evidence[new_key]
        if sha256_file(target_geometry) != expected:
            raise RuntimeError("Cannot reuse rebound Step 1 handoff: copied geometry digest changed")
        return
    if not expected or sha256_file(source_geometry) != expected or sha256_file(target_geometry) != expected:
        raise RuntimeError("Cannot rebind Step 1 handoff: source/copy geometry digest is not verified")
    evidence.pop(old_key)
    evidence[new_key] = expected
    payload["energy_result"]["evidence_json"] = json.dumps(evidence, sort_keys=True)
    write_json(target_best, payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-root", required=True, type=Path)
    parser.add_argument("--step1-root", required=True, type=Path)
    parser.add_argument("--project-context", required=True, type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--genome", type=Path)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--hypothesis", required=True, type=Path)
    parser.add_argument("--attempt-index", required=True, type=int)
    parser.add_argument("--valid-target-index", required=True, type=int)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--skip-precheck", action="store_true")
    args = parser.parse_args()

    agent_root = args.agent_root.resolve()
    workflow_root = agent_root / "workflow_output"
    step1_target = workflow_root / "step1_massing"
    if not step1_target.exists():
        shutil.copytree(args.step1_root.resolve(), step1_target)
    rebind_copied_step1_geometry_evidence(args.step1_root.resolve(), step1_target)

    context = read_json(args.project_context.resolve())
    if context.get("location") != "hong_kong":
        raise RuntimeError("The controlled LLM benchmark is restricted to hong_kong.")
    context["output_root"] = str(workflow_root)
    context_path = agent_root / "config" / "project_context.json"
    write_json(context_path, context)

    if bool(args.genome) == bool(args.design):
        raise RuntimeError("Provide exactly one of --genome or --design.")
    genome: dict[str, Any] | None = None
    physical_design: dict[str, Any] | None = None
    if args.genome:
        genome = normalize_genome(read_json(args.genome.resolve()))
        spec = decode_genome(
            genome,
            method="llm_agent",
            candidate_index=args.attempt_index,
            case_id=args.case_id,
        )
    else:
        physical_design = read_json(args.design.resolve())
        spec = physical_design_to_spec(
            physical_design,
            method="llm_agent",
            candidate_index=args.attempt_index,
            case_id=args.case_id,
        )
    hypothesis = args.hypothesis.read_text(encoding="utf-8")
    spec["description"] = f"LLM Agent attempt {args.attempt_index:03d}: {hypothesis.splitlines()[0].strip()}"
    spec["design_intent"] = hypothesis.strip()

    case_dir = agent_root / "generated_candidates" / args.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.hypothesis, case_dir / "hypothesis.md")
    if genome is not None:
        write_json(case_dir / "genome.json", genome)
    if physical_design is not None:
        write_json(case_dir / "physical_design_raw.json", physical_design)
    spec_path = case_dir / "design_spec.json"
    write_json(spec_path, spec)

    env = os.environ.copy()
    env["AUTOMATED_DESIGN_PROJECT_CONTEXT"] = str(context_path)
    env["AUTOMATED_DESIGN_OUTPUT_ROOT"] = str(workflow_root)
    env["AUTOMATED_DESIGN_ALLOWED_OUTPUT_ROOT"] = str(agent_root.parent.parent)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        sys.executable,
        str(REPO_ROOT / "steps" / "step2_envelope" / "stage2_envelope_energyplus.py"),
        "--json-spec",
        str(spec_path),
        "--case-id",
        args.case_id,
        "--overwrite",
    ]
    started = time.time()
    # A proposal the stage rejects before simulation is recorded from the
    # pre-check, without spending the coil-sizing run on it.
    rejected = None if args.skip_precheck else precheck_rejection(
        spec_path, args.case_id, env, case_dir / "precheck.log")
    try:
        if rejected is not None:
            raise _Rejected
        result = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=args.timeout,
        )
        returncode = result.returncode
        output = result.stdout
    except _Rejected:
        returncode = 1
        output = f"Rejected by the Step 2 constraint pre-check: {rejected.get('status')}\n"
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        output = (exc.stdout or "") + f"\nTIMEOUT after {args.timeout}s\n"
    (case_dir / "energyplus.log").write_text(
        "COMMAND: " + " ".join(command)
        + f"\nELAPSED_SECONDS: {time.time() - started:.2f}\n"
        + output,
        encoding="utf-8",
        errors="replace",
    )

    results_csv = workflow_root / "step2_envelope_layout" / "envelope_eui_results.csv"
    prune_large_outputs(workflow_root)
    row = rejected if rejected is not None else read_case_row(results_csv, args.case_id)
    score = valid_score(row)
    evaluation = {
        "case_id": args.case_id,
        "attempt_index": args.attempt_index,
        "target_valid_index": args.valid_target_index,
        "returncode": returncode,
        "precheck_rejected": rejected is not None,
        "valid": score is not None,
        "score": score,
        "result": row,
        "spec_path": str(spec_path),
        "elapsed_seconds": round(time.time() - started, 3),
        "proposal_consumed": True,
    }
    write_json(case_dir / "evaluation.json", evaluation)
    with (agent_root / "evaluations.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(evaluation, ensure_ascii=False) + "\n")
    print(json.dumps(evaluation, indent=2, ensure_ascii=True))
    return 0 if returncode == 0 else returncode


if __name__ == "__main__":
    raise SystemExit(main())
