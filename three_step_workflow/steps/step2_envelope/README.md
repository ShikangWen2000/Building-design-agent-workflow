# Step 2 Envelope

> Note on this release: this document is kept as it was used in the experiments and describes the complete
> repository. The baseline model, the weather file of the
> measured year, and the scripts for model generation, simulation, and validation that it names are not part
> of this release (see the README at the root).

Step 2 keeps the selected Step 1 massing fixed and designs the facade: windows
and external shading. There is no menu of window or shading types. Each case
is an OpenStudio Ruby script, written by the designer, that changes a copy of
the base model; the model-level rules (`steps/shared/model_rules.py`) then
judge what the script built. The selected envelope is the eligible case with
the lowest `selected_total_eui_kwh_m2`; its OSM is the Step 3 input.

## Fixed settings

- Massing, zoning and core (with its shading box) from the selected Step 1
  handoff.
- Opaque constructions, glazing construction, internal loads, schedules,
  infiltration basis, HVAC, controls and sizing inherited from the active
  baseline OSM, exactly as in Step 1; weather from the active climate profile.
  FCU cooling coils are sized on the zone cooling design air flow after the
  script runs, as in Step 1.
- Six simulation timesteps per hour.
- Lighting energy is not daylight-controlled, so results describe
  energy-oriented facade design only.

## Handoff reproduction and base model

Before any envelope case, reproduce the selected Step 1 design:

```powershell
python steps\step2_envelope\stage2_envelope_energyplus.py --verify-step1-handoff
```

This rebuilds the Step 1 massing with its fixed WWR 0.56 inset windows and no
shading, and compares area, total EUI and every end use with the hash-verified
Step 1 result (0.1% per metric, 0.001 absolute floor). A failed comparison stops
the search. The report is `step2_envelope_layout/handoff_verification.json`;
the reproduction is not ranked and does not count as a case.

The reproduced model before coil sizing is saved as
`step2_envelope_layout/envelope_base/envelope_base.osm`, with its hash and the
handoff verification in `envelope_base.json`. Every envelope script starts
from a copy of it. A script that changes nothing reproduces the Step 1 result.

## Design specification

One JSON spec per case:

```json
{
  "description": "What the design changes.",
  "design_intent": "The hypothesis it tests.",
  "envelope_script": "envelope_001.rb"
}
```

`envelope_script` is relative to the spec file or absolute. The workflow runs
it with the configured OpenStudio CLI:

```
openstudio execute_ruby_script <envelope_script>
  ENV['INPUT_OSM']   copy of the base model (read it)
  ENV['OUTPUT_OSM']  where the script saves the changed model
  ENV['CASE_DIR']    the case's iteration folder (for the script's own notes)
```

`envelope_script_template.rb` shows the frame of a script and
`envelope_script_examples.rb` runnable OpenStudio API examples (wall frame,
windows, horizontal boards, fins; its closing example only demonstrates the
calls and is not a recommended design). The script may
remove, replace and add windows and add shading surfaces in any form; it may
read its own parameter files. It runs for at most 15 minutes.

## Design requirements (model-level rules)

The built model is compared with the base model. A case is eligible only if:

- only windows and added shading surfaces (with their shading groups) differ;
  constructions, loads, schedules, HVAC, settings, the massing and the thermal
  zoning are unchanged;
- the surrounding buildings and the core shading box stay in place, and added
  shading surfaces have no transmittance schedule;
- every window is a fixed window on an exterior wall, uses the base glazing
  construction, has multiplier 1 and no shading control, lies in the plane and
  inside the outline of its wall (tolerance 0.01 m), and windows of one wall
  do not overlap;
- the window-to-wall ratio of each orientation (quadrants split at 45, 135,
  225 and 315 degrees from true north) is between 0.50 and 0.90 (deviation of
  0.02 tolerated);
- seen along the facade normal, added shading covers at most 30% of any window
  and 30% of the window area of any orientation;
- every protruding shading board stands out less than 1.0 m from the facade;
  in front of one window, horizontal boards are at least 3.0 m apart
  vertically and vertical boards at least 3.0 m apart horizontally; every
  added shading surface belongs to a window (within 1.5 m in front of its
  wall, on the window's story);
- the added shading surfaces (one side) total at most 30% of the building's
  exterior surface area (gross area of the walls and roofs with an outdoor
  boundary, windows included);
- the Step 1 area, height and footprint are unchanged;
- the simulation completes with zero Severe/Fatal errors.

A script that fails, or a model that fails a rule, is recorded
(`failed_envelope_script`, `failed_model_rules`) and is not simulated.

## Iteration

1. Read the previous case's `physical_constraints/<case_id>.json`,
   `llm_iterations/<case_id>/model_rules.json` (rule results, realized WWR and
   shading audit), its row in `envelope_eui_results.csv`, its `feedback.json`
   and its renders.
2. Fix failed rules first; otherwise state the next hypothesis.
3. Write one script and spec and run them.

A failed case may be repaired up to three times. Failed attempts are kept and
do not count toward the 10 valid cases.

## Commands

```powershell
python steps\step2_envelope\stage2_envelope_interface.py --json-spec <envelope_spec.json> --case-id <case_id>
python steps\step2_envelope\stage2_envelope_energyplus.py --json-spec <envelope_spec.json> --case-id <case_id> --overwrite
```

## Outputs

Under `%AUTOMATED_DESIGN_OUTPUT_ROOT%/step2_envelope_layout`:

- `envelope_base/`: base model and its record
- `llm_iterations/<case_id>/`: `design_spec.json`, the script copy,
  `model_rules.json`, `feedback.json`, `massing_preview.png`,
  `envelope_axon.png`
- `osm_cases/<case_id>.osm` and `<case_id>_script.log`
- `physical_constraints/<case_id>.json`
- `envelope_eui_results.csv`: all cases with realized WWR and audit results
- `energyplus_runs/<case_id>/optimization_diagnostics.json`
- `organized_current/best_envelope.osm` and `best_envelope.json`: Step 3 input

The window and shading generator of `toolkit/ruby/replace_original_geometry.rb`
remains in the repository because Step 1 and the handoff reproduction use it;
it is not a Step 2 input.
