"""Constraint pre-check of one Step 2 benchmark proposal, without sizing or simulation.

The Step 2 stage exports the model, sizes the cooling coils (a sizing run of
several minutes) and only then audits the windows and shading. A proposal that
fails the audit therefore costs the sizing run for nothing. This script runs
the stage's own `run_case` with the coil sizing skipped and stops right after
the audit, so the constraint decision is the stage's, not a copy of it.

Exit code 0: the proposal passed the constraints checked before simulation, or
the pre-check was inconclusive; the caller runs the stage as usual.
Exit code 3: the stage rejected the proposal; the JSON output holds the
rejected case row and the caller records it without running the stage.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
REJECTED = 3


class _PassedAudit(Exception):
    """Raised at the first stage call that follows the window and shading audit."""


def precheck_rejection(spec_path: Path, case_id: str, env: dict[str, str], log_path: Path,
                       timeout: int = 900) -> dict[str, Any] | None:
    """Run the pre-check in a subprocess; return the rejected case row, or None to run the stage."""
    output = log_path.with_suffix(".json")
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--json-spec", str(spec_path),
             "--case-id", case_id, "--output", str(output)],
            cwd=str(REPO_ROOT), env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(result.stdout or "", encoding="utf-8", errors="replace")
    if result.returncode != REJECTED or not output.exists():
        return None
    return json.loads(output.read_text(encoding="utf-8")).get("result")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-spec", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for folder in (REPO_ROOT / "steps" / "shared", REPO_ROOT / "steps" / "step2_envelope"):
        sys.path.insert(0, str(folder))
    import coil_sizing
    from audit_contract import output_lock

    coil_sizing.size_cooling_coils = lambda *unused, **unused_keywords: {}
    import stage2_envelope_energyplus as stage2

    def passed(*unused: Any, **unused_keywords: Any) -> None:
        raise _PassedAudit

    # run_case renders the model only after the audit has passed.
    stage2.export_openstudio_threejs_scene = passed

    def write(payload: dict[str, Any]) -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    try:
        with output_lock(stage2.OUTPUT_ROOT):
            cfg = stage2.load_config()
            massing = stage2.load_best_massing()
            for folder in (stage2.STEP2, stage2.RUNS, stage2.OSMS):
                folder.mkdir(parents=True, exist_ok=True)
            spec = json.loads(args.json_spec.read_text(encoding="utf-8"))
            case = stage2.run_case(cfg, massing, stage2.case_from_design_spec(massing, spec, args.case_id), True)
    except _PassedAudit:
        write({"passed": True})
        return 0
    except Exception as exc:  # inconclusive: the stage itself decides
        write({"passed": None, "error": f"{type(exc).__name__}: {exc}"})
        print(f"pre-check inconclusive: {type(exc).__name__}: {exc}")
        return 0
    row = asdict(case)
    write({"passed": False, "result": row})
    print(f"pre-check rejected {args.case_id}: {row.get('status')}")
    return REJECTED


if __name__ == "__main__":
    raise SystemExit(main())
