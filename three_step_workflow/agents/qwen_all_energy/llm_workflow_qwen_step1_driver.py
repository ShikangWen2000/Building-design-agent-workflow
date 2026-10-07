from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from json import JSONDecodeError
from pathlib import Path
from typing import Any

MODEL = os.environ.get("QWEN_MODEL", "qwen3.8:latest")
BASE_URL = "http://localhost:11434/v1"
DEFAULT_REPO = Path(__file__).resolve().parents[2]
DEFAULT_NUM_PREDICT = int(os.environ.get("QWEN_NUM_PREDICT", "24576"))
DEFAULT_SEED = int(os.environ.get("QWEN_SEED", "901"))


def read_text(path: Path, max_chars: int | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    return text if max_chars is None else text[:max_chars]


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(stripped[index:])
        except JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("The model response did not contain a valid JSON object.")


def require_step1_spec(spec: dict[str, Any]) -> None:
    massing = spec.get("massing", {})
    tiers = massing.get("parametric_tiers") or massing.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("Generated spec must contain massing.parametric_tiers or massing.tiers.")
    for tier in tiers:
        if "story_start" not in tier or "story_end" not in tier:
            raise ValueError("Each tier must contain story_start and story_end.")
        if "parametric_tiers" not in massing and "footprint" not in tier:
            raise ValueError("Each explicit tier must contain footprint.")


def run_command(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    print("+ " + " ".join(command))
    completed = subprocess.run(command, cwd=cwd, env=env, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {' '.join(command)}")


def create_project_context(repo: Path, location: str, output_root_base: str, user_requirements: str) -> Path:
    command = [
        sys.executable,
        "steps/shared/create_project_context.py",
        "--location",
        location,
        "--output-root",
        output_root_base,
    ]
    if user_requirements:
        command.extend(["--user-requirements", user_requirements])
    run_command(command, cwd=repo, env=os.environ.copy())
    context_path = repo / "config" / "project_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    return Path(context["output_root"])
