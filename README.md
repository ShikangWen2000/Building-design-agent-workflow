# LLM-based design-agent workflow for early-stage building design

Material of the paper

> S. Wen, R. You, Q. Chen. A Large Language Model (LLM)-based design-agent workflow for generating and screening
> simulation-ready candidates in early-stage building design.

An LLM agent proposes one design candidate at a time in three steps (massing, envelope, HVAC concept). Deterministic
scripts build the OpenStudio model, check it, simulate it with EnergyPlus, and rank the valid candidates. The agent
reads the feedback and proposes the next candidate.

This release contains the design briefs and rules, the agent instructions,
the tool and Model Context Protocol (MCP) interfaces, the local agent harness, the benchmark methods, and example
candidates. The model-generation and simulation scripts and the result tables will be added when the paper is
published.

## Contents

| Folder | Content | Section of the paper |
|---|---|---|
| `three_step_workflow/` | Instructions of the three steps, validation rules (`physical_constraints.md`), HVAC system catalog and specification contract, tool descriptions and MCP server interface (`hvac_toolkit/`), configuration, and the local agent harness (`agents/`) | 2.2–2.5, 3.2–3.4, 3.5.1 |
| `no_toolkit_three_step/` | The same design task for an agent without the workflow tools: task, brief, and rules | 3.3 |
| `benchmark/` | Step 2 envelope benchmark: search space and decoder, genetic algorithm, Bayesian optimization, random and space-filling search, engineering rule, LLM prompts and launchers | 3.5.3 |
| `style_generation/` | Style-referenced generation task: task, brief, and rules | 3.6.1 |
| `image_reconstruction/` | Image-based reconstruction task: five input images, task, brief, and rules | 3.6.2 |
| `examples/` | The selected candidate of each step of the representative Hong Kong run | 3.2 |
| `docs/` | Requirements for the baseline model | 3.1 |

## Baseline model

The OpenStudio model of the case building is not distributed. The code and the configuration contain no property
of that building other than the design brief (site size and location, number of stories, floor-area target, core size); all other
values are read from the baseline model when a run starts:

- Step 1 and Step 2 build each candidate on the baseline model: constructions, internal loads, schedules,
  thermostats, infiltration, zone sizing settings, and the HVAC system are inherited, and only the geometry
  (Step 1) and the windows and shading (Step 2) change.
- Step 3 reads the fan pressure rises, the pump head, the terminal-fan efficiencies, and the chiller COP of the
  baseline. A candidate may not lower a pressure or a head below the baseline value, the terminal-fan efficiency
  may not exceed the baseline value, and a plant COP must lie between the code minimum and the low-lift credit, so
  energy cannot be saved by changing these inputs.
- The model rules compare every candidate model with the baseline: thermal zones, internal loads and their
  schedules, the outdoor-air requirement, and constructions must be unchanged.
- The comfort check compares the share of occupied hours within the predicted mean vote band with that of the
  baseline simulation.

`docs/baseline_model.md` lists what a baseline model must contain.

## Agents

| Configuration | Harness | Instructions |
|---|---|---|
| Claude Opus 5.5, Claude Sonnet 5.5 | Claude Code | `three_step_workflow/README.md`, step READMEs, `hvac_toolkit/stage3_tool_prompt.md` |
| GPT-6.1 Sol, GPT-6 Sol | Codex | the same files |
| Qwen3.8:27B (local) | LangGraph harness with Ollama | `three_step_workflow/agents/qwen_all_energy/` |
| Claude Opus 5.5, feedback ablation | LangGraph harness, Claude Code in headless mode without tools | `three_step_workflow/agents/qwen_all_energy/run_agent_ablation_matrix.py` |
| Claude Opus 5.5 without workflow tools | Claude Code | `no_toolkit_three_step/TASK.md` |

Settings of the local agent (`agents/qwen_all_energy/llm_workflow_qwen_pilot_supervisor.py`): Ollama with
`qwen3.8:27b` (Q4_K_M), thinking enabled, temperature 0.7, top-p 0.9, up to 24,576 output tokens per call, and a
context window set with `QWEN_NUM_CTX` (131,072 tokens in the three-step runs and 65,536 tokens in the benchmark).
The harness sends one prompt per proposal with the task, the rules, and, depending on the ablation condition, the
earlier proposals and their results.

## Software

OpenStudio Application 1.5.0 (OpenStudio SDK 3.5.1, EnergyPlus 22.2) with the `openstudio-standards` gem 0.3.0, and
Python 3.13 with the packages of `three_step_workflow/requirements.txt`.

## Citation

Please cite the paper given above.

## License

MIT License (see `LICENSE`).
