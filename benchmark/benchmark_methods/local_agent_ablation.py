"""Reproducible Step-2 LLM ablations under one provider-neutral agent loop.

The model backend reads a prompt from stdin and returns one normalized genome.
Claude Code and local models receive the same prompt/history and have no direct
filesystem, shell, MCP, simulation, or benchmark-result access. The Python
harness alone decodes, simulates, scores, and selects the feedback packet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen
from urllib.request import Request

from run_one_llm_step2_case import read_json, write_json
from envelope_search_space import (
    FACADE_KEYS, SEARCH_SPACE_VERSION, SPACE, WINDOW_PARAMETER_GENES, WINDOW_TYPES,
    facade_genes, genome_to_physical, normalize_genome, physical_output_schema, physical_to_genome, prompt_sections,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODES = ("full_feedback", "no_simulation_feedback", "schema_only", "engineer_rule")
GENOME_KEYS = FACADE_KEYS
OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "hypothesis": {"type": "string"},
        "genome": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "window_parameters": {"type": "array", "minItems": WINDOW_PARAMETER_GENES, "maxItems": WINDOW_PARAMETER_GENES,
                                      "items": {"type": "number", "minimum": 0, "maximum": 1}},
                "facades": {
                    "type": "object", "additionalProperties": False,
                    "properties": {orientation: {
                        "type": "object", "additionalProperties": False,
                        "properties": {key: {"type": "number", "minimum": 0, "maximum": 1} for key in GENOME_KEYS},
                        "required": list(GENOME_KEYS),
                    } for orientation in ("north", "east", "south", "west")},
                    "required": ["north", "east", "south", "west"],
                },
            },
            "required": ["window_parameters", "facades"],
        },
    },
    "required": ["hypothesis", "genome"],
}


def extract_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Model response contains no JSON object")


# Options of the pre-registered rule that the active search space offers.
RULE_WINDOWS = tuple(name for name in ("offset_window", "continuous_window", "floor_to_ceiling", "punched_window")
                     if name in WINDOW_TYPES)
RULE_SHADINGS = tuple(name for name in ("horizontal", "louver", "perforated_panel", "ledge")
                      if all(name in SPACE["windows"][window]["shading_types"] for window in RULE_WINDOWS))
RULE_DEPTHS = (0.30, 0.45, 0.60, 0.75, 0.90)
RULE_WWRS = (0.50, 0.52, 0.54, 0.56)


def engineer_rule_choice(index: int) -> tuple[str, str, float, float]:
    """(window type, sun-facade shading, depth, WWR) of proposal `index`.

    Shading cycles fastest and the window type once per shading cycle, so each
    option appears equally often. The (shading, window) pairs repeat after one
    block; depth shifts with that block so every proposal is distinct.
    """
    block = index // (len(RULE_SHADINGS) * len(RULE_WINDOWS))
    shading = RULE_SHADINGS[index % len(RULE_SHADINGS)]
    window = RULE_WINDOWS[(index // len(RULE_SHADINGS)) % len(RULE_WINDOWS)]
    depth = RULE_DEPTHS[(index + block) % len(RULE_DEPTHS)]
    # Punched modules cannot realize WWR above 0.50 on the 4 m story.
    wwr = 0.50 if window == "punched_window" else RULE_WWRS[(index // 2 + block) % len(RULE_WWRS)]
    return window, shading, depth, wwr


def engineer_rule_design(index: int) -> dict[str, Any]:
    """Pre-registered cooling-climate facade rule; no access to simulation feedback.

    Low WWR on every facade, no shading on the north facade, and horizontal
    overhangs, louvers, perforated panels or floor-edge ledges on the east,
    south and west facades, cycling window type, shading, depth and WWR.
    Windows are 2.0 m tall on a 0.8 m sill where the type and the active space
    allow it; overhangs sit 0.1 m above the head without side extension.
    Vertical and combined fins are excluded: at the corners of a faceted plan
    they cannot keep the audited 3 m fin spacing.
    """
    window, shading, depth, wwr = engineer_rule_choice(index)
    facades = {
        orientation: facade_genes(
            window_type=window, wwr=wwr, window_height_m=2.0, sill_height_m=0.8,
            shading_type="none" if orientation == "north" else shading, depth_m=depth,
            louver_spacing_m=3.0, overhang_gap_m=0.10, overhang_side_extension_m=0.0)
        for orientation in ("north", "east", "south", "west")
    }
    return normalize_genome({"window_parameters": [0.25, 0.5, 0.75], "facades": facades})


PROMPT_FILES = {
    # schema_only: task, design space, hard limits and response format only.
    "contract": "LLM_STEP2_MIXED_SPACE_PROMPT.md",
    # Design guidance for modes that see earlier proposals.
    "strategy": "LLM_STEP2_STRATEGY_PROMPT.md",
    # How to use simulation results; full_feedback only.
    "feedback": "LLM_STEP2_FEEDBACK_PROMPT.md",
}
MODE_PROMPT_PARTS = {
    "full_feedback": ("contract", "strategy", "feedback"),
    "no_simulation_feedback": ("contract", "strategy"),
    "schema_only": ("contract",),
}


def selected_massing_summary(step1_root: Path) -> str:
    """One-line description of the fixed Step 1 handoff for the prompt."""
    best = read_json(step1_root / "organized_current" / "best_massing.json", {}) or {}
    tiers = []
    for tier in best.get("tiers") or []:
        source = tier.get("parametric_source") or {}
        shape = str(source.get("shape") or "polygon")
        if shape == "regular_polygon":
            shape = f"{source.get('sides')}-sided regular polygon"
        tiers.append(f"floors {tier.get('story_start')}-{tier.get('story_end')} {shape}")
    return (f"`{best.get('candidate_id')}`, {best.get('stories')} stories, "
            f"rotation {best.get('rotation_deg', 0)} deg; " + "; ".join(tiers))


HISTORY_LIMIT = 30  # same window as the three-step local agent


def _rounded(value: Any) -> Any:
    """Genome copy with four-decimal genes, so that a long history fits the context."""
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


REPRESENTATIONS = ("genome", "physical")


def shown_design(row: dict[str, Any], representation: str) -> dict[str, Any]:
    """An earlier proposal as the model wrote it: genes, or the physical values they decode to."""
    genome = row.get("genome")
    if representation == "physical":
        return {"design": genome_to_physical(genome) if genome else None}
    return {"genome": _rounded(genome)}


def prompt_for(mode: str, index: int, history: list[dict[str, Any]],
               selected_massing: str = "the fixed Step 1 handoff massing", representation: str = "genome") -> str:
    here = Path(__file__).parent
    parts = [(here / PROMPT_FILES[part]).read_text(encoding="utf-8") for part in MODE_PROMPT_PARTS[mode]]
    prompt = "\n\n".join(parts).replace("{selected_massing}", selected_massing)
    for name, text in {**prompt_sections(representation), "search_space": SEARCH_SPACE_VERSION}.items():
        prompt = prompt.replace("{" + name + "}", text)
    prompt += f"\n\nReturn proposal {index} as one JSON object only."
    if mode == "full_feedback":
        feedback = []
        shown = history[-HISTORY_LIMIT:]
        valid = [row for row in history if (row.get("evaluation") or {}).get("valid")]
        best = min(valid, key=lambda row: float(row["evaluation"]["score"]), default=None)
        if best is not None and all(best is not row for row in shown):
            shown = [best, *shown]  # the best valid design stays visible after it leaves the window
        for row in shown:
            evaluation = row.get("evaluation") or {}
            result = evaluation.get("result") or {}
            constraint_errors: list[str] = []
            wwr_mismatches: dict[str, Any] = {}
            try:
                constraint_report = json.loads(result.get("physical_constraints_json") or "{}")
                constraint_errors = list(constraint_report.get("errors") or [])[:5]
                # Requested and realized window-to-wall ratio of each rejected orientation.
                for check in constraint_report.get("checks") or []:
                    if check.get("name") == "realized_orientation_wwr_matches_request" and not check.get("ok", True):
                        wwr_mismatches = (check.get("data") or {}).get("mismatches") or {}
            except (TypeError, json.JSONDecodeError):
                constraint_errors = []
            feedback.append({
                "case_id": row.get("case_id"), **shown_design(row, representation),
                "proposal_error": row.get("proposal_error"), "valid": evaluation.get("valid"),
                "score_eui_kwh_m2": evaluation.get("score"), "status": result.get("status"),
                "physical_constraints_ok": result.get("physical_constraints_ok"),
                "severe_count": result.get("severe_count"), "fatal_count": result.get("fatal_count"),
                "constraint_errors": constraint_errors,
                "realized_wwr_mismatches": wwr_mismatches,
                "max_window_projection_ratio": result.get("max_window_projection_ratio"),
                "max_perpendicular_shading_depth_m": result.get("max_perpendicular_shading_depth_m"),
                "min_horizontal_board_vertical_spacing_m": result.get("min_horizontal_board_vertical_spacing_m"),
                "min_vertical_board_horizontal_spacing_m": result.get("min_vertical_board_horizontal_spacing_m"),
            })
        prompt += "\nPrevious controlled proposal/evaluation history:\n" + json.dumps(feedback, ensure_ascii=False)
    elif mode == "no_simulation_feedback":
        designs = [{"case_id": row.get("case_id"), **shown_design(row, representation)}
                   for row in history[-HISTORY_LIMIT:]]
        prompt += "\nYour previous designs (simulation results intentionally withheld):\n" + json.dumps(designs, ensure_ascii=False)
    elif mode == "schema_only":
        prompt += "\nNo prior designs or simulation feedback are available in this ablation."
    return prompt


OLLAMA_SYSTEM_MESSAGE = (
    "You produce strict JSON only. Keep internal reasoning concise and reserve "
    "enough generation budget for the final JSON. Do not explain in the final answer."
)
OLLAMA_OPTIONS = {"temperature": 0.7, "top_p": 0.9, "num_ctx": 65536, "num_predict": 24576}


def model_design(
    command: list[str], prompt: str, timeout: int, *, backend: str, model: str | None, seed: int,
    representation: str = "genome",
) -> tuple[dict[str, Any], str, float, dict[str, Any], str]:
    started = time.perf_counter()
    if backend == "ollama":
        # Same call as the three-step local agent: chat endpoint, thinking on,
        # the same sampling and context settings. Thinking with a JSON schema
        # returns an empty answer on /api/generate, so the chat endpoint is used.
        body = json.dumps({
            "model": model,
            "messages": [{"role": "system", "content": OLLAMA_SYSTEM_MESSAGE}, {"role": "user", "content": prompt}],
            "stream": False, "format": output_schema(representation), "think": True,
            "options": {**OLLAMA_OPTIONS, "seed": seed},
        }).encode("utf-8")
        request = Request("http://127.0.0.1:11434/api/chat", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=timeout) as response:
            ollama_result = json.load(response)
        message = ollama_result.get("message") or {}
        raw = str(message.get("content") or "")
        result_returncode, result_stdout, result_stderr = 0, raw, ""
        backend_result = {key: ollama_result.get(key) for key in (
            "model", "created_at", "done_reason", "total_duration", "load_duration",
            "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration",
        )}
        backend_result["thinking"] = str(message.get("thinking") or "")
        backend_result["thinking_chars"] = len(backend_result["thinking"])
    else:
        # A positional prompt is reliable on native Windows; piped stdin can be
        # consumed inconsistently by launchers. Tools remain explicitly empty.
        result = subprocess.run(
            [*command, prompt], text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        )
        result_returncode, result_stdout, result_stderr = result.returncode, result.stdout, result.stderr
        backend_result = {}
    elapsed = time.perf_counter() - started
    if result_returncode:
        raise RuntimeError(f"Model command failed ({result_returncode}): {result_stderr[-2000:]}")
    outer = extract_json(result_stdout)
    metadata: dict[str, Any] = backend_result
    if outer.get("type") == "result" and isinstance(outer.get("structured_output"), dict):
        proposal = outer["structured_output"]
        metadata = {
            "session_id": outer.get("session_id"), "total_cost_usd": outer.get("total_cost_usd"),
            "usage": outer.get("usage"), "model_usage": outer.get("modelUsage"),
            "num_turns": outer.get("num_turns"), "stop_reason": outer.get("stop_reason"),
        }
    elif outer.get("type") == "result" and isinstance(outer.get("result"), str):
        proposal = extract_json(outer["result"])
        metadata = {
            "session_id": outer.get("session_id"), "total_cost_usd": outer.get("total_cost_usd"),
            "usage": outer.get("usage"), "model_usage": outer.get("modelUsage"),
            "num_turns": outer.get("num_turns"), "stop_reason": outer.get("stop_reason"),
        }
    else:
        proposal = outer
    hypothesis = str(proposal.get("hypothesis") or "Model returned no hypothesis.").strip()
    if representation == "physical":
        # Physical values are encoded into the shared genome; every method is decoded by the same decoder.
        genome = physical_to_genome(proposal.get("design", proposal))
    else:
        genome = normalize_genome(proposal.get("genome", proposal))
    return genome, result_stdout, elapsed, metadata, hypothesis


def output_schema(representation: str) -> dict[str, Any]:
    return physical_output_schema() if representation == "physical" else OUTPUT_SCHEMA


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolved_model_command(args: argparse.Namespace) -> list[str]:
    if args.model_command:
        return args.model_command
    if args.backend == "claude_code":
        command = [
            "claude", "-p", "--model", args.model, "--tools", "",
            "--disable-slash-commands", "--no-session-persistence", "--output-format", "json",
            "--json-schema", json.dumps(output_schema(args.representation), separators=(",", ":")),
        ]
        if args.max_budget_usd_per_proposal is not None:
            command.extend(["--max-budget-usd", str(args.max_budget_usd_per_proposal)])
        return command
    if args.backend == "ollama":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
            Path(shutil.which("ollama") or ""),
        ]
        executable = next((path for path in candidates if path.is_file() and path.stat().st_size > 0), None)
        if executable is None:
            raise FileNotFoundError("A non-empty Ollama executable was not found")
        return [str(executable), "run", args.model]
    raise ValueError("Use --model-command, or select --backend and --model")


def backend_identity(backend: str, model: str | None, command: list[str] | None) -> dict[str, Any]:
    identity: dict[str, Any] = {"backend": backend, "requested_model": model}
    if backend == "claude_code":
        result = subprocess.run(
            [command[0], "--version"], text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=30,
        )
        identity["cli_version"] = result.stdout.strip()
    elif backend == "ollama":
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as response:
            models = json.load(response).get("models", [])
        match = next((item for item in models if item.get("name") == model), None)
        if match is None:
            raise RuntimeError(f"Ollama model tag is not installed: {model}")
        identity.update({
            "resolved_model": match.get("model"), "digest": match.get("digest"),
            "size": match.get("size"), "details": match.get("details"),
        })
    return identity


def main() -> int:
    parser = argparse.ArgumentParser(description="Run controlled Step-2 local-agent ablations.")
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--representation", choices=REPRESENTATIONS, default="genome",
                        help="What the model writes: normalized genes, or physical values that the harness "
                             "encodes into the same genome.")
    parser.add_argument("--agent-root", type=Path, required=True)
    parser.add_argument("--step1-root", type=Path, required=True)
    parser.add_argument("--project-context", type=Path, required=True)
    parser.add_argument("--proposal-budget", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20260905)
    parser.add_argument("--model-command", nargs="+", help="Executable and arguments; omitted only for engineer_rule.")
    parser.add_argument("--backend", choices=("claude_code", "ollama", "command"), default="command")
    parser.add_argument("--model", help="Exact backend model identifier; required unless --model-command is supplied.")
    parser.add_argument("--max-budget-usd-per-proposal", type=float)
    parser.add_argument("--model-timeout", type=int, default=1800)
    parser.add_argument("--simulation-timeout", type=int, default=2400)
    args = parser.parse_args()
    if args.mode != "engineer_rule" and not args.model_command and not args.model:
        parser.error("--model is required when --model-command is not supplied")
    model_command = None if args.mode == "engineer_rule" else resolved_model_command(args)
    identity = ({"backend": "deterministic", "requested_model": None} if args.mode == "engineer_rule"
                else backend_identity(args.backend, args.model, model_command))

    root = args.agent_root.resolve() / args.mode
    selected_massing = selected_massing_summary(args.step1_root.resolve())
    root.mkdir(parents=True, exist_ok=True)
    records_path = root / "ablation_evaluations.jsonl"
    history = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()] if records_path.exists() else []
    write_json(root / "experiment_manifest.json", {
        "mode": args.mode, "representation": None if args.mode == "engineer_rule" else args.representation,
        "proposal_budget": args.proposal_budget, "seed": args.seed,
        "backend": args.backend, "model": args.model, "model_command": model_command,
        "backend_identity": identity,
        "generation": ({"endpoint": "/api/chat", "think": True, **OLLAMA_OPTIONS} if args.backend == "ollama" else None),
        "model_seed_control": (f"Ollama generation seed={args.seed}" if args.backend == "ollama"
                               else "Claude Code exposes no seed control"),
        "prompt_sha256": {part: file_sha256(Path(__file__).with_name(name)) for part, name in PROMPT_FILES.items()},
        "prompt_parts": list(MODE_PROMPT_PARTS.get(args.mode, ())),
        "selected_massing": selected_massing,
        "search_space": SEARCH_SPACE_VERSION,
        "decoder_sha256": file_sha256(Path(__file__).with_name("envelope_search_space.py")),
        "runner_sha256": file_sha256(Path(__file__).with_name("run_one_llm_step2_case.py")),
        "budget_rule": "every proposal consumes one unit",
        "step1_root": str(args.step1_root.resolve()), "project_context": str(args.project_context.resolve()),
    })

    for index in range(len(history) + 1, args.proposal_budget + 1):
        raw_response = ""
        model_seconds = 0.0
        backend_metadata: dict[str, Any] = {}
        hypothesis_text = f"Deterministic engineer-rule proposal {index}."
        proposal_error = None
        try:
            if args.mode == "engineer_rule":
                genome = engineer_rule_design(index - 1)
            else:
                genome, raw_response, model_seconds, backend_metadata, hypothesis_text = model_design(
                    model_command, prompt_for(args.mode, index, history, selected_massing, args.representation),
                    args.model_timeout, backend=args.backend, model=args.model, seed=args.seed + index - 1,
                    representation=args.representation,
                )
        except Exception as exc:
            genome, proposal_error = {}, f"{type(exc).__name__}: {exc}"
            hypothesis_text = f"Proposal generation failed: {proposal_error}"

        case_id = f"{args.mode}_{index:03d}"
        proposal_dir = root / "proposals" / case_id
        write_json(proposal_dir / "genome.json", genome)
        (proposal_dir / "model_response.txt").write_text(raw_response, encoding="utf-8")
        thinking_text = backend_metadata.pop("thinking", None)
        if thinking_text:
            (proposal_dir / "model_thinking.txt").write_text(thinking_text, encoding="utf-8")
        hypothesis = proposal_dir / "hypothesis.md"
        hypothesis.write_text(hypothesis_text + "\n", encoding="utf-8")
        simulation_started = time.perf_counter()
        returncode = None
        evaluation: dict[str, Any] = {}
        if proposal_error is None:
            command = [
                sys.executable, str(Path(__file__).with_name("run_one_llm_step2_case.py")),
                "--agent-root", str(root), "--step1-root", str(args.step1_root),
                "--project-context", str(args.project_context), "--case-id", case_id,
                "--genome", str(proposal_dir / "genome.json"), "--hypothesis", str(hypothesis),
                "--attempt-index", str(index), "--valid-target-index", str(index),
                "--timeout", str(args.simulation_timeout),
            ]
            returncode = subprocess.run(command, cwd=REPO_ROOT).returncode
            evaluation = read_json(root / "generated_candidates" / case_id / "evaluation.json", {}) or {}
        record = {
            "case_id": case_id, "proposal_index": index, "mode": args.mode, "genome": genome,
            "proposal_error": proposal_error, "model_elapsed_seconds": round(model_seconds, 3),
            "backend_metadata": backend_metadata,
            "hypothesis": hypothesis_text,
            "simulation_elapsed_seconds": round(time.perf_counter() - simulation_started, 3),
            "runner_returncode": returncode, "evaluation": evaluation, "proposal_consumed": True,
        }
        with records_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        history.append(record)

    valid = [row for row in history if row.get("evaluation", {}).get("valid")]
    best = min(valid, key=lambda row: float(row["evaluation"]["score"])) if valid else None
    write_json(root / "ablation_summary.json", {
        "mode": args.mode, "proposal_count": len(history), "proposal_budget": args.proposal_budget,
        "valid_count": len(valid), "valid_fraction": len(valid) / len(history) if history else 0,
        "best_case_id": best.get("case_id") if best else None,
        "best_eui_kwh_m2": best.get("evaluation", {}).get("score") if best else None,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
