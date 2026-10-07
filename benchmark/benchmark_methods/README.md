# Hong Kong Step 2 Mixed-Variable Benchmark

> Note on this release: this document is kept as it was used in the experiments and describes the complete
> repository. The baseline model, the weather file of the
> measured year, and the scripts for model generation, simulation, and validation that it names are not part
> of this release (see the README at the root).

This benchmark compares genetic algorithm, random search, space-filling
search, and Bayesian optimization over one shared hierarchical mixed
discrete-continuous envelope design space.

All methods use `envelope_search_space.py` as the only candidate decoder.
Bayesian optimization (`bayesian_optimization.py`) fits a Gaussian-process
surrogate to the valid evaluations and proposes each next candidate by
maximizing Expected Improvement over the same normalized genome vector; it is
warm-started with the first eight valid space-filling samples and requires
NumPy and SciPy.
The shared representation includes:

- orientation-specific window type, WWR, window height and sill height;
- orientation-specific shading type, including a continuous floor-edge ledge;
- conditional depth, angle, spacing/open-area, overhang side-extension and
  head-gap, and story-range parameters;
- three shared window-parameter genes for type-specific margins.

Two decoders share the same 51 normalized genes; `STEP2_BENCHMARK_SPACE`
(or `--search-space` of the matrix runner) selects one, and every method of a
run uses the same one:

| Space | Content |
|---|---|
| `valid_range_v3` (default) | Combinations the Step 2 stage can realize. Three window types (`offset_window`, `floor_to_ceiling`, `continuous_window`); the WWR range of each type stops at what its parameters reach on a 4 m story; small margins; shading `none`, `horizontal`, `louver`, `perforated_panel`, `ledge`, with no perforated screen on floor-to-ceiling glazing; board tilt and screen openness inside the projection limit. |
| `hierarchical_mixed_v2` | Every option the stage accepts: four window types, seven shading types, WWR 0.50-0.90 for every type and wide margins. WWR and window parameters are sampled independently, so most combinations are rejected by the stage's constraints. |

Options left out of `valid_range_v3` are those the constraints reject on a
faceted plan with short wall segments: vertical and combined fins (3 m board
spacing), punched modules (wall outline), and a perforated screen on
floor-to-ceiling glazing (frame outside the audited window area). The ranges
are data in `envelope_search_space.py` (`SPACES`); the model prompt and the
engineer rule are written from the active space.

`constraint_pass_rate.py` measures how many random designs of the active
space pass the stage's constraint pre-check, without simulation, and lists the
failed checks by window and shading type:

```powershell
python benchmark_methods\constraint_pass_rate.py `
  --probe-root E:\LLM_Output\<probe> `
  --step1-root E:\LLM_Output\<handoff>\step1_massing `
  --project-context <context folder>\project_context_hong_kong.json `
  --count 96 --workers 8
```

A v1 genome with one building-level window type is still decoded by applying
that type to every facade. Parameters that do not apply to the selected type
remain inactive and do not affect the generated model.

Example:

```powershell
python benchmark_methods\benchmark_step2_mixed_methods.py `
  --run-root E:\LLM_Output\benchmark_step2_mixed_hk_repeat01 `
  --step1-root E:\LLM_Output\<hong_kong_run>\step1_massing `
  --project-context <context folder>\project_context_hong_kong.json `
  --methods all `
  --proposal-budget 50 `
  --repeat-index 1
```

`--project-context` must declare `"location": "hong_kong"`. Each optimization
method receives a separate output root but the same climate, Step 1 handoff,
decoder, constraints, and proposal budget. Every proposal consumes one budget
unit, including interface rejection, EnergyPlus failure, and timeout. Reports
report valid fraction and elapsed time for each method.

## LLM added-value ablation

`local_agent_ablation.py` runs four controlled modes over the same decoder and
proposal budget. The prompt is assembled from three files:

| Mode | Prompt parts | History |
|---|---|---|
| `full_feedback` | contract, design guidance, feedback use | last 30 proposals with simulation results, and the best valid one |
| `no_simulation_feedback` | contract, design guidance | last 30 proposals, results withheld |
| `schema_only` | contract | none |
| `engineer_rule` | no model; pre-registered deterministic rule | none |

Each model mode runs in two representations (`--representation`):

