"""External scheduler adapter: benchmark, Opus 5.5 through Claude Code headless, genome only.

The frozen matrix launcher writes Ollama commands for the LLM runs; this adapter keeps every other argument
and runs each worker through local_agent_ablation.py with the Claude Code backend (`claude -p`, no tools, no
slash commands, structured output). Two additions around the frozen harness, recorded in
resident_launcher_manifest.json:
- every Claude call runs in an empty temporary folder with --strict-mcp-config, so the model sees only the
  prompt of its condition (as in the three-step ablation);
- a usage-limit reply ("You've hit your ... limit", rate limit, overloaded) is not a proposal: the call waits
  and is repeated (every 600 s, at most 48 h), and each wait is logged in capacity_events.jsonl.

    python launch_opus_genome.py --run-root <this folder> --step1-root ... --project-context ... \
        --model claude-opus-5-5 --methods llm_genome --llm-modes full_feedback --repeats 5 --budget 50 \
        --max-parallel 5 --search-space valid_range_v3 --simulation-timeout 5400
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess as real_subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

REPO = Path("SET_THIS_PATH")
METHODS = REPO / "benchmark_methods"
sys.path.insert(0, str(METHODS))
sys.path.insert(1, str(REPO))
MODEL = "claude-opus-5-5"
CLAUDE = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
CAPACITY = re.compile(r"hit your (\w+ )?limit|usage limit reached|rate limit|overloaded", re.IGNORECASE)
WAIT_SECONDS = 600
MAX_WAIT_SECONDS = 48 * 3600


def stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ClaudeSubprocess:
    """subprocess for local_agent_ablation: Claude calls in an empty folder, waiting out usage limits."""

    def __init__(self, events: Path):
        self.events = events

    def __getattr__(self, name):
        return getattr(real_subprocess, name)

    def run(self, command, *args, **kwargs):
        if not command or Path(str(command[0])).stem.lower() != "claude" or "-p" not in command:
            return real_subprocess.run(command, *args, **kwargs)
        command = [CLAUDE, *command[1:]]
        if "--strict-mcp-config" not in command:
            command.insert(command.index("-p") + 1, "--strict-mcp-config")
        # The harness appends the prompt as the last argument. A full-feedback prompt outgrows the Windows command
        # line (32,767 characters, WinError 206) after about twelve proposals, so it is passed on stdin instead,
        # as in the three-step ablation; the prompt itself is unchanged.
        if "input" not in kwargs and not str(command[-1]).startswith("-"):
            kwargs["input"] = command.pop()
        waited = 0
        while True:
            with tempfile.TemporaryDirectory() as folder:
                result = real_subprocess.run(command, *args, **{**kwargs, "cwd": folder})
            text = f"{result.stdout or ''} {result.stderr or ''}"
            failed = result.returncode != 0 or '"is_error":true' in (result.stdout or "").replace(" ", "")
            if not (failed and CAPACITY.search(text)):
                return result
            with self.events.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"time": stamp(), "pid": os.getpid(), "waited_seconds": waited,
                                         "reply": text.strip()[:300]}) + "\n")
            if waited >= MAX_WAIT_SECONDS:
                return result
            print(f"{stamp()} Claude capacity limit; waiting {WAIT_SECONDS} s (waited {waited / 60:.0f} min)", flush=True)
            time.sleep(WAIT_SECONDS)
            waited += WAIT_SECONDS


def matrix_adapter():
    import run_step2_benchmark_matrix as matrix
    original_jobs = matrix.jobs

    def adapted_jobs(args):
        result = original_jobs(args)
        for job in result:
            if job["group"] != "llm_genome":
                raise ValueError("This launcher is restricted to llm_genome")
            command = job["command"]
            command[command.index("--backend") + 1] = "claude_code"
            command[command.index("--model") + 1] = MODEL
            command[1:2] = [str(Path(__file__).resolve()), "--worker"]
        return result

    matrix.jobs = adapted_jobs
    return matrix


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        import local_agent_ablation as agent
        root = Path(sys.argv[sys.argv.index("--agent-root") + 1])
        root.mkdir(parents=True, exist_ok=True)
        agent.subprocess = ClaudeSubprocess(Path(__file__).resolve().parent / "capacity_events.jsonl")
        sys.argv = [str(METHODS / "local_agent_ablation.py"), *sys.argv[2:]]
        return agent.main()
    matrix = matrix_adapter()
    run_root = Path(sys.argv[sys.argv.index("--run-root") + 1]).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    sources = [Path(__file__).resolve(), METHODS / "run_step2_benchmark_matrix.py", METHODS / "local_agent_ablation.py",
               METHODS / "run_one_llm_step2_case.py", METHODS / "envelope_search_space.py"]
    commit = real_subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, text=True, capture_output=True).stdout.strip()
    dirty = bool(real_subprocess.run(["git", "status", "--porcelain"], cwd=REPO, text=True, capture_output=True).stdout.strip())
    version = real_subprocess.run([CLAUDE, "--version"], text=True, capture_output=True).stdout.strip()
    audit = dict(started=stamp(), launcher_pid=os.getpid(), python=sys.executable, argv=sys.argv,
                 frozen_repository=str(REPO), repository_commit=commit, repository_dirty=dirty,
                 backend="claude_code", model=MODEL, claude_cli=CLAUDE, claude_cli_version=version,
                 additions=["--strict-mcp-config and an empty temporary working folder for every Claude call",
                            "the prompt is passed on stdin instead of as the last argument (Windows command-line "
                            "limit, WinError 206)",
                            f"usage-limit replies wait {WAIT_SECONDS} s and repeat, at most {MAX_WAIT_SECONDS} s; "
                            "not counted as proposals (capacity_events.jsonl)"],
                 source_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources})
    (run_root / "resident_launcher_manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return matrix.main()


if __name__ == "__main__":
    raise SystemExit(main())
