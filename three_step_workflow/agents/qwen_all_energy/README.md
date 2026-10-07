# Three-step agent harness

This folder contains the LangGraph harness that runs the three-step workflow with a model that is called without
tools. The model returns one JSON proposal per call. The harness writes the proposal, runs the step scripts, and
returns the results to the model according to the experiment condition.

Two model backends are available:

- Ollama with a local model (default; `qwen3.8:27b` in the paper);
- Claude Code in headless mode (`AGENT_LLM_BACKEND=claude_code`): `claude -p` without tools, slash commands, or MCP
  servers, started in an empty temporary folder.

## Proposals

- Step 1: the parameters of the massing tiers.
- Step 2: `{"description", "design_intent", "envelope_script"}`, where `envelope_script` is a complete OpenStudio
  Ruby script. The harness writes it to `envelope.rb`, the step script runs it on a copy of the base model, and the
  model rules judge the result.
- Step 3: a `series_id` of the HVAC catalog with optional `air_side_options`, an `hvac_script` (written to
  `hvac.rb`) that changes the series or builds another system, or both.

A proposal whose Step 2 script, or whose Step 3 series, options, and script, repeat an earlier proposal of the run
is rejected as a duplicate. The comparison uses the text of the proposal. A rejected proposal counts as a proposal.

## Conditions

| Condition | Prompt | History of the run in the prompt |
|---|---|---|
| `full_feedback` | task, rules, design guidance, use of feedback | earlier proposals with their validation and simulation results, the latest script, and the best valid script |
| `no_feedback` | task, rules, design guidance | earlier proposals and the latest script, without results |
| `schema_only` | task and rules | none |

Every run starts with an empty history. Proposals or results of other runs are not added to the prompt.

## Files

| File | Content |
|---|---|
| `langgraph_qwen_all_energy_supervisor.py` | Entry point. Runs the three steps in sequence, selects the valid candidate with the lowest EUI in each step, and writes the run records. |
| `llm_workflow_qwen_pilot_supervisor.py` | Model calls, Step 3 prompt, Step 3 runs, and the log of the model calls. |
| `open_design_agent.py` | Step 2 script proposals and the prompt sections quoted from the step documents. |
| `llm_workflow_qwen_step1_driver.py` | Client settings, JSON extraction, and the check of the Step 1 proposal. |
| `run_agent_ablation_matrix.py` | Runs every condition at every seed with a fixed number of proposals per step. |
| `summarize_agent_ablation_matrix.py` | Tables of the ablation runs per run and per condition. |
| `prepare_original_baseline_cache.py` | Simulates the baseline model once and stores its results for the comfort and ventilation checks. |
| `backfill_step3_zone_conditions.py` | Adds the zone conditions of a finished Step 3 case to its result record. |
| `reset_step3.py` | Moves the Step 3 results of a run aside so that Step 3 can be run again. |
| `../run_manifest.schema.json` | Schema of `run_manifest.json`. |

## Requirements

- Python with the packages of `requirements.txt` (`openai`, `langgraph`, `jsonschema`).
- For the local backend, an Ollama server at `http://localhost:11434` with the model:

```powershell
ollama pull qwen3.8:27b
```

- The workflow of this repository configured with the paths of OpenStudio and EnergyPlus
  (`three_step_workflow/config/`).

## Settings

The settings are environment variables.

| Variable | Default | Value in the paper |
|---|---|---|
| `QWEN_MODEL` | `qwen3.8:latest` | `qwen3.8:27b` |
| `QWEN_NUM_CTX` | 65536 | 131072 in the three-step runs, 65536 in the benchmark |
| `QWEN_NUM_PREDICT` | 24576 | 24576 |
| `QWEN_SEED` | 901 | 901, 1901, and 2901 for the three repeats |
| `QWEN_REQUEST_TIMEOUT` (s) | 900 | 3600 in the ablation runs |
| `AGENT_LLM_BACKEND` | `ollama` | `ollama`; `claude_code` in the feedback ablation |
| `EXPERIMENT_CONDITION` | `full_feedback` | see Conditions |
| `STEP2_TIMESTEP_PER_HOUR`, `STEP3_TIMESTEP_PER_HOUR` | 6 | 6 |

The seed and the settings of the run are written to `run_manifest.json`.

## Run

Run the commands from the folder `three_step_workflow/`.

Three-step run (20, 10, and 10 valid designs; at most 60, 30, and 30 proposals):

```powershell
$env:QWEN_MODEL = "qwen3.8:27b"
$env:QWEN_NUM_CTX = "131072"
$env:QWEN_SEED = "901"
python agents\qwen_all_energy\langgraph_qwen_all_energy_supervisor.py `
  --step1-count 20 --step1-proposal-cap 60 `
  --step2-count 10 --step2-proposal-cap 30 `
  --step3-count 10 --step3-proposal-cap 30 `
  --output-root-base <output folder>
```

Feedback ablation (three conditions, three seeds, 20, 10, and 10 proposals per step):

```powershell
python agents\qwen_all_energy\run_agent_ablation_matrix.py `
  --run-root <output folder> --backend claude_code --model claude-opus-5-5 `
  --seeds 901 1901 2901 --step1-budget 20 --step2-budget 10 --step3-budget 10 --max-parallel 3
python agents\qwen_all_energy\summarize_agent_ablation_matrix.py --run-root <output folder>
```

Invalid JSON, duplicate proposals, and failed simulations count as proposals.

With `--fixed-step1-handoff <folder>` and `--step1-count 0`, a run starts from the selected Step 1 design of a
finished run and carries out the later steps only.

## Outputs

| Path in the output folder | Content |
|---|---|
| `agent_candidates/.../massing_spec.json`, `envelope_spec.json`, `hvac_spec.json` | Proposals as parsed from the model output |
| `llm_raw_outputs/` | Prompt, model output, parsed JSON, time, and token counts of every call |
| `attempt_log.jsonl` | One record per proposal and per simulation |
| `energy_decision_record.json` | Proposals with design intent, EUI, and selection |
| `run_manifest.json` | Model, harness, settings, seed, requested and valid designs, time, and hardware |
| `step1_massing_energy/organized_current/best_massing_energy.json` | Selected Step 1 design |
| `step2_envelope_layout/organized_current/best_envelope.json` | Selected Step 2 design |
| `step3_hvac/llm_iterations/.../feedback.json` | Simulation results and rule checks of each Step 3 case |

EnergyPlus output files above 10 MB are deleted after each simulation. The results stay in the CSV and JSON files.

## Selection

In each step, the valid candidate with the lowest `selected_total_eui_kwh_m2` is selected. A candidate that fails a
rule is not eligible. The baseline is reported separately and is not a candidate.