| Representation | What the model writes | What it sees of earlier proposals |
|---|---|---|
| `genome` | the 51 normalized genes | the genes |
| `physical` | window and shading types by name, WWR, heights, depths and angles in physical units | the same physical values |

A physical proposal is clipped to the active ranges, rounded to the decoder's
step and encoded into the shared genome (`physical_to_genome`), then decoded
like every other method's genome. Both representations therefore reach exactly
the same set of designs; only the language of the proposal differs.

The contract (`LLM_STEP2_MIXED_SPACE_PROMPT.md`) holds the task, the fixed
massing read from the Step 1 handoff, the design space, hard limits and the
response format; the design-space and response-format text is written from the
active space and representation. Design guidance is `LLM_STEP2_STRATEGY_PROMPT.md`; feedback
use is `LLM_STEP2_FEEDBACK_PROMPT.md`. Local model serving is supplied as an
explicit command or Ollama model tag, and the manifest records the backend,
seed, proposal budget, prompt-file hashes, timings, raw responses, designs,
failures, and evaluations.

Claude Code and local models use the same provider-neutral loop: both receive
the same text/history and return one normalized genome; neither receives file,
shell, MCP, image, or simulation tools. The harness alone runs the shared
decoder and simulation. Example Claude backend:

```powershell
python benchmark_methods\local_agent_ablation.py `
  --mode full_feedback --backend claude_code --model claude-opus-5-5 `
  --agent-root <new-run-root> --step1-root <fixed-step1-root> `
  --project-context <context folder>\project_context_hong_kong.json `
  --proposal-budget 10 --max-budget-usd-per-proposal 0.50
```

Use `--backend ollama --model <exact-local-model-tag>` for the local model.
Record the model digest and serving configuration for reproducibility.

## Full matrix and summary

`run_step2_benchmark_matrix.py` runs every optimizer repeat and every
local-agent ablation mode against one fixed Step 1 handoff, each job in its
own output root, with bounded concurrency (`--max-parallel`, default 6).
Finished jobs are skipped on restart.

```powershell
python benchmark_methods\run_step2_benchmark_matrix.py `
  --run-root E:\LLM_Output\<matrix_run> `
  --step1-root E:\LLM_Output\<fixed_run>\step1_massing `
  --project-context <context folder>\project_context_hong_kong.json `
  --model <exact-local-model-tag> --repeats 5 --budget 50
python benchmark_methods\summarize_step2_benchmark_matrix.py --run-root E:\LLM_Output\<matrix_run>
```

Layout under the matrix root: `optimizers/<method>_r<repeat>/` for the four
optimizers, `llm_ablation/seed<seed>/<mode>/` for the model modes writing
genes, `llm_physical/seed<seed>/<mode>/` for the same modes writing physical
values (reported as `<mode>_physical`) and
`llm_ablation/deterministic/engineer_rule/`. Jobs run repeat by repeat, the
engineer rule first, so the finished repeats always form complete sets. The summary writes `runs.csv`,
`methods.csv`, `comparisons.csv`, `best_so_far.csv` and `summary.json` under `matrix_summary/`.

Every method copies the fixed Step 1 handoff into its own output root and
binds the copied geometry file in the handoff evidence. The evidence also
binds the model, the inputs and the code files of the run that produced the
handoff, so that run and its repository copy must stay in place and unchanged.

`prepare_fixed_step1_handoff.py` extracts the selected Step 1 design of a
finished run into a compact handoff folder (geometry, result table, selection
records) and verifies its evidence:

```powershell
python benchmark_methods\prepare_fixed_step1_handoff.py `
  --source-root E:\LLM_Output\<finished_run_output_root> `
  --handoff-root E:\LLM_Output\<handoff>
```

Use `<handoff>\step1_massing` as `--step1-root` here and `<handoff>` as
`--fixed-step1-handoff` of the three-step local agent.

After each proposal the runners delete files above 10 MB that are not models,
tables, records or images; the extracted results stay in the CSV and JSON files.

`generate_mixed_benchmark_comparison.py` and
`export_portable_benchmark_bundles.py` in the repository root read the
single-run layout (`benchmark_methods/<method>/` plus `llm_agent/` in one run
root with 50 valid cases per series), not the matrix layout.
