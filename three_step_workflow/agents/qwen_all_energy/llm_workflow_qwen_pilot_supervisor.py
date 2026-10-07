from __future__ import annotations

import argparse
import csv
import hashlib
from open_design_agent import STEP3_SCRIPT_FILE, materialize_script, script_sha256, script_text, step3_script_text
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_workflow_qwen_step1_driver import (
    BASE_URL,
    MODEL,
    create_project_context,
    extract_json_object,
    read_text,
    require_step1_spec,
)


DEFAULT_REPO = Path(__file__).resolve().parents[2]
if str(DEFAULT_REPO) not in sys.path:
    sys.path.insert(0, str(DEFAULT_REPO))
SHARED_DIR = DEFAULT_REPO / "steps" / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))
from audit_contract import eligible as audit_result_eligible  # noqa: E402
from hvac_toolkit.schema import normalize_tool_plan  # noqa: E402

DEFAULT_NUM_PREDICT = int(os.environ.get("QWEN_NUM_PREDICT", "24576"))
DEFAULT_NUM_CTX = int(os.environ.get("QWEN_NUM_CTX", "65536"))
DEFAULT_SEED = int(os.environ.get("QWEN_SEED", "901"))
# Several runs share one model server; a queued request must not count as a failed proposal.
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("QWEN_REQUEST_TIMEOUT", "900"))
CASE_NAMESPACE = os.environ.get("AGENT_CASE_NAMESPACE", "qwen_hk")
AGENT_HARNESS = os.environ.get("AGENT_HARNESS", "langgraph")
# Model backend: "ollama" (the local model server) or "claude_code" (Claude Code in headless mode).
LLM_BACKEND = os.environ.get("AGENT_LLM_BACKEND", "ollama")
CLAUDE_EXE = os.environ.get("CLAUDE_EXE", "claude")
SYSTEM_MESSAGE = (
    "You produce strict JSON only. Keep internal reasoning concise and reserve "
    "enough generation budget for the final JSON. Do not explain in the final answer."
)


