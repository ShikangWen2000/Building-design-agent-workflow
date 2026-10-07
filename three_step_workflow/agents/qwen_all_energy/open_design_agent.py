"""Open-design pieces of the agent harness: design scripts in Step 2 (and optionally Step 3).

The model returns its OpenStudio Ruby script as text inside its JSON proposal; the harness writes it next to
the spec (`envelope.rb`, `hvac.rb`) and the spec names that file, as the stage runners expect. The prompt
sections quote the repository's own documents (script frame, API examples, rules), so the agent sees what a
Claude Code or Codex run reads in the repository.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

STEP2_SCRIPT_FILE = "envelope.rb"
STEP3_SCRIPT_FILE = "hvac.rb"


def _section(text: str, start: str, end: str | None) -> str:
    begin = text.index(start)
    stop = text.index(end, begin) if end else len(text)
    return text[begin:stop].strip()


def script_text(value: Any, folder: Path | None) -> str:
    """Script code of a proposal (code text) or of a written spec (file name next to the spec)."""
    if not isinstance(value, str) or not value.strip():
        return ""
    if "\n" not in value and value.strip().endswith(".rb") and folder is not None:
        path = folder / value.strip()
        return path.read_text(encoding="utf-8") if path.exists() else ""
    return value


def script_sha256(code: str) -> str:
    return hashlib.sha256("\n".join(line.rstrip() for line in code.strip().splitlines()).encode("utf-8")).hexdigest()


def materialize_script(spec: dict[str, Any], field: str, folder: Path, filename: str) -> dict[str, Any]:
    """Write the script code of `field` to `folder/filename` and point the spec at that file."""
    code = script_text(spec.get(field), folder)
    if not code.strip():
        return spec
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(code.rstrip() + "\n", encoding="utf-8")
    return {**spec, field: filename}


def step2_spec_from_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields of the Step 2 spec contract; the stage interface rejects any other field."""
    return {key: proposal[key] for key in ("description", "design_intent", "envelope_script", "notes") if key in proposal}


def step2_task_text(repo: Path) -> str:
    readme = (repo / "steps" / "step2_envelope" / "README.md").read_text(encoding="utf-8")
    template = (repo / "steps" / "step2_envelope" / "envelope_script_template.rb").read_text(encoding="utf-8")
    examples = (repo / "steps" / "step2_envelope" / "envelope_script_examples.rb").read_text(encoding="utf-8")
    return "\n\n".join([
        _section(readme, "## Fixed settings", "## Handoff reproduction"),
        _section(readme, "## Design specification", "## Iteration"),
        "Script frame (steps/step2_envelope/envelope_script_template.rb):\n" + template.strip(),
        "Verified OpenStudio API examples (steps/step2_envelope/envelope_script_examples.rb; the closing example "
        "only demonstrates the calls and is not a recommended design):\n" + examples.strip(),
    ])


def step3_script_text(repo: Path) -> str:
    readme = (repo / "steps" / "step3_hvac" / "README.md").read_text(encoding="utf-8")
    template = (repo / "steps" / "step3_hvac" / "hvac_script_template.rb").read_text(encoding="utf-8")
    return "\n\n".join([
        _section(readme, "## Own HVAC scripts", "## Model-level rules"),
        _section(readme, "## Model-level rules", "## Execution"),
        "Script frame (steps/step3_hvac/hvac_script_template.rb):\n" + template.strip(),
    ])


def _case_rows(output_root: Path, spec_filename: str, script_field: str) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((output_root / "agent_candidates").glob(f"*/{spec_filename}")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        code = script_text(spec.get(script_field), path.parent)
        rows.append({"case_id": path.parent.name, "spec": spec, "script": code})
    return rows


def _feedback(output_root: Path, stage_folder: str, case_id: str) -> dict[str, Any]:
    path = output_root / stage_folder / "llm_iterations" / case_id / "feedback.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _failed_checks(feedback: dict[str, Any]) -> list[dict[str, Any]]:
    raw = feedback.get("physical_constraints_json")
    try:
        report = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
    except (TypeError, ValueError):
        report = None
    if not isinstance(report, dict):
        return []
    return [{"name": c.get("name"), "message": c.get("message"), "data": c.get("data")}
            for c in report.get("checks", []) if isinstance(c, dict) and c.get("ok") is False]


def step2_registry(output_root: Path, with_results: bool, limit: int = 30) -> str:
    """Earlier Step 2 proposals of this run. Both feedback conditions see every description and the full
    script of the latest proposal; with results, each row adds status, EUI, realized facade figures and
    failed rules, and the full script of the best valid case follows."""
    rows = _case_rows(output_root, "envelope_spec.json", "envelope_script")[-limit:]
    if not rows:
        return "(none yet)"
    lines, best = [], None
    for row in rows:
        entry: dict[str, Any] = {"case_id": row["case_id"], "description": row["spec"].get("description"),
                                 "design_intent": row["spec"].get("design_intent")}
        if with_results:
            feedback = _feedback(output_root, "step2_envelope_layout", row["case_id"])
            rules_path = output_root / "step2_envelope_layout" / "llm_iterations" / row["case_id"] / "model_rules.json"
            summary = json.loads(rules_path.read_text(encoding="utf-8")).get("summary", {}) if rules_path.exists() else {}
            log = output_root / "step2_envelope_layout" / "osm_cases" / f"{row['case_id']}_script.log"
            entry.update({
                "status": feedback.get("status", "no_feedback_written"),
                "physical_constraints_ok": feedback.get("physical_constraints_ok"),
                "selected_total_eui_kwh_m2": feedback.get("selected_total_eui_kwh_m2"),
                **{key: feedback.get(key) for key in ("cooling_eui_kwh_m2", "fan_eui_kwh_m2", "pump_eui_kwh_m2",
                                                       "lighting_eui_kwh_m2", "equipment_eui_kwh_m2")},
                "realized": {key: summary.get(key) for key in ("orientation_wwr", "wwr", "window_count",
                                                                "added_shading_surface_count", "shading_area")},
                "failed_rules": _failed_checks(feedback),
            })
            if feedback.get("status") == "failed_envelope_script" and log.exists():
                entry["script_log_tail"] = log.read_text(encoding="utf-8", errors="replace")[-1500:]
            eui = feedback.get("selected_total_eui_kwh_m2")
            if feedback.get("physical_constraints_ok") is True and eui is not None and (best is None or eui < best[0]):
                best = (eui, row)
        lines.append(json.dumps(entry, ensure_ascii=False, default=str))
    text = "\n".join(lines)
    text += f"\n\nFull script of the latest proposal ({rows[-1]['case_id']}):\n{rows[-1]['script']}"
    if with_results and best is not None and best[1]["case_id"] != rows[-1]["case_id"]:
        text += f"\n\nFull script of the best valid case ({best[1]['case_id']}, {best[0]:.2f} kWh/m2):\n{best[1]['script']}"
    return text
