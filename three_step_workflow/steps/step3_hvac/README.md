# Step 3: HVAC

> Note on this release: this document is kept as it was used in the experiments and describes the complete
> repository. The baseline model, the weather file of the
> measured year, and the scripts for model generation, simulation, and validation that it names are not part
> of this release (see the README at the root).

Step 3 applies HVAC designs to the selected Step 2 envelope, runs EnergyPlus,
and ranks eligible cases by selected total EUI.

## Input and protocol

The input OSM is:

```text
<AUTOMATED_DESIGN_OUTPUT_ROOT>/step2_envelope_layout/organized_current/best_envelope.osm
```

A design is one of:

| Design | Spec fields |
|---|---|
| A catalog series: one of the 30 DOAS + FCU, radiant and chilled-beam series, catalog parameters fixed | `series_id` (optional `air_side_options`) |
| An explicit tool plan of the HVAC toolkit | `tool_plan` |
| A catalog series or tool plan changed by the design's own OpenStudio script | `series_id` or `tool_plan`, plus `hvac_script` |
| A system built entirely by the design's own OpenStudio script | `hvac_script` |

Systems outside the catalog are allowed as long as the built model passes the
model-level rules below; every case, catalog or not, is judged by the same
rules, simulation checks and comfort criterion.

```json
{
  "description": "<design hypothesis>",
  "case_id": "<case_id>",
  "series_id": "<supported_series_id>",
  "air_process_design": {"load_basis": "...", "cooling": {...}, "heating": {...}}
}
```

Every spec includes `air_process_design`, the predicted air states on the
psychrometric chart. See [Air-process design](psychrometric_workflow.md).

## Own HVAC scripts

`hvac_script` names an OpenStudio Ruby script, relative to the spec file or
absolute. The workflow runs it with the configured OpenStudio CLI after the
catalog series or tool plan, if any, has been built:

```
openstudio execute_ruby_script <hvac_script>
  ENV['INPUT_OSM']   the Step 2 model, or the catalog series or tool plan built on it
  ENV['OUTPUT_OSM']  where the script saves the changed model
  ENV['CASE_DIR']    the case's iteration folder
```

`hvac_script_template.rb` shows the frame. The script runs for at most 15
minutes. The toolkit's own post-processing (chiller curve normalization,
condenser reference temperatures, DOAS coil controls) runs inside the catalog
and tool-plan constructors only; a script that adds equipment sets these
itself, as the model-level rules require. Coil sizing passes and the
simulation follow the script as for any case. A case with a script skips the
tool-effect verification of its plan, since the script may replace what the
plan built; the model-level rules judge the result instead.

## Model-level rules

`steps/shared/model_rules.py` compares every built Step 3 model with the Step 2
model before simulation (`llm_iterations/<case_id>/model_rules.json`); a model
that fails a rule is not simulated:

- the envelope (windows, shading, surface areas by kind and orientation, zoning),
  opaque constructions (a radiant surface may use an internal-source
  construction that keeps every baseline layer in order), internal loads,
  infiltration, the outdoor-air requirement with its fraction schedule, space
  assignments and simulation settings (no special days or daylight-saving
  time added) are unchanged;
- schedules stay those of the Step 2 model, with two exceptions on the
  thermostat schedules, each declared in the spec with its reason
  (`"schedule_changes"`); an undeclared change fails:
  - A: the summer and winter design-day profiles of the heating and cooling
    setpoint schedules, for sizing (the toolkit's design-day alignment is
    declared by the runner for cases built with a series or tool plan);
  - B: every cooling setpoint of the simulated days raised by one offset
    0 < delta <= 1.0 K, only in zones with radiant cooling or chilled beams
    (they lower the mean radiant temperature); times, rules and dates of the
    schedule stay unchanged;

  ```json
  "schedule_changes": {"setpoint_design_day_profiles": {"reason": "..."},
                       "cooling_setpoint_offset": {"delta_k": 0.5, "reason": "..."}}
  ```

  a new schedule is used only as a setpoint or an availability and holds one
  value all year; in a climate with a heating period it may also change with
  that period only (one value per day, rules for all days of the week, dated at
  the heating period). Other control that varies in time uses only a schedule
  of the Step 2 model, unchanged;