def staged_exploration_instruction(valid_count: int, target_count: int) -> str:
    """Return the mandatory exploration/refinement policy for one LLM call."""
    target_count = max(int(target_count), 1)
    valid_count = max(int(valid_count), 0)
    exploration_target = max(1, (3 * target_count + 4) // 5)
    if valid_count < exploration_target:
        return (
            f"EXPLORATION phase ({valid_count}/{exploration_target} exploration cases complete). "
            "An incumbent with low EUI may be a local optimum. Maximize information gain by testing "
            "a materially different, previously untested design hypothesis. An exact repeat of any "
            "prior design or executed configuration is forbidden."
        )
    return (
        f"REFINEMENT phase ({valid_count}/{target_count} valid cases complete). Revisit a promising "
        "family only when changing one or two explicit design variables. State the reference case, "
        "the changed variables, and the expected energy/comfort effect. Every formal case must occupy "
        "a different design-parameter configuration; exact repeats are forbidden."
    )


# Every Step 2 field that stage2_envelope_geometry.case_from_design_spec
# executes; the duplicate signature and the history registry both use it.
# Open workflow: a Step 2 design is its envelope script (open_design_agent.py).
STEP2_DESIGN_KEYS = ("envelope_script",)

_NARRATIVE_KEYS = {
    "candidate_id",
    "case_id",
    "description",
    "design_intent",
    "design_note",
    "engineering_basis",
    "rationale",
    "strategy_summary",
    "source_massing",
    "notes",
}


def _canonical_design_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonical_design_value(value[key])
            for key in sorted(value)
            if key not in _NARRATIVE_KEYS
        }
    if isinstance(value, list):
        return [_canonical_design_value(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        # Two-decimal quantization prevents millimetre-scale perturbations from
        # masquerading as a new design while keeping meaningful HVAC/envelope
        # parameter changes distinct.
        rounded = round(float(value), 2)
        return int(rounded) if rounded.is_integer() else rounded
    return value


def design_parameter_signature(stage: str, spec: dict[str, Any], spec_dir: Path | None = None) -> str:
    """Hash only parameters that can change the generated design or executed tool plan."""
    if stage == "step1":
        payload = {
            "area_tolerance_m2": spec.get("area_tolerance_m2"),
            "massing": spec.get("massing"),
        }
    elif stage == "step2":
        payload = {"script": script_sha256(script_text(spec.get("envelope_script"), spec_dir))}
    elif stage == "step3":
        plan_source = spec.get("tool_plan") or spec.get("toolkit_plan")
        series_id = spec.get("series_id")
        if plan_source is None and not series_id and spec.get("hvac_script"):
            plan = {"case_id": "__SIGNATURE_CASE__", "tool_calls": []}
        elif plan_source is None and isinstance(series_id, str) and series_id:
            # The HVAC catalog resolves location-specific performance at import
            # time, so defer this import until the run context is active.
            from hvac_toolkit.series import plan_for_series

            plan = plan_for_series(series_id, case_id="__SIGNATURE_CASE__")
        else:
            if plan_source is None and "tool_calls" in spec:
                plan_source = spec
            if not isinstance(plan_source, dict):
                raise ValueError("Step 3 spec must contain series_id or tool_plan for signature generation")
            plan = dict(plan_source)
            plan["case_id"] = "__SIGNATURE_CASE__"
        payload = {"tool_calls": normalize_tool_plan(plan).get("tool_calls", [])}
        if spec.get("air_side_options"):
            payload["air_side_options"] = spec["air_side_options"]
        if spec.get("hvac_script"):
            payload["hvac_script"] = script_sha256(script_text(spec.get("hvac_script"), spec_dir))
    else:
        raise ValueError(f"Unsupported design-signature stage: {stage}")
    encoded = json.dumps(
        _canonical_design_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def existing_design_signatures(output_root: Path, stage: str) -> set[str]:
    filenames = {"step1": "massing_spec.json", "step2": "envelope_spec.json", "step3": "hvac_spec.json"}
    filename = filenames[stage]
    signatures: set[str] = set()
    for path in (output_root / "agent_candidates").glob(f"*/{filename}"):
        try:
            signatures.add(design_parameter_signature(stage, read_json(path), path.parent))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return signatures


def derived_call_seed(log_label: str) -> int:
    """Give each labeled call a distinct deterministic seed derived from the run's base seed."""
    offset = int.from_bytes(hashlib.sha256(log_label.encode("utf-8")).digest()[:4], "big") % 1_000_000
    return DEFAULT_SEED + offset


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


HANDOFF_AUDIT_KEYS = {
    "evidence_json",
    "audit_contract",
    "energy_contract_version",
    "physical_constraints_json",
    "osm_path",
    "run_dir",
    "source_osm",
}


def compact_handoff(record: Any) -> Any:
    """Design parameters and metrics of a handoff record, without audit evidence or file paths."""
    if isinstance(record, dict):
        return {
            key: compact_handoff(value)
            for key, value in record.items()
            if key not in HANDOFF_AUDIT_KEYS and not str(key).endswith(("_png", "_path"))
        }
    if isinstance(record, list):
        return [compact_handoff(item) for item in record]
    return record


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def climate_prompt_section(climate: dict[str, Any], condition: str) -> str:
    """schema_only keeps the task location but drops climate, standards and design guidance."""
    if condition == "schema_only":
        return f"Project location: {climate.get('display_name') or climate.get('location')}"
    return f"Active climate and standards context:\n{json.dumps(climate, ensure_ascii=False)}"


def active_climate_prompt_context(repo: Path, output_root: Path) -> dict[str, Any]:
    """Return the run-specific climate facts that prompts are allowed to assume."""
    run_context_path = output_root / "active_project_context.json"
    context_path = run_context_path if run_context_path.exists() else repo / "config" / "project_context.json"
    context = read_json(context_path)
    location = str(context.get("location") or "").strip()
    profiles = read_json(repo / "config" / "climate_profiles.json")
    if location not in profiles:
        raise ValueError(f"Unsupported or missing active prompt location: {location!r}")
    profile = profiles[location]
    requirements = context.get("design_requirements") if isinstance(context.get("design_requirements"), dict) else {}
    return {
        "location": location,
        "display_name": profile.get("display_name", location),
        "region_family": profile.get("region_family"),
        "heating_periods": profile.get("heating_periods", []),
        "standards_priority": profile.get("standards_priority", []),
        "chiller_reference_conditions": profile.get("chiller_reference_conditions", {}),
        "chiller_reference_performance": profile.get("chiller_reference_performance", {}),
        "heat_pump_heating_baseline": profile.get("heat_pump_heating_baseline", {}),
        "user_requirements": requirements.get("user_requirements", ""),
        "objective": context.get("objective", {}),
        "frozen_simulation": context.get("frozen_simulation", {}),
        "stage_scope": context.get("stage_scope", {}),
        "handoff_policy": context.get("handoff_policy", {}),
        "hvac_constraints": context.get("hvac_constraints", {}),
    }


def _safe_log_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", label.strip())
    return cleaned.strip("_") or "llm_call"


def _write_llm_call_record(record: dict[str, Any]) -> None:
    output_root_text = os.environ.get("AUTOMATED_DESIGN_OUTPUT_ROOT")
    if not output_root_text:
        return
    log_dir = Path(output_root_text) / "llm_raw_outputs"
    log_dir.mkdir(parents=True, exist_ok=True)
    existing = len(list(log_dir.glob("*.json")))
    label = _safe_log_label(str(record.get("label") or "llm_call"))
    path = log_dir / f"{existing + 1:04d}_{label}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    with (log_dir / "ollama_calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"path": str(path), **record}, ensure_ascii=False) + "\n")
    label = str(record.get("label") or "llm_call")
    stage = "step1" if label.startswith("step1") else "step2" if label.startswith("step2") else "step3" if label.startswith("step3") else "run"
    candidate_match = re.search(rf"((?:candidate|envelope|hvac)_{re.escape(CASE_NAMESPACE)}_\d+)$", label)
    candidate_id = candidate_match.group(1) if candidate_match else label
    error_message = record.get("transport_error") or record.get("parse_error")
    append_attempt_event(
        log_dir.parent,
        {
            "event_type": "llm_generation",
            "condition": os.environ.get("EXPERIMENT_CONDITION", "full_feedback"),
            "stage": stage,
            "attempt_id": label,
            "candidate_id": candidate_id,
            "outcome": "error" if error_message else "parsed",
            "duration_seconds": record.get("client_duration_seconds"),
            "prompt_chars": record.get("prompt_chars"),
            "prompt_tokens": record.get("prompt_tokens", record.get("ollama_prompt_eval_count")),
            "completion_tokens": record.get("completion_tokens", record.get("ollama_eval_count")),
            "error_category": "transport" if record.get("transport_error") else "json_parse" if record.get("parse_error") else None,
            "error_message": error_message,
            "evidence_path": str(path),
            "details": {
                "thinking_chars": record.get("thinking_chars"),
                "raw_content_chars": record.get("raw_content_chars"),
                "ollama_total_duration_ns": record.get("ollama_total_duration"),
                "thinking_tokens": record.get("thinking_tokens"),
                "cost_usd": record.get("claude_total_cost_usd"),
            },
        },
    )


