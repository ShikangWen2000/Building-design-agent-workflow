# LLM Design Workflow: open design

> Note on this release: this document is kept as it was used in the experiments and describes the complete
> repository. The baseline model, the weather file of the
> measured year, and the scripts for model generation, simulation, and validation that it names are not part
> of this release (see the README at the root).

Three sequential stages generate and evaluate building massing, envelope and
HVAC designs with OpenStudio/EnergyPlus. In this version the envelope and the
HVAC system are designed with the designer's own OpenStudio scripts where the
designer chooses to, and every design is judged on the model it builds by
model-level rules (`steps/shared/model_rules.py`), not on the option it names.

| Stage | Design | Fixed input | Selected handoff |
|---|---|---|---|
| 1: Massing | Parametric tiers, proportions, offsets and rotation | Envelope, HVAC, loads and climate | `step1_massing/organized_current/best_massing.json` |
| 2: Envelope | An OpenStudio script that places windows and adds shading on the selected massing | Selected Step 1 massing and baseline HVAC | `step2_envelope_layout/organized_current/best_envelope.osm` |
| 3: HVAC | A catalog series (30 DOAS + FCU, radiant and chilled-beam series), a tool plan, or either changed or replaced by an OpenStudio script | Selected Step 2 envelope and loads | `step3_hvac/organized_current/best_hvac.osm` |

Each stage ranks eligible cases by `selected_total_eui_kwh_m2`. Eligibility
requires completed simulation, passing model-level rules, physical constraints
and comfort checks, zero Severe/Fatal errors, and current input/output
evidence. All stages use six simulation timesteps per hour.

## Setup

Install Python dependencies from `requirements.txt`. Configure OpenStudio,
EnergyPlus and the Ruby `openstudio-standards` gem for the local machine;
`config/block_m.json` names the OpenStudio CLI that runs every model change,
including the design scripts.

Supported locations: `hong_kong`, `beijing`, `shanghai`, `shenyang`, `kunming`.
Create one context and external output root for each independent run:

```powershell
python steps\shared\create_project_context.py --location hong_kong --output-root <output folder> --user-requirements "<project brief>"
```

The command appends a timestamp to the output-root base and writes the active
project context. Climate inputs are selected through `config/climate_profiles.json`.
Hong Kong uses `climate_inputs/hong_kong/baseline.osm` and
`climate_inputs/hong_kong/weather_2021_measured.epw`.

For direct stage commands, set the output root to the folder created above:

```powershell
$env:AUTOMATED_DESIGN_OUTPUT_ROOT = "E:\LLM_Output\<run_name>_<timestamp>"
```

The stages refuse to run when this variable and the context's `output_root`
differ, so a variable left over from an earlier run cannot redirect outputs.
The runtime guard requires an absolute output path outside the repository.
Its default allowed parent is `E:\LLM_Output`; another parent can be configured
with `AUTOMATED_DESIGN_ALLOWED_OUTPUT_ROOT`. Generated specs, scripts, models,
logs, rankings and simulation files are written under the active output root.

## Iteration protocol

1. Read the active run's latest feedback, rule results, metrics and previews.
2. State the design hypothesis and write one candidate (spec, and script where
   the stage uses one).
3. Execute its stage runner and inspect the resulting model and simulation evidence.
4. Use the result to choose the next candidate.

The default stopping counts are 20 valid Step 1 cases, 10 valid Step 2 cases
and 10 valid Step 3 cases. A failed case may be repaired up to three times;
failed attempts are recorded separately from valid-case counts. Each
independent run starts with an empty design history. Iteration context comes
from that run's outputs.

## Stage commands

Write each input specification and script under the active output root. Run
commands from the repository root.

### Step 1: Massing

```powershell
python steps\step1_massing_energy\stage1_massing_interface.py --json-spec <massing_spec.json> --candidate-id <candidate_id>
python steps\step1_massing_energy\stage1_massing_energyplus.py --candidate <candidate_id> --wwr 0.56 --overwrite
```