- outdoor air comes from 100% outdoor-air loops only. Its flow is set only by
  the `Demand Controlled Ventilation` switch of the loop's
  `Controller:MechanicalVentilation` (method `ZoneSum`, baseline availability);
  the fields of `Controller:OutdoorAir` that set the outdoor-air flow keep the
  values of the Step 2 model (minimum and maximum flow, minimum limit type,
  minimum outdoor-air schedule, outdoor-air fraction schedules, time-of-day
  economizer schedule, high-humidity control); air terminals do not schedule
  their minimum flow; zone equipment brings in no outdoor air;
- every 100% outdoor-air loop has an exhaust (relief) fan on its outdoor-air
  system, with autosized flow and the availability of the supply fan; with
  heat recovery the exhaust air passes the heat-recovery exchanger;
- no added loads, ideal loads, extra ventilation or mixing, energy management,
  on-site generation, district cooling, absorption chillers, evaporative
  coolers or thermal storage;
- HVAC components are of the types whose performance the rules verify (water,
  electric, DX and radiant coils; constant-volume, variable-volume and on/off
  fans; constant- and variable-speed pumps; electric EIR chillers; plant-loop
  EIR and water-to-water heat pumps; hot-water boilers; single- and
  variable-speed cooling towers; air-to-air and fluid-to-fluid heat exchangers;
  fan coils, radiant systems, cooling panels, baseboards, beams, single-duct
  terminals);
- chillers, heat pumps and boilers are autosized (capacity, flow rates and
  reference power) with a component sizing factor of at least 1.0;
- the chilled-water loop of every DOAS cooling coil supplies at most 7 C
  (design exit temperature, scheduled supply setpoint and chiller reference
  leaving temperature);
- equipment performs as the catalog of the active climate: FCU (zone
  equipment) fans at most the efficiency of the baseline FCU fans, other fans
  total efficiency at most 0.55 and motor efficiency at most 0.92, pressure
  rise at least the baseline pressure of the fan's role (zone equipment, air
  loop, relief); pumps with at least the baseline head, motor efficiency at most
  0.90, autosized power at a shaft power of 1.282 per flow and head and linear
  part-load coefficients; chiller and heat-pump COP between the catalog value
  and that value credited for the leaving water temperature (+3.5% per K above
  7 C up to 16 C; for heating, per K below the standard leaving temperature
  down to 35 C); chillers with the ASHRAE 90.1 Path A curves normalized at their
  reference conditions and the climate's reference condenser temperature;
  plant-loop heat pumps, on/off fans, DX coils and variable-speed towers with
  the OpenStudio default curves the toolkit keeps; water-to-water heat pumps
  with the slopes of the example curves and power not below 1.0 at their
  design point; boilers at the catalog efficiency without an efficiency curve;
  heat recovery effectiveness at most 0.80; autosized tower fan power; radiant
  and cooling-panel condensation control `SimpleOff` or `VariableOff` with a
  dewpoint offset of at least 1 K.

The inspection, plant, DOAS, ventilation, comfort and Severe/Fatal checks below
apply to every case as well, and the simulation may use no district energy.
Plan-declaration checks (the Hong Kong BEC and mainland checks that read tool
arguments) apply to cases with a catalog series or tool plan; a case built only
by a script is judged on the same quantities through the model-level rules.

Use `hvac_series_catalog.json` for series IDs and
`../../hvac_toolkit/registry.py` for tool names and argument definitions.
`json_hvac_spec_contract.json` describes the general tool-plan wrapper.

## Execution

Write the design spec under the active output root, then run:

```powershell
python steps\step3_hvac\stage3_hvac_interface.py --json-spec <hvac_spec.json> --case-id <case_id>
python steps\step3_hvac\stage3_hvac_energyplus.py --json-spec <hvac_spec.json> --case-id <case_id> --overwrite
```

The runner validates the spec, applies HVAC changes, inspects the generated
OSM, simulates at six timesteps per hour, verifies requested IDF effects,
extracts metrics, evaluates constraints and updates the eligible-case ranking.
A failed pre-simulation topology inspection stops that case before EnergyPlus.
The default formal run stops after ten valid completed HVAC cases.

