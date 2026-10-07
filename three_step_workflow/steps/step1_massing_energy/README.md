# Step 1 Massing

> Note on this release: this document is kept as it was used in the experiments and describes the complete
> repository. The baseline model, the weather file of the
> measured year, and the scripts for model generation, simulation, and validation that it names are not part
> of this release (see the README at the root).

Step 1 generates building massing, validates it, renders it and screens it with
an annual EnergyPlus simulation under a fixed envelope and the baseline HVAC.
The selected massing is the eligible candidate with the lowest
`selected_total_eui_kwh_m2`.

## Design variables

- Parametric tiers: `shape` (rectangle, chamfered rectangle, 5-12 sided regular
  polygon, cross), `aspect_ratio`, `area_weight`, offsets and building rotation.
- Story ranges, zero-based and end-exclusive, for example `0..6`, `6..12`, `12..18`.

The generator scales all tiers to the project net floor-area target.

## Design requirements

Every candidate must satisfy:

- one to four tiers covering every story exactly once;
- at most 12 vertices per tier and facade segments of at least 1 m;
- every tier inside the site rectangle;
- the fixed central core (from `config/block_m.json`) fully inside every tier;
- at least `min_core_to_facade_depth_m` (4.5 m) of clear depth between the core
  and the facade, and a core share of at most `max_core_area_share` (30%) of the
  gross plate, on every tier;
- an upper tier may extend at most 1.5 m beyond the tier below, and at most one
  tier transition may expand upward;
- net floor area within the configured tolerance of the target.

## Fixed evaluation settings

- WWR 0.56 on all orientations, with inset windows generated on every exterior wall.
- No added facade shading; the urban-context shading of the baseline is retained.
- One thermal zone per floor.
- The ground-floor slab is adiabatic (`building.ground_floor_boundary` in
  `config/block_m.json`), in the baselines and in every generated model, so a
  larger ground-floor footprint earns no ground-coupling benefit.
- Envelope constructions, internal loads, schedules, infiltration basis, HVAC
  and sizing inherited from the active baseline OSM; weather from the active
  climate profile.
- FCU cooling coils are sized on the zone cooling design air flow: a
  sizing-period pass hard-sizes each autosized coil whose zone has a larger
  heating flow (`steps/shared/coil_sizing.py`). The baselines carry the
  same sizing.
- Six simulation timesteps per hour.

## Reference rows

The baseline OSM of every climate is the original building rebuilt by the
candidate generator. It keeps the original stories, story heights, floor-plate
outlines, cores, core shading and windows; its window sills are lowered by one
common distance (minimum sill 0.1 m) until the facade reaches WWR 0.56. Zoning
(one zone per story), infiltration and HVAC follow the candidate protocol. The
Hong Kong HVAC is calibrated on this model.

Each output root holds two reference rows that are never selectable:

- `baseline_osm_timestep_6`: the baseline OSM, simulated as is.
- `generated_original_control`: the generator rebuild of the original. When
  the baseline OSM is that rebuild (it records `generated_massing =
  generated_original_control`), this row reuses the baseline result. For a
  baseline with the original geometry, `original_massing_control.py` builds the
  rebuild from it and accepts it only when its provenance matches that file.

Candidate form rules are recorded for the control but not enforced, because
the existing building does not follow them. Specs submitted for candidates
cannot use its id or its reserved fields.

Each candidate row reports:

- `delta_vs_generated_original_pct`: change against the control (massing effect);
- `delta_vs_original_pct`: change against the baseline row.

## Source files

- `prompt_massing_contract.json`: output field/type contract.
- `json_geometry_contract.json`: contract for direct geometry JSON.
- `stage1_massing_interface.py`: validates a massing, checks the design
  requirements, renders it and writes the handoff geometry.
- `stage1_massing_energyplus.py`: exports the fixed-envelope OSM, runs the
  reference rows and the candidate, and records EUI.
- `original_massing_control.py`: builds the generated original control.

## Commands

```powershell
python steps\step1_massing_energy\stage1_massing_interface.py --json-spec <massing_spec.json> --candidate-id <candidate_id>
python steps\step1_massing_energy\stage1_massing_energyplus.py --candidate <candidate_id> --wwr 0.56 --overwrite
```

The energy runner runs the baseline and the control once per output root and
reuses them while their evidence hashes are current. Use
`--overwrite-baseline` or `--overwrite-original-control` to rebuild them.

## Outputs

Under `%AUTOMATED_DESIGN_OUTPUT_ROOT%`:

- `step1_massing/llm_iterations/<candidate_id>/`: `geometry.json`,
  `physical_constraints.json`, `massing_3d.png`, `feedback.json`
- `step1_massing/organized_current/best_massing.json`: selected handoff
- `step1_massing_energy/massing_energy_results.csv`: baseline row, control row,
  then candidate rows
- `step1_massing_energy/llm_iterations/<candidate_id>/`: `feedback.json`,
  `fixed_envelope_axon.png`
- `step1_massing_energy/energyplus_runs/<case>/optimization_diagnostics.json`:
  glass, roof and soffit areas, orientation window gains and load components

Lighting and equipment energy do not change with massing; judge massing
through cooling, fan and pump energy and the diagnostics above. Run Step 2's
`--verify-step1-handoff` before envelope optimization.