The interface takes parametric tiers, or explicit geometry through
`--geometry-json` with optional `--generator-code` provenance. Candidates must
keep the fixed core inside every tier with at least 4.5 m of clear depth to the
facade and a core share of at most 30%. Candidate models use WWR 0.56, inset
windows, per-floor zoning, no added facade shading, the urban-context shading
and every non-geometry setting of the baseline OSM. The baseline OSM is the
original building rebuilt by the same generator (`generated_original_control`),
the reference for Step 1 comparisons.

See [Step 1](steps/step1_massing_energy/README.md) for geometry constraints,
rendering and output paths.

### Step 2: Envelope

First reproduce the selected Step 1 design; this also writes the base model
every envelope script starts from:

```powershell
python steps\step2_envelope\stage2_envelope_energyplus.py --verify-step1-handoff
python steps\step2_envelope\stage2_envelope_interface.py --json-spec <envelope_spec.json> --case-id <case_id>
python steps\step2_envelope\stage2_envelope_energyplus.py --json-spec <envelope_spec.json> --case-id <case_id> --overwrite
```

A spec holds `description`, `design_intent` and `envelope_script`, an
OpenStudio Ruby script that reads `ENV['INPUT_OSM']`, may replace windows and
add shading surfaces in any form, and saves `ENV['OUTPUT_OSM']`. The rules
check what it built: only windows and added shading change; windows sit on
exterior walls with the base glazing; each orientation's WWR is 0.50-0.90;
added shading covers at most 30% of any window, boards stand out less than
1.0 m and keep 3.0 m spacing.

See [Step 2](steps/step2_envelope/README.md) for the script interface and rules.

### Step 3: HVAC

```powershell
python steps\step3_hvac\stage3_hvac_interface.py --json-spec <hvac_spec.json> --case-id <case_id>
python steps\step3_hvac\stage3_hvac_energyplus.py --json-spec <hvac_spec.json> --case-id <case_id> --overwrite
```

A spec names a catalog `series_id` (with optional `air_side_options`), an
explicit `tool_plan`, an `hvac_script` (an OpenStudio Ruby script), or a series
or plan together with a script that changes it. Systems outside the catalog
are allowed when the built model passes the model-level rules: envelope, loads,
outdoor-air requirement, thermostats and settings unchanged; only equipment
types whose performance the rules read; and equipment no better than the
catalog of the active climate. Every case ventilates every zone through a DOAS;
in the simulation each zone receives at least its minimum ventilation at the
current occupancy. Cases also pass topology, plant, simulation and
occupied-PMV checks. Each spec also predicts cooling and heating air states on
the psychrometric chart; these are compared with the simulated states of a
typical-floor zone at fixed hours.

See [Step 3](steps/step3_hvac/README.md) for the rules, controls and outputs,
and [HVAC toolkit](hvac_toolkit/README.md) for MCP access.

## Comparisons

- Step 1: each candidate against `generated_original_control`, the baseline:
  the original building rebuilt with the candidate generator, zoning, WWR and
  HVAC.
- Step 2: each case against the reproduced Step 1 selection.
- Step 3: each case against its Step 2 input.
- Original to final: the Step 3 selection against the active same-climate
  baseline.

Every comparison uses the same climate, weather file, occupancy, internal
loads, infiltration basis, six timesteps per hour and EUI definition (site
energy of all end uses and fuels over net conditioned floor area).

## Repository layout

- `config/`: project settings, climate profiles and active context.
- `climate_inputs/`: baseline OSMs and weather inputs.
- `reference/`: extracted baseline input pack; standards are obtained separately.
- `steps/`: stage interfaces, simulators, model-level rules, constraints and handoffs.
- `hvac_toolkit/`: HVAC schemas, constructors, simulation and MCP server.
- `toolkit/`: geometry, OpenStudio, model dump, rendering and diagnostic utilities.
- `agents/qwen_all_energy/`: LangGraph agent harness for controlled runs (Ollama
  or Claude Code headless backend; full_feedback, no_feedback, schema_only).

Differences from the earlier version of the workflow: Step 2 takes design scripts instead of a window
and shading option list; Step 3 drops the VAV family and accepts design
scripts; model-level rules judge Steps 2 and 3; the agent harness takes design
scripts in Step 2 and optional scripts in Step 3; the Step 2 optimizer benchmark
is in the folder `benchmark/` of this release.