## Controls and sizing

- Each eligible case ventilates every zone through a DOAS: a 100% outdoor-air
  loop serving every zone. Pre-simulation checks inspect served zones, air
  loops, plant loops, equipment and coil controls; every DOAS has a cooling
  coil and a heating coil (disabled in Hong Kong).
- Minimum ventilation is checked in the simulation for every family. Each
  occupied hour, a zone's mechanical outdoor air is compared with the
  requirement of that hour: the occupant count times the outdoor air per
  person of the outdoor-air specification (8 L/s in Hong Kong) times its
  fraction schedule. A zone may be below 99% of the
  requirement for at most 1% of its occupied hours, and its occupied total
  must reach at least 0.99 of the requirement. The EnergyPlus
  `OutdoorAirDetails` time-below statistic is not used for this check.
- Humidity: in every zone the relative humidity is at most 70% in at least 95%
  of the ventilation hours (occupied hours with that schedule above zero),
  occupant-weighted.
- Chilled beams: in the hours a beam cools, its chilled-water inlet
  temperature is at least the zone dew point + 1 C in at least 99% of them,
  for every beam.
- Reported without a limit (`unmet_hours_json`, with `baseline_unmet_hours_json`
  for reference): setpoint-not-met hours and ASHRAE 55 (simple) discomfort
  hours of the building and of each zone.
- DOAS uses the ventilation schedule of the input model, then its occupancy schedule, with an
  always-on fallback. FCU/radiant terminals use an explicit HVAC availability
  schedule when present; otherwise thermostat schedules control operation
  while terminals remain available.
- `availability_prestart_hours` shifts DOAS off-to-on transitions while
  preserving the calendar's day types. Formal catalog prestart is zero hours.
- DOAS sizing uses 100% outdoor air, a central cooling design supply
  temperature equal to the coil leaving-air setpoint, and a central heating
  air flow ratio of 1.0, so the heating coil is sized for the full
  constant-volume flow.
- DOAS cooling-coil leaving-air control defaults to 12 C. Actual final supply
  temperature depends on downstream coils, fans and operating conditions.
- Hong Kong heating sources and heating coils are disabled. Its placeholder
  heating-coil controller uses the cooling outlet target (default 12 C).
  Mainland DOAS heating uses 20 C outlet control during the configured heating
  season. Heating sources run only in the heating season of the active
  climate profile. As in the baselines, cooling sources stay available all
  year and the DOAS cooling coil is off during the heating season;
  `cooling_available_all_year` keeps the DOAS coil on as well.
- FCU and chilled-beam thermostats follow the baseline cooling setpoint
  schedule (Hong Kong 23.5 C air in control hours, the baseline setback value outside them).
  Sizing uses the same occupied setpoint: where the thermostat's summer
  design-day value is warmer than the lowest cooling setpoint of a summer
  weekday, Step 3 lowers it to that value (the calibrated Hong Kong baseline
  sizes at 26 C; its Step 3 designs size at 23.5 C). Unoccupied design-day
  values are unchanged, and the mainland design days already use the
  operating 26 C.
- Radiant cooling uses `OperativeTemperature` control at 26 C, and 24 C in
  the heating period of climates that have one (where PMV uses 1.0 clo),
  because a cooled ceiling lowers the mean radiant temperature and an air
  setpoint would not describe the occupants' thermal condition. Systems are
  compared on occupied PMV, not on air temperature. Radiant series use
  `SimpleOff` condensation control and a 2 K dewpoint safety offset.
- A catalog spec may add `"air_side_options": {...}` next to `series_id`
  with `add_heat_recovery`, `hrv_sensible_effectiveness`,
  `hrv_latent_effectiveness`, `economizer_type` and `enable_dcv`; every
  other catalog value stays fixed.