def append_attempt_event(output_root: Path, payload: dict[str, Any]) -> None:
    event = {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_id": output_root.name,
        "agent_harness": AGENT_HARNESS,
        "model_id": MODEL,
        **payload,
    }
    with (output_root / "attempt_log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def count_logged_proposals(output_root: Path, stage: str) -> int:
    """Count LLM calls already charged to a stage's total-proposal budget."""
    path = output_root / "attempt_log.jsonl"
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event_type") == "llm_generation" and event.get("stage") == stage:
            count += 1
    return count


# Simulation by-products above this size are deleted after every stage command:
# the Step 3 eplusout.sql and eso (about 1 GB each per case), which the stage
# has already read, render scenes and intermediate model copies. Records and
# the case models bound by result evidence stay.
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


def prune_large_outputs(output_root: Path) -> int:
    """Delete files above LARGE_OUTPUT_BYTES under output_root; return the bytes freed."""
    freed = 0
    for path in output_root.rglob("*"):
        try:
            if path.is_file() and path.stat().st_size > LARGE_OUTPUT_BYTES and not keeps_large_file(path):
                size = path.stat().st_size
                path.unlink()
                freed += size
        except OSError:
            continue
    return freed


def run_command(command: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print("+ " + " ".join(command))
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log_path.write_text(completed.stdout, encoding="utf-8", errors="replace")
    print(completed.stdout[-3000:])
    if env.get("AUTOMATED_DESIGN_OUTPUT_ROOT"):
        freed = prune_large_outputs(Path(env["AUTOMATED_DESIGN_OUTPUT_ROOT"]))
        if freed:
            print(f"Pruned {freed / 1e6:.0f} MB of large simulation outputs")
    return completed.returncode


# Replies of the Claude Code CLI when the account has no capacity left. Such a reply is not a proposal:
# the call waits and is repeated, and nothing is written to the call records or the attempt log.
CLAUDE_CAPACITY_MARKERS = ("usage limit reached", "rate limit", "overloaded")
# "You've hit your session limit", "... weekly limit", "... usage limit", "... limit".
CLAUDE_CAPACITY_PATTERN = re.compile(r"hit your (\w+ )?limit", re.IGNORECASE)
CLAUDE_CAPACITY_WAIT_SECONDS = int(os.environ.get("CLAUDE_CAPACITY_WAIT_SECONDS", "600"))
CLAUDE_CAPACITY_MAX_WAIT_SECONDS = int(os.environ.get("CLAUDE_CAPACITY_MAX_WAIT_SECONDS", str(48 * 3600)))


def is_claude_capacity_reply(text: str) -> bool:
    lowered = text.lower()
    return bool(CLAUDE_CAPACITY_PATTERN.search(text)) or any(marker in lowered for marker in CLAUDE_CAPACITY_MARKERS)


def ask_json_claude_code(prompt: str, log_label: str) -> dict[str, Any]:
    """One proposal from Claude Code in headless mode: every tool, skill and MCP server disabled, run in an
    empty working folder, so the model sees exactly the prompt that the condition allows."""
    command = [
        CLAUDE_EXE, "-p", "--model", MODEL, "--tools", "", "--disable-slash-commands", "--strict-mcp-config",
        "--no-session-persistence", "--output-format", "json", "--system-prompt", SYSTEM_MESSAGE,
    ]
    record: dict[str, Any] = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "label": log_label,
        "model": MODEL,
        "backend": "claude_code",
        "prompt_chars": len(prompt),
        "prompt": prompt,
    }
    waited = 0
    while True:
        request_started = time.perf_counter()
        try:
            with tempfile.TemporaryDirectory() as folder:
                completed = subprocess.run(
                    command, input=prompt, text=True, encoding="utf-8", capture_output=True, cwd=folder,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            reply = f"{completed.stdout} {completed.stderr}"
        except Exception as exc:
            _write_llm_call_record({**record, "transport_error": str(exc),
                                    "client_duration_seconds": time.perf_counter() - request_started})
            raise
        try:
            failed = completed.returncode != 0 or bool(json.loads(completed.stdout).get("is_error"))
        except Exception:
            failed = True
        if not (failed and is_claude_capacity_reply(reply)):
            break
        if waited >= CLAUDE_CAPACITY_MAX_WAIT_SECONDS:
            raise RuntimeError(f"CLAUDE_CAPACITY_LIMIT: no capacity after waiting {waited / 3600:.1f} h: {reply[:300]}")
        print(f"Claude capacity limit ({reply.strip()[:160]}); waiting {CLAUDE_CAPACITY_WAIT_SECONDS} s "
              f"(waited {waited / 60:.0f} min); this call is not counted as a proposal", flush=True)
        time.sleep(CLAUDE_CAPACITY_WAIT_SECONDS)
        waited += CLAUDE_CAPACITY_WAIT_SECONDS
    record["capacity_wait_seconds"] = waited
    try:
        result = json.loads(completed.stdout)
        if completed.returncode != 0 or result.get("is_error"):
            raise RuntimeError(str(result.get("result") or completed.stderr)[:500])
    except Exception as exc:
        _write_llm_call_record({**record, "transport_error": str(exc),
                                "client_duration_seconds": time.perf_counter() - request_started})
        raise
    usage = result.get("usage") or {}
    content = str(result.get("result") or "")
    record.update({
        "raw_content_chars": len(content),
        "raw_content": content,
        # Main-model usage; input = uncached + cache-creation + cache-read input. Output includes thinking.
        "prompt_tokens": sum(int(usage.get(key) or 0) for key in
                             ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "completion_tokens": usage.get("output_tokens"),
        "thinking_tokens": (usage.get("output_tokens_details") or {}).get("thinking_tokens"),
        "claude_usage": usage,
        # Every model the CLI called, with its own tokens and cost (it may add a small helper-model call).
        "claude_model_usage": result.get("modelUsage"),
        "claude_total_cost_usd": result.get("total_cost_usd"),
        "claude_duration_ms": result.get("duration_ms"),
        "claude_duration_api_ms": result.get("duration_api_ms"),
        "claude_session_id": result.get("session_id"),
        "claude_stop_reason": result.get("stop_reason"),
        "client_duration_seconds": time.perf_counter() - request_started,
    })
    try:
        parsed = extract_json_object(content)
    except Exception as exc:
        record["parse_error"] = str(exc)
        _write_llm_call_record(record)
        raise
    record["parsed_json"] = parsed
    _write_llm_call_record(record)
    return parsed


def ask_json(prompt: str, max_tokens: int | None = None, log_label: str = "llm_call") -> dict[str, Any]:
    if LLM_BACKEND == "claude_code":
        return ask_json_claude_code(prompt, log_label)
    max_tokens = DEFAULT_NUM_PREDICT if max_tokens is None else max_tokens
    call_seed = derived_call_seed(log_label)
    ollama_url = BASE_URL.removesuffix("/v1") + "/api/chat"
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_MESSAGE,
            },
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "think": True,
        "format": "json",
        "options": {
            "temperature": 0.7,
            "top_p": 0.9,
            "num_ctx": DEFAULT_NUM_CTX,
            "num_predict": max_tokens,
            "seed": call_seed,
        },
    }
    request = urllib.request.Request(
        ollama_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    request_started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        _write_llm_call_record(
            {
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                "label": log_label,
                "model": MODEL,
                "max_tokens": max_tokens,
                "seed": call_seed,
                "prompt_chars": len(prompt),
                "prompt": prompt,
                "transport_error": str(exc),
                "client_duration_seconds": time.perf_counter() - request_started,
            }
        )
        raise
    message = result.get("message", {})
    content = message.get("content") or ""
    thinking = message.get("thinking")
    record = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "label": log_label,
        "model": MODEL,
        "max_tokens": max_tokens,
        "seed": call_seed,
        "prompt_chars": len(prompt),
        "raw_content_chars": len(content),
        "thinking_chars": len(thinking or ""),
        "prompt": prompt,
        "raw_content": content,
        "thinking": thinking,
        "ollama_done": result.get("done"),
        "ollama_total_duration": result.get("total_duration"),
        "ollama_load_duration": result.get("load_duration"),
        "ollama_prompt_eval_count": result.get("prompt_eval_count"),
        "ollama_eval_count": result.get("eval_count"),
        # Server-side times of the answer phase; with thinking on, the counts above are
        # prompt + thinking (prompt_eval_count) and the final answer (eval_count).
        "ollama_prompt_eval_duration": result.get("prompt_eval_duration"),
        "ollama_eval_duration": result.get("eval_duration"),
        "ollama_done_reason": result.get("done_reason"),
        "client_duration_seconds": time.perf_counter() - request_started,
    }
    if not content.strip() and thinking:
        record["parse_error"] = "Qwen returned thinking but no final JSON content."
        _write_llm_call_record(record)
        raise ValueError(
            "Qwen returned thinking but no final JSON content. Increase max_tokens/num_predict or simplify the prompt."
        )
    try:
        parsed = extract_json_object(content)
    except Exception as exc:
        record["parse_error"] = str(exc)
        _write_llm_call_record(record)
        raise
    record["parsed_json"] = parsed
    _write_llm_call_record(record)
    return parsed


def enrich_step1_handoff(path: Path) -> None:
    massing = read_json(path)
    tiers = massing.get("tiers") or []
    if not tiers:
        return
    core_area = 252.0

    def polygon_area(points: list[list[float]]) -> float:
        total = 0.0
        for index, point in enumerate(points):
            nxt = points[(index + 1) % len(points)]
            total += float(point[0]) * float(nxt[1]) - float(nxt[0]) * float(point[1])
        return abs(total) * 0.5

    xs = [float(x) for tier in tiers for x, _ in tier["footprint"]]
    ys = [float(y) for tier in tiers for _, y in tier["footprint"]]
    tier_schedule = [
        {
            "story_start": int(tier["story_start"]),
            "story_end": int(tier["story_end"]),
            "footprint_vertices": tier["footprint"],
        }
        for tier in tiers
    ]
    total_net_area = sum(
        (polygon_area(tier["footprint"]) - core_area) * (int(tier["story_end"]) - int(tier["story_start"]))
        for tier in tiers
    )
    massing["footprint_vertices_json"] = json.dumps(tiers[0]["footprint"], separators=(",", ":"))
    massing["tier_schedule_json"] = json.dumps(tier_schedule, separators=(",", ":"))
    massing["rotation_deg"] = float(massing.get("rotation_deg", 0.0) or 0.0)
    massing["width_m"] = max(xs) - min(xs)
    massing["depth_m"] = max(ys) - min(ys)
    massing["total_net_floor_area_m2"] = total_net_area
    write_json(path, massing)


def _compact_design_spec(spec: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: spec.get(key)
        for key in (
            "description",
            "design_intent",
            "series_id",
            "air_side_options",
            *STEP2_DESIGN_KEYS,
            "air_process_design",
        )
        if spec.get(key) is not None
    }
    tool_plan = spec.get("tool_plan")
    if isinstance(tool_plan, dict):
        compact["tool_plan"] = {
            key: tool_plan.get(key)
            for key in ("strategy_summary", "tool_calls", "engineering_basis", "rationale")
            if tool_plan.get(key) is not None
        }
    return compact


def _compact_constraint_feedback(feedback: dict[str, Any]) -> dict[str, Any]:
    raw = feedback.get("physical_constraints_json")
    try:
        report = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        report = None
    if not isinstance(report, dict):
        return {}
    failed_checks = []
    for check in report.get("checks", []):
        if isinstance(check, dict) and check.get("ok") is False:
            failed_checks.append(
                {
                    "name": check.get("name"),
                    "message": check.get("message"),
                    "data": check.get("data", {}),
                }
            )
    return {
        "errors": report.get("errors", []),
        "warnings": report.get("warnings", []),
        "failed_checks": failed_checks,
    }


def generation_error_rows(events: list[dict[str, Any]], stage: str) -> list[str]:
    return [
        json.dumps(
            {
                "case_id": event.get("candidate_id"),
                "status": "generation_rejected_before_simulation",
                "error_category": event.get("error_category"),
                "error_message": event.get("error_message"),
                "next_iteration_rule": "Correct this exact contract error in the next proposal.",
            },
            ensure_ascii=False,
        )
        for event in events
        if event.get("event_type") == "llm_generation"
        and event.get("stage") == stage
        and event.get("outcome") == "error"
    ]


STEP3_VENTILATION_FIELDS = (
    "ventilation_max_time_below_voz_dyn_pct",
    "ventilation_max_time_below_voz_dyn_hours",
    "ventilation_min_occupied_oa_to_voz_dyn_ratio",
)


def step3_ventilation_results(output_root: Path) -> dict[str, dict[str, Any]]:
    """Minimum-ventilation results per Step 3 case, read from the stage's result table.

    The Step 3 feedback packet carries the ventilation check only as a failed
    constraint; the values of passing cases are in the result table.
    """
    path = output_root / "step3_hvac" / "hvac_toolkit_eui_results.csv"
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            str(row.get("case_id")): {key: _float_or_none(row.get(key)) for key in STEP3_VENTILATION_FIELDS}
            for row in csv.DictReader(handle)
        }


