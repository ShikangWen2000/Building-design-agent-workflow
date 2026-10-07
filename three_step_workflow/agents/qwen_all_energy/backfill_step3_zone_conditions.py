from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


csv.field_size_limit(max(csv.field_size_limit(), 10 * 1024 * 1024))

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hvac_toolkit.runner import parse_zone_conditions  # noqa: E402


DIRECT_ZONE_FIELDS = (
    "zone_temp_min_c",
    "zone_temp_max_c",
    "zone_temp_avg_c",
    "zone_temp_setpoint_unmet_hours",
    "zone_temp_setpoint_unmet_pct",
    "zone_rh_min_pct",
    "zone_rh_max_pct",
    "zone_rh_avg_pct",
    "zone_rh_hours_above_70",
    "zone_rh_pct_above_70",
)
DECISION_RECORD_FIELDS = (
    *DIRECT_ZONE_FIELDS,
    "zone_condition_check_ok",
    "zone_rh_below_70_pct_ventilation_hours",
    "pmv_occupied_comfort_pct",
    "pmv_occupied_cold_pct",
    "pmv_occupied_hot_pct",
    "original_pmv_occupied_comfort_pct",
    "severe_count",
    "fatal_count",
)
CHECK_NAME = "direct_zone_temperature_and_humidity_outputs_available"
CHECK_MESSAGE = (
    "Step 3 must parse direct zone air-temperature, thermostat-setpoint, and relative-humidity "
    "outputs from the EnergyPlus SQL before the case can pass."
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def direct_check(metrics: dict[str, Any]) -> dict[str, Any]:
    values = {name: metrics.get(name) for name in DIRECT_ZONE_FIELDS}
    return {
        "name": CHECK_NAME,
        "ok": all(value is not None for value in values.values()),
        "severity": "error",
        "message": CHECK_MESSAGE,
        "data": values,
    }


def upsert_check(checks: list[dict[str, Any]], item: dict[str, Any]) -> None:
    for index, existing in enumerate(checks):
        if existing.get("name") == CHECK_NAME:
            checks[index] = item
            return
    checks.append(item)


def refresh_report(report: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    checks = report.setdefault("checks", [])
    upsert_check(checks, check)
    report["errors"] = [
        item.get("message", item.get("name", "constraint failed"))
        for item in checks
        if item.get("severity", "error") == "error" and not item.get("ok")
    ]
    report["ok"] = not report["errors"]

    common = report.setdefault("common_constraints", {"checks": []})
    common_checks = common.setdefault("checks", [])
    upsert_check(common_checks, check)
    common["ok"] = all(item.get("ok") for item in common_checks)
    return report


def update_csv(path: Path, updates: dict[str, dict[str, Any]]) -> None:
    if not path.exists():
        return
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    for field in (*DIRECT_ZONE_FIELDS, "physical_constraints_ok", "physical_constraints_json"):
        if field not in fieldnames:
            fieldnames.append(field)
    for row in rows:
        update = updates.get(str(row.get("case_id")))
        if not update:
            continue
        for key, value in update.items():
            row[key] = "" if value is None else str(value)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def backfill(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    step3 = output_root / "step3_hvac"
    feedback_paths = sorted((step3 / "llm_iterations").glob("hvac_*/feedback.json"))
    csv_updates: dict[str, dict[str, Any]] = {}
    feedback_by_case: dict[str, dict[str, Any]] = {}
    repaired: list[str] = []
    skipped: list[str] = []

    for feedback_path in feedback_paths:
        feedback = read_json(feedback_path)
        case_id = str(feedback.get("case_id") or feedback_path.parent.name)
        run_dir = output_root / "step3_hvac_toolkit" / "energyplus_runs" / case_id
        metrics = (
            {name: feedback.get(name) for name in DIRECT_ZONE_FIELDS}
            if all(feedback.get(name) is not None for name in DIRECT_ZONE_FIELDS)
            else parse_zone_conditions(run_dir)
        )
        check = direct_check(metrics)
        if not check["ok"]:
            skipped.append(case_id)
            continue

        feedback.update({name: metrics.get(name) for name in DIRECT_ZONE_FIELDS})
        report_path = step3 / "physical_constraints" / f"{case_id}.json"
        if report_path.exists():
            report = refresh_report(read_json(report_path), check)
            write_json(report_path, report)
            common_path = step3 / "physical_constraints" / f"{case_id}_common_constraints.json"
            if common_path.exists():
                write_json(common_path, report["common_constraints"])
            feedback["physical_constraints_ok"] = report["ok"]
            feedback["physical_constraints_json"] = json.dumps(
                report, separators=(",", ":"), ensure_ascii=False
            )

        write_json(feedback_path, feedback)
        feedback_by_case[case_id] = feedback
        csv_updates[case_id] = {
            **{name: feedback.get(name) for name in DIRECT_ZONE_FIELDS},
            "physical_constraints_ok": feedback.get("physical_constraints_ok"),
            "physical_constraints_json": feedback.get("physical_constraints_json"),
        }
        repaired.append(case_id)

    update_csv(step3 / "hvac_toolkit_eui_results.csv", csv_updates)

    best_path = step3 / "organized_current" / "best_hvac.json"
    if best_path.exists():
        best = read_json(best_path)
        update = feedback_by_case.get(str(best.get("case_id")))
        if update:
            best.update({name: update.get(name) for name in DIRECT_ZONE_FIELDS})
            best["physical_constraints_ok"] = update.get("physical_constraints_ok")
            best["physical_constraints_json"] = update.get("physical_constraints_json")
            write_json(best_path, best)

    decision_path = output_root / "energy_decision_record.json"
    if decision_path.exists():
        decision = read_json(decision_path)
        for row in decision.get("step3", []):
            feedback = feedback_by_case.get(str(row.get("case_id")))
            if feedback:
                row.update({name: feedback.get(name) for name in DECISION_RECORD_FIELDS})
        write_json(decision_path, decision)

    return {"output_root": str(output_root), "repaired": repaired, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill direct Step 3 zone temperature/RH metrics from existing EnergyPlus SQL files."
    )
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    print(json.dumps(backfill(Path(args.output_root)), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