- Air-side options on every constructor: `add_heat_recovery` (all families),
  `economizer_type` (on a DOAS only a heat recovery bypass, requiring heat
  recovery) and `enable_dcv` (DOAS+FCU and radiant; not chilled beams, whose
  four-pipe beam terminal is constant volume and needs its primary air for
  induction). With DCV the DOAS fan is variable volume, each
  zone terminal supplies the outdoor air of its current occupancy, and the FCU
  or radiant equipment comes first in the zone sequence. The outdoor-air
  specification keeps its fraction schedule, so the outdoor
  air follows occupants x rate x schedule, the requirement the ventilation
  check applies. Every DOAS controller keeps the minimum outdoor-air schedule
  of the project model.
- Plant checks on the built model, before simulation: each plant loop has at
  most one chiller or cooling heat pump on its supply side; the condenser loop
  of water-cooled chillers holds the location's reference entering-condenser
  temperature all year (scheduled setpoint and design exit temperature); design
  temperature differences are 5 K for chilled water below 10 C and for the
  condenser water of chillers, and at most 5 K for high-temperature radiant and
  beam loops.
- New FCU, radiant and chilled-beam constructors default to global cooling
  sizing factor 1.05. Component sizing factors remain 1.0.
- Before each simulation, sizing-period passes (`steps/shared/coil_sizing.py`):
  - hard-size every autosized FCU cooling coil whose zone has a larger heating
    than cooling design air flow to the zone cooling design flow (sized at the
    heating flow, the coil's design UA calculation fails);
  - size each DOAS cooling coil on the 0.4% Enth=>MDB design day when that
    gives a larger coil load than the full design-day set, because the
    air-loop sizing otherwise takes the outdoor state of the dry-bulb peak.
    The chilled-water plant then autosizes from the coils. The original
    baseline reference keeps its calibrated sizing;
  - size each autosized four-pipe beam length to the zone design cooling load
    divided by the rated capacity per length, without credit for the primary
    air.
- Step 3 models use a loads convergence tolerance of 0.5 W (EnergyPlus
  default 0.04 W, an absolute value). With zone loads of tens of kW,
  day-to-day noise of about 0.1 W otherwise fails warmup on a design day and
  reports a Severe error.
- Radiant `cooling_capacity_method="autosize"` uses design-day sizing.
  `capacity_per_floor_area` requires an explicit panel capacity in W/m2.
  Terminal capacity, source capacity and water flow are reported separately.
- `autosized_fields` and `hard_sized_fields` describe the plan's sizing choices.
  Their accepted contents are checked by `hvac_toolkit/schema.py`; the selected
  Ruby constructor performs the actual sizing operations.

Formal series parameters are generated by `hvac_toolkit/series.py`. For manual
plans, consult `hvac_toolkit.registry.public_tool_descriptions()` and
`hvac_toolkit/schema.py` for supported arguments, bounds and required engineering
justifications. Baseline pressure references are read from the active location.

## Performance assumptions

Climate reference performance is configured in `config/climate_profiles.json`.
The COP of the existing chiller is read from the baseline model; equipment-reference minima
and replacement-system assumptions are separate values.

Every chiller the toolkit builds uses ASHRAE 90.1-2010 Path A performance
curves (openstudio-standards 0.3.0), the family of the baseline chillers, so a
replacement is credited only with its declared COP and not with a flatter
part-load curve. Air-cooled: `AirCooled_Chiller_2010_PathA` CAPFT/EIRFT with
`AirCooled_Chiller_AllCapacities_2004_2010_EIRFPLR`; water-cooled: the curves
the standard assigns to centrifugal chillers of 300 tons and more
(`WaterCooled_PositiveDisplacement_Chiller_GT150_2010_PathA` CAPFT/EIRFT with
`ChlrWtrCentPathAAllEIRRatio_fQRatio`). CAPFT and EIRFT are scaled to 1.0 at
each chiller's reference leaving-water and condenser temperatures, where the
catalog COP applies, and their leaving-water range is extended to cover the
15-18 C radiant and beam terminal chillers. Heat pumps keep their own curves.

In mainland climates the catalog's high-lift water-cooled chillers use the
GB 55015 Table 3.2.9-1 centrifugal minimum for plants above 2110 kW (Beijing
6.2, Shanghai 6.3, Shenyang and Kunming 6.1), gas boilers use the GB 55015
Table 3.2.5-1 minimum efficiency of 0.92, and each plan declares fans at
GB 19761 grade 2 and pumps at the GB 19762 energy-saving value.

The catalog applies the declared 3.5% COP adjustment per degree of improved
leaving-water temperature. Active chilled-beam defaults include four-pipe beam
terminals, 15/18 C terminal water, 7/12 C DOAS water, primary airflow multiplier
1.0 and cooling capacity 960 W/m, an upper-bound equipment selection
assumption. Before simulation the zone beam length is sized to the zone design
cooling load divided by 960 W/m, and the rated chilled-water flow per length
carries 960 W/m at the 3 K beam-loop temperature difference, so the terminal
chiller matches the beams. Record these assumptions with the selected series.

## Energy and comfort

EUI includes heating, cooling, fans, pumps, heat rejection, lighting and equipment
across reported fuels, divided by net conditioned floor area.

Comfort eligibility uses occupied PMV in the band -0.5 to +0.5: the occupied
comfort share may be at most one percentage point below that of the active
same-location original baseline. Missing PMV evidence fails
eligibility. PMV uses 1.1 met, 0.1 m/s and clothing of 1.00 clo in the heating
season and 0.50 clo otherwise. The output `pmv_assumptions_json` records the
applied assumptions and sampling method.

Temperature, thermostat tracking and RH are diagnostic outputs. Use supply-air,
coil, plant and zone results to assess sensible/latent capacity and control
operation. Occupied comfort percentages are occupant weighted; facility unmet
hours count each hour once across occupied zones.

## Outputs

Paths below are relative to `AUTOMATED_DESIGN_OUTPUT_ROOT`:

| Path | Contents |
|---|---|
| `step3_hvac/llm_iterations/<case_id>/design_spec.json` | Submitted design |
| `step3_hvac/llm_iterations/<case_id>/tool_plan.json` | Expanded tool plan |
| `step3_hvac/llm_iterations/<case_id>/model_rules.json` | Model-level rule results |
| `step3_hvac/llm_iterations/<case_id>/hvac_script/` | Copy of the design's own script |
| `step3_hvac/llm_iterations/<case_id>/pre_simulation_osm_inspection.json` | Generated topology and controls |
| `step3_hvac/llm_iterations/<case_id>/feedback.json` | Metrics and validation feedback, including `air_process_comparison` |
| `step3_hvac/llm_iterations/<case_id>/psychrometric/` | Design charts, simulated-versus-design charts and `psychrometric_evidence.json` |
| `step3_hvac/physical_constraints/<case_id>.json` | Eligibility checks |
| `step3_hvac/hvac_toolkit_eui_results.csv` | Case comparison table |
| `step3_hvac/organized_current/best_hvac.json` | Selected eligible case |
| `step3_hvac/organized_current/best_hvac.osm` | Selected HVAC model |
| `step3_hvac_toolkit/` | Applied OSMs, simulation runs and tool-effect evidence |

Inspect each completed case's feedback and constraints before generating the
next design. Failed cases may be repaired up to three times.

## Diagnostics

```powershell
python steps\step3_hvac\analyze_overheat_periods.py --case-id <case_id>
python steps\step3_hvac\cooling_diagnostic.py --case-id <case_id>
```

The overheat tool writes daily/period summaries under
`step3_hvac/diagnostics/overheat/`. The cooling tool writes plant and terminal
capacity, unmet-demand periods and occupied-overheat overlap under
`step3_hvac/diagnostics/cooling/`. Both accept `--run-dir` for an explicit run.
Reports identify missing SQL variables.

The baseline roof and ceilings are ordinary constructions without radiant panels. Radiant constructors, and the `add_radiant_ceiling_metal_panels` tool (`panel_scope`: `all_ceilings` or `roof_only`), keep every existing layer of each roof and ceiling and add a room-side ceiling cavity (when absent) and two 3.175 mm metal panel layers, with the chilled-water internal source between the metal layers. The slab above each converted ceiling receives the reversed construction.

For radiant systems, inspect construction source position, thermal resistance,
adjacent-surface layer reversal, terminal flow and zone conditions together.
Radiant source heat extraction and instantaneous room sensible cooling are
separate quantities in the heat balance.

See [HVAC toolkit](../../hvac_toolkit/README.md) for MCP endpoints.