def summarize_previous_designs(output_root: Path, stage: str, limit: int = 30) -> str:
    """Design parameters of this run's earlier Step 2 or Step 3 proposals, without any result.

    The no_feedback condition shows these so that it differs from full_feedback
    only in the withheld validation and simulation results.
    """
    filename = {"step2": "envelope_spec.json", "step3": "hvac_spec.json"}[stage]
    rows = []
    for path in sorted((output_root / "agent_candidates").glob(f"*/{filename}")):
        spec = read_json(path)
        design = ({key: spec.get(key) for key in ("description", "design_intent") if spec.get(key) is not None}
                  if stage == "step2" else
                  {key: value for key, value in (("series_id", spec.get("series_id")),
                                                 ("air_side_options", spec.get("air_side_options")),
                                                 ("hvac_script", script_text(spec.get("hvac_script"), path.parent)))
                   if value})
        rows.append(json.dumps({"case_id": path.parent.name, "design": design}, ensure_ascii=False))
    return "\n".join(rows[-limit:])


def summarize_stage_feedback(iterations: Path, spec_filename: str | None = None, limit: int = 30) -> str:
    rows: list[str] = []
    output_root = iterations.parents[1]
    stage = "step2" if iterations.parent.name.startswith("step2") else "step3"
    ventilation = step3_ventilation_results(output_root) if stage == "step3" else {}
    attempt_log = output_root / "attempt_log.jsonl"
    if attempt_log.exists():
        events: list[dict[str, Any]] = []
        for line in attempt_log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        rows.extend(generation_error_rows(events, stage))
    case_ids = {path.parent.name for path in iterations.glob("*/feedback.json")}
    if spec_filename:
        case_ids.update(path.parent.name for path in (output_root / "agent_candidates").glob(f"*/{spec_filename}"))
    for case_id in sorted(case_ids):
        feedback_path = iterations / case_id / "feedback.json"
        feedback = read_json(feedback_path) if feedback_path.exists() else {}
        spec_path = output_root / "agent_candidates" / case_id / spec_filename if spec_filename else None
        spec = read_json(spec_path) if spec_path and spec_path.exists() else {}
        if spec.get("hvac_script") and spec_path is not None:
            spec = {**spec, "hvac_script": script_text(spec["hvac_script"], spec_path.parent)}
        rows.append(
            json.dumps(
                {
                    "case_id": feedback.get("case_id", case_id),
                    "design": _compact_design_spec(spec),
                    "status": feedback.get("status", "generated_without_feedback"),
                    "physical_constraints_ok": feedback.get("physical_constraints_ok"),
                    "selected_total_eui_kwh_m2": feedback.get("selected_total_eui_kwh_m2"),
                    "optimization_diagnostics": feedback.get("optimization_diagnostics"),
                    "heating_eui_kwh_m2": feedback.get("heating_eui_kwh_m2"),
                    "cooling_eui_kwh_m2": feedback.get("cooling_eui_kwh_m2"),
                    "fan_eui_kwh_m2": feedback.get("fan_eui_kwh_m2"),
                    "pump_eui_kwh_m2": feedback.get("pump_eui_kwh_m2"),
                    "heat_rejection_eui_kwh_m2": feedback.get("heat_rejection_eui_kwh_m2"),
                    "lighting_eui_kwh_m2": feedback.get("lighting_eui_kwh_m2"),
                    "equipment_eui_kwh_m2": feedback.get("equipment_eui_kwh_m2"),
                    "severe_count": feedback.get("severe_count"),
                    "fatal_count": feedback.get("fatal_count"),
                    "window_projection_audit_ok": feedback.get("window_projection_audit_ok"),
                    "max_window_projection_ratio": feedback.get("max_window_projection_ratio"),
                    "max_orientation_projection_ratio": feedback.get("max_orientation_projection_ratio"),
                    "max_perpendicular_shading_depth_m": feedback.get("max_perpendicular_shading_depth_m"),
                    "min_horizontal_board_vertical_spacing_m": feedback.get("min_horizontal_board_vertical_spacing_m"),
                    "min_vertical_board_horizontal_spacing_m": feedback.get("min_vertical_board_horizontal_spacing_m"),
                    "zone_temp_setpoint_unmet_hours": feedback.get("zone_temp_setpoint_unmet_hours"),
                    "zone_temp_setpoint_unmet_pct": feedback.get("zone_temp_setpoint_unmet_pct"),
                    "zone_rh_max_pct": feedback.get("zone_rh_max_pct"),
                    "zone_rh_hours_above_70": feedback.get("zone_rh_hours_above_70"),
                    "zone_rh_pct_above_70": feedback.get("zone_rh_pct_above_70"),
                    "pmv_occupied_comfort_pct": feedback.get("pmv_occupied_comfort_pct"),
                    "pmv_occupied_cold_pct": feedback.get("pmv_occupied_cold_pct"),
                    "pmv_occupied_hot_pct": feedback.get("pmv_occupied_hot_pct"),
                    "constraint_feedback": _compact_constraint_feedback(feedback),
                    **({"air_process_comparison": feedback.get("air_process_comparison"),
                        **ventilation.get(case_id, {})} if stage == "step3" else {}),
                    "next_iteration_rule": feedback.get("next_iteration_rule"),
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(rows[-limit:])


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def valid_energy_feedback(feedback: dict[str, Any]) -> bool:
    from optimization_feedback import valid_energy_result
    return valid_energy_result(feedback)


def select_step2_best(output_root: Path) -> str:
    step2 = output_root / "step2_envelope_layout"
    organized = step2 / "organized_current"
    candidates: list[dict[str, Any]] = []
    results_path = step2 / "envelope_eui_results.csv"
    if not results_path.exists():
        raise RuntimeError(f"Missing Step 2 result table: {results_path}")
    with results_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            # Feedback packets intentionally omit the large evidence map. The
            # result CSV is the hash-bound source of truth for cross-stage
            # handoff selection.
            normalized = dict(row)
            try:
                normalized["severe_count"] = int(float(row.get("severe_count") or -1))
                normalized["fatal_count"] = int(float(row.get("fatal_count") or -1))
            except (TypeError, ValueError):
                continue
            if audit_result_eligible(normalized):
                candidates.append(normalized)
    if not candidates:
        raise RuntimeError("No valid Step 2 cases available for global best selection.")

    best = min(candidates, key=lambda row: float(row["selected_total_eui_kwh_m2"]))
    organized.mkdir(parents=True, exist_ok=True)
    write_json(organized / "best_envelope.json", best)
    osm_path = Path(str(best.get("osm_path", "")))
    if not osm_path.exists():
        raise RuntimeError(f"Selected Step 2 best OSM is missing: {osm_path}")
    shutil.copy2(osm_path, organized / "best_envelope.osm")

    ranked = sorted(candidates, key=lambda row: float(row["selected_total_eui_kwh_m2"]))
    ranking = "\n".join(
        f"{index + 1}. {row.get('case_id')}: WWR {float(row.get('wwr') or 0):.3f}, "
        f"shade {float(row.get('horizontal_shade_depth_m') or 0):.1f} m, "
        f"total {float(row['selected_total_eui_kwh_m2']):.2f} kWh/m2"
        for index, row in enumerate(ranked)
    )
    (organized / "README.md").write_text(
        f"""# Step 2 Detailed Envelope Design

Step 2 global best was selected by the Qwen supervisor after all valid cases completed.

- Best case: `{best.get("case_id")}`
- Best selected EUI: **{float(best["selected_total_eui_kwh_m2"]):.2f} kWh/m2**

## Ranking

{ranking}
""",
        encoding="utf-8",
    )
    print(
        "Step 2 global best: "
        f"{best.get('case_id')} total={float(best['selected_total_eui_kwh_m2']):.4f} kWh/m2"
    )
    return str(best.get("case_id"))


def step3_air_process_context(repo: Path, location: str) -> dict[str, Any]:
    """Fixed hours, zone rule and weather states for the Step 3 psychrometric design."""
    step3_dir = str(repo / "steps" / "step3_hvac")
    if step3_dir not in sys.path:
        sys.path.insert(0, step3_dir)
    from psychrometric_evidence import design_hours, epw_design_hour_states, hour_label, site_pressure_from_epw

    hours = design_hours(read_json(repo / "config" / "block_m.json"))
    epw = Path(read_json(repo / "config" / "climate_profiles.json")[location]["weather_file"])
    epw = epw if epw.is_absolute() else repo / epw
    weather = epw_design_hour_states(epw, hours)
    return {
        "design_hours": {name: hour_label(when) for name, when in hours.items()},
        "site_pressure_kpa_for_saturation_check": round(site_pressure_from_epw(epw) / 1000, 1),
        "comparison_zone": "typical floor: the largest zone on the middle floor level of the Step 2 model",
        "outdoor_air_from_active_epw": {
            name: {
                "temperature_c": weather[name]["temperature_c"],
                "humidity_ratio_g_kg": round(1000 * weather[name]["humidity_ratio_kg_kg"], 2),
                "relative_humidity_pct": weather[name]["relative_humidity_pct"],
            }
            for name in hours if name in weather
        },
        "fixed_doas_conventions": [
            "DOAS supplies 100% outdoor air; the mixed state equals the outdoor state.",
            "The DOAS cooling coil controls leaving air to 12 C; air leaving it is close to saturation when it dehumidifies.",
            "The DOAS supply fan is downstream of the coils and adds sensible heat before zone supply.",
            "Outside Hong Kong, a series with heating uses a DOAS heating coil that heats supply air to 20 C; in Hong Kong the DOAS heating coil is disabled.",
            "FCU, beam and radiant terminals act on zone air; only an FCU has its own air outlet state.",
        ],
    }


def step3_prompt(
    repo: Path,
    output_root: Path,
    case_id: str,
    valid_count: int = 0,
    target_count: int = 10,
    condition: str = "full_feedback",
) -> str:
    catalog = read_text(repo / "steps" / "step3_hvac" / "hvac_series_catalog.json")
    step2_best = read_json(output_root / "step2_envelope_layout" / "organized_current" / "best_envelope.json")
    previous = ""
    if condition == "full_feedback":
        previous = summarize_stage_feedback(
            output_root / "step3_hvac" / "llm_iterations",
            spec_filename="hvac_spec.json",
        )
    climate = active_climate_prompt_context(repo, output_root)
    air_process = step3_air_process_context(repo, climate["location"])
    if condition == "schema_only":
        # Task and validation data only; no weather states or DOAS conventions.
        air_process = {key: air_process[key] for key in (
            "design_hours", "site_pressure_kpa_for_saturation_check", "comparison_zone")}
    timestep_per_hour = int(os.environ.get("STEP3_TIMESTEP_PER_HOUR", "6"))
    if condition == "full_feedback":
        strategy = staged_exploration_instruction(valid_count, target_count)
    elif condition == "no_feedback":
        strategy = (
            "Your previous designs of this run are listed below without results. Propose a design that differs "
            "from them; no validation or simulation results are available."
        )
    elif condition == "schema_only":
        strategy = "Return one valid candidate using only the declared task, bounds, and JSON contract."
    else:
        raise ValueError(f"Unsupported model condition: {condition}")
    feedback_rules = ""
    if condition == "full_feedback":
        feedback_rules = f"""
- Use the Step 2 best envelope, previous Step 3 feedback, EUI, comfort, humidity, and validity status to decide.
- Treat the attempted-design registry below as authoritative memory of what has already been generated and simulated.
- Do not assume the current best system is the global optimum. During exploration, prioritize an untested family, source combination, or parameter region over repeating the incumbent.
- You may revisit a previous family during refinement only when changing explicit parameters and stating the reference case, changed variables, and expected energy/comfort/humidity effect.
""".strip()
    history_section = ""
    if condition == "full_feedback":
        history_section = f"""
Attempted Step 3 design-and-result registry (regenerated before every LLM call):
{previous or "(none yet)"}
""".strip()
    elif condition == "no_feedback":
        history_section = f"""
Your previous Step 3 designs in this run (validation and simulation results intentionally withheld):
{summarize_previous_designs(output_root, "step3") or "(none yet)"}
""".strip()
    return f"""
Return one compact Step 3 HVAC JSON object for case_id {case_id}.
Do not include markdown or explanation. The final answer must start with {{.
EXPERIMENT_CONDITION={condition}

Decision rule:
- Primary objective: find the lowest selected_total_eui_kwh_m2 among physically valid Step 3 HVAC systems that also pass the operational-service gate.
- A case is eligible for final ranking only when valid occupied PMV evidence is present and the PMV-versus-original check passes. Air-thermostat unmet hours are diagnostic only.
- {strategy}
{feedback_rules}
- Treat EnergyPlus as deterministic. Different EUI values for the same executed HVAC configuration indicate a pipeline inconsistency, not a stochastic opportunity.
- Do not change massing, envelope, internal loads, schedules, infiltration, or weather. HVAC only.
- The supervisor runs Step 3 EnergyPlus at {timestep_per_hour} timesteps per hour; do not include or tune timestep in the HVAC JSON.
- Every eligible case must include a DOAS serving every zone. In the simulation each zone must receive its occupants' minimum outdoor air in at least 99% of occupied hours.
- Base heating/cooling, humidity, heat-pump, and standards decisions only on the active climate context below; do not assume Hong Kong conditions for a mainland location.

Required output: a catalog series (one of the 30 series_id values, optionally with air_side_options), and/or
an own OpenStudio Ruby script in hvac_script that changes the series or builds the whole system:
{{
  "description": "...",
  "design_intent": "...",
  "case_id": "{case_id}",
  "series_id": "one_supported_series_id (omit when hvac_script builds the whole system)",
  "air_side_options": {{"optional": "add_heat_recovery, hrv_sensible_effectiveness, hrv_latent_effectiveness, economizer_type, enable_dcv"}},
  "hvac_script": "optional: the complete Ruby script as one JSON string",
  "schedule_changes": {{"optional": "only when the design changes a thermostat schedule as the rules allow: setpoint_design_day_profiles {{reason}} and/or cooling_setpoint_offset {{delta_k, reason}}"}},
  "air_process_design": {{
    "load_basis": "weather, zone load and setpoint assumptions behind the states",
    "cooling": {{
      "process_notes": "one sentence per segment: outdoor -> coil -> fan -> zone supply -> zone, and the terminal",
      "states": {{
        "outdoor": {{"temperature_c": 0.0, "humidity_ratio_g_kg": 0.0}},
        "coil_outlet": {{"temperature_c": 0.0, "humidity_ratio_g_kg": 0.0}},
        "doas_zone_supply": {{"temperature_c": 0.0, "humidity_ratio_g_kg": 0.0}},
        "zone": {{"temperature_c": 0.0, "humidity_ratio_g_kg": 0.0}}
      }}
    }},
    "heating": {{
      "process_notes": "...",
      "states": {{
        "outdoor": {{"temperature_c": 0.0, "humidity_ratio_g_kg": 0.0}},
        "doas_zone_supply": {{"temperature_c": 0.0, "humidity_ratio_g_kg": 0.0}},
        "zone": {{"temperature_c": 0.0, "humidity_ratio_g_kg": 0.0}}
      }}
    }}
  }}
}}

Air-process design on the psychrometric chart (required, both scenarios):
- Predict the air states of your chosen system for the cooling and heating hours below, in the comparison zone. The supervisor plots them before simulation and overlays the EnergyPlus states at the same hour and zone afterwards.
- Optional states: "mixed", "heating_coil_outlet" (heating), "coil_outlet" (heating, when the coil still dehumidifies), "local_terminal_outlet" (FCU series), and "radiant_surface_temperature_c" in a scenario (radiant series; it is checked against the zone dew point).
- Every state must lie at or below saturation at the site pressure given below. A coil outlet cannot be warmer or more humid than its inlet; a heating coil and the fan change temperature only; zone supply keeps the coil-outlet humidity ratio.
- Prediction errors are feedback only; they do not affect eligibility or ranking.
{json.dumps(air_process, ensure_ascii=False)}

Executable design space:
- A catalog series_id runs with its fixed catalog values; air_side_options may switch on heat recovery, the DOAS
  economizer bypass (requires heat recovery) or DCV (DOAS+FCU and radiant, not chilled beams).
- hvac_script may change what the series built or build another system; any system is allowed when the built
  model passes the model-level rules below and the simulation checks. The supervisor writes hvac_script to
  hvac.rb next to the spec. Explicit tool plans are not used in this harness.
- No listed family, source or approach is recommended in advance. Decide from the active climate and the evidence.
- Provide heating where the active climate requires it.

{step3_script_text(repo)}

{climate_prompt_section(climate, condition)}

Selected Step 2 best envelope:
{json.dumps(compact_handoff(step2_best), ensure_ascii=False)}

{history_section}

Supported catalog:
{catalog}
""".strip()


def run_step3_cases(
    repo: Path,
    output_root: Path,
    count: int,
    env: dict[str, str],
    log_dir: Path,
    condition: str = "full_feedback",
    proposal_cap: int | None = None,
) -> list[str]:
    context_path = output_root / "active_project_context.json"
    cache_command = [
        sys.executable,
        "agents/qwen_all_energy/prepare_original_baseline_cache.py",
        "--repo",
        str(repo),
        "--output-root",
        str(output_root),
    ]
    if context_path.exists():
        cache_command.extend(["--project-context", str(context_path)])
    cache_code = run_command(
        cache_command,
        cwd=repo,
        env=env,
        log_path=log_dir / "step3_original_baseline_cache.log",
    )
    if cache_code != 0:
        raise RuntimeError(
            "Step 3 original-baseline PMV cache preparation failed; "
            "see step3_original_baseline_cache.log."
        )

    valid_ids = [
        path.parent.name
        for path in sorted((output_root / "step3_hvac" / "llm_iterations").glob("*/feedback.json"))
        if valid_energy_feedback(read_json(path))
    ][:count]
    attempted_signatures = existing_design_signatures(output_root, "step3")
    existing_indices: list[int] = []
    case_prefix = f"hvac_{CASE_NAMESPACE}_"
    existing_paths = list((output_root / "step3_hvac" / "llm_iterations").glob(f"{case_prefix}*"))
    existing_paths.extend((output_root / "agent_candidates").glob(f"{case_prefix}*"))
    for path in existing_paths:
        try:
            existing_indices.append(int(path.name.rsplit("_", 1)[-1]))
        except ValueError:
            continue
    start_index = max(existing_indices, default=0)
    if valid_ids:
        print(f"Step 3 existing valid cases: {len(valid_ids)}/{count}")

    # A previous infrastructure failure may have prevented already-generated,
    # unique Qwen proposals from being evaluated. Retry those exact proposals
    # before asking the model for any new design so recovery does not inflate the
    # proposal budget or change the experiment condition.
    existing_specs = sorted(
        (output_root / "agent_candidates").glob(f"{case_prefix}*/hvac_spec.json")
    )
    for spec_path in existing_specs:
        if len(valid_ids) >= count:
            break
        case_id = spec_path.parent.name
        feedback_path = output_root / "step3_hvac" / "llm_iterations" / case_id / "feedback.json"
        if feedback_path.exists() and valid_energy_feedback(read_json(feedback_path)):
            if case_id not in valid_ids:
                valid_ids.append(case_id)
            continue
        print(f"\n=== Step 3 infrastructure recovery: {case_id} ===")
        code = run_command(
            [
                sys.executable,
                "steps/step3_hvac/stage3_hvac_energyplus.py",
                "--json-spec",
                str(spec_path),
                "--case-id",
                case_id,
                "--overwrite",
            ],
            cwd=repo,
            env=env,
            log_path=log_dir / f"{case_id}_energyplus_recovery.log",
        )
        feedback = read_json(feedback_path) if feedback_path.exists() else {}
        is_valid = code == 0 and valid_energy_feedback(feedback)
        append_attempt_event(
            output_root,
            {
                "event_type": "simulation_validation",
                "condition": condition,
                "stage": "step3",
                "attempt_id": case_id,
                "candidate_id": case_id,
                "outcome": "valid" if is_valid else "invalid",
                "command_exit_code": code,
                "physical_constraints_ok": feedback.get("physical_constraints_ok"),
                "selected_total_eui_kwh_m2": feedback.get("selected_total_eui_kwh_m2"),
                "error_category": None if is_valid else "energy_simulation_or_constraints",
                "evidence_path": str(feedback_path),
                "details": {
                    "phase": "step3_energy_infrastructure_recovery",
                    "feedback_status": feedback.get("status"),
                    "reused_existing_llm_proposal": True,
                },
            },
        )
        if is_valid:
            valid_ids.append(case_id)
            print(f"Step 3 valid cases: {len(valid_ids)}/{count}")
        else:
            print(f"Step 3 recovered case did not become valid: {case_id}")

    attempts = 0
    prior_proposals = count_logged_proposals(output_root, "step3")
    max_attempts = (
        max(proposal_cap - prior_proposals, 0)
        if proposal_cap is not None
        else max((count - len(valid_ids)) * 5, 0)
    )
    while len(valid_ids) < count and attempts < max_attempts:
        attempts += 1
        case_id = f"{case_prefix}{start_index + attempts:03d}"
        print(f"\n=== Step 3 attempt {attempts}: {case_id} ===")
        try:
            spec = ask_json(
                step3_prompt(repo, output_root, case_id, len(valid_ids), count, condition),
                max_tokens=DEFAULT_NUM_PREDICT,
                log_label=f"step3_hvac_{case_id}",
            )
        except Exception as exc:
            if "CLAUDE_CAPACITY_LIMIT" in str(exc):
                raise
            print(f"Qwen Step 3 JSON generation failed: {exc}")
            continue
        spec["case_id"] = case_id
        if not spec.get("series_id"):
            spec.pop("series_id", None)
        if not spec.get("air_side_options"):
            spec.pop("air_side_options", None)
        if not spec.get("hvac_script"):
            spec.pop("hvac_script", None)
        if (spec.get("tool_plan") or spec.get("tool_calls")
                or not (isinstance(spec.get("series_id"), str) or isinstance(spec.get("hvac_script"), str))
                or (spec.get("air_side_options") and not isinstance(spec.get("series_id"), str))):
            print(f"Step 3 rejected a proposal outside the harness protocol (series_id and/or hvac_script): {case_id}")
            append_attempt_event(
                output_root,
                {
                    "event_type": "simulation_validation",
                    "condition": condition,
                    "stage": "step3",
                    "attempt_id": case_id,
                    "candidate_id": case_id,
                    "outcome": "invalid",
                    "physical_constraints_ok": False,
                    "error_category": "proposal_outside_protocol",
                    "details": {"phase": "proposal_validation"},
                },
            )
            continue
        signature = design_parameter_signature("step3", spec)
        if signature in attempted_signatures:
            print(f"Step 3 duplicate parameter configuration blocked before simulation: {case_id}")
            continue
        attempted_signatures.add(signature)
        spec_path = output_root / "agent_candidates" / case_id / "hvac_spec.json"
        spec = materialize_script(spec, "hvac_script", spec_path.parent, STEP3_SCRIPT_FILE)
        write_json(spec_path, spec)
        code = run_command(
            [
                sys.executable,
                "steps/step3_hvac/stage3_hvac_energyplus.py",
                "--json-spec",
                str(spec_path),
                "--case-id",
                case_id,
                "--overwrite",
            ],
            cwd=repo,
            env=env,
            log_path=log_dir / f"{case_id}_energyplus.log",
        )
        feedback_path = output_root / "step3_hvac" / "llm_iterations" / case_id / "feedback.json"
        feedback = read_json(feedback_path) if feedback_path.exists() else {}
        is_valid = code == 0 and valid_energy_feedback(feedback)
        append_attempt_event(
            output_root,
            {
                "event_type": "simulation_validation",
                "condition": condition,
                "stage": "step3",
                "attempt_id": case_id,
                "candidate_id": case_id,
                "outcome": "valid" if is_valid else "invalid",
                "command_exit_code": code,
                "physical_constraints_ok": feedback.get("physical_constraints_ok"),
                "selected_total_eui_kwh_m2": feedback.get("selected_total_eui_kwh_m2"),
                "error_category": None if is_valid else "energy_simulation_or_constraints",
                "evidence_path": str(feedback_path),
                "details": {"phase": "step3_energy", "feedback_status": feedback.get("status")},
            },
        )
        if is_valid:
            valid_ids.append(case_id)
            print(f"Step 3 valid cases: {len(valid_ids)}/{count}")
        else:
            print(f"Step 3 case did not become valid: {case_id}")
    if not valid_ids:
        raise RuntimeError(f"Produced no valid Step 3 cases after {attempts} attempts.")
    if len(valid_ids) < count and proposal_cap is None:
        raise RuntimeError(f"Only produced {len(valid_ids)} valid Step 3 cases after {attempts} attempts.")
    return valid_ids
