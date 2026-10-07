"""Extract the selected Step 1 design of a finished run as a compact fixed handoff.

A finished output root keeps every Step 1 simulation folder. The Step 2
benchmark and the local-agent ablation copy the handoff into each of their
output roots, so they need only the selected design: its geometry, the Step 1
result table and the selection records. The evidence of the selected design
keeps binding the source run's model, inputs and code, so the source run must
stay in place and unchanged; only the geometry entry is moved to the copy.

Use <handoff>/step1_massing as --step1-root of the Step 2 benchmark and
<handoff> as --fixed-step1-handoff of the local agent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "steps" / "shared"))
from audit_contract import eligible  # noqa: E402

RECORD = "fixed_step1_handoff.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_small_files(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file():
            shutil.copy2(path, target / path.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True, help="Finished output root holding step1_massing.")
    parser.add_argument("--handoff-root", type=Path, required=True, help="New folder for the compact handoff.")
    args = parser.parse_args()
    source = args.source_root.resolve()
    target = args.handoff_root.resolve()
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"Handoff folder is not empty: {target}")

    source_best = source / "step1_massing" / "organized_current" / "best_massing.json"
    payload = json.loads(source_best.read_text(encoding="utf-8"))
    candidate_id = str(payload.get("candidate_id") or "")
    energy_result = payload.get("energy_result") or {}
    if not candidate_id or not eligible(energy_result):
        raise SystemExit("The source run has no selected Step 1 design with a current, hash-verified result.")

    geometry = Path("step1_massing") / "llm_iterations" / candidate_id / "geometry.json"
    copy_small_files(source / geometry.parent, target / geometry.parent)
    energy = Path("step1_massing_energy")
    energy_iteration = energy / "llm_iterations" / candidate_id
    if (source / energy_iteration).is_dir():
        copy_small_files(source / energy_iteration, target / energy_iteration)
    for relative in (energy / "massing_energy_results.csv", energy / "organized_current" / "best_massing_energy.json"):
        if (source / relative).is_file():
            (target / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target / relative)

    evidence = json.loads(energy_result["evidence_json"])
    digest = evidence.pop(str((source / geometry).resolve()), None)
    if not digest or sha256_file(target / geometry) != digest:
        raise SystemExit("The selected geometry is not bound by the Step 1 evidence.")
    evidence[str((target / geometry).resolve())] = digest
    energy_result["evidence_json"] = json.dumps(evidence, sort_keys=True)
    target_best = target / "step1_massing" / "organized_current" / "best_massing.json"
    target_best.parent.mkdir(parents=True, exist_ok=True)
    target_best.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if not eligible(json.loads(target_best.read_text(encoding="utf-8"))["energy_result"]):
        raise SystemExit("The compact handoff failed its own evidence check.")

    record = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "source_root": str(source), "candidate_id": candidate_id,
        "selected_total_eui_kwh_m2": float(energy_result["selected_total_eui_kwh_m2"]),
        "geometry_sha256": digest, "evidence_files": len(evidence),
        "note": "Evidence binds files of the source run and its repository copy; keep both unchanged.",
    }
    (target / RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
