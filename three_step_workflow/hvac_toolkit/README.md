# HVAC Toolkit

> Note on this release: this document is kept as it was used in the experiments and describes the complete
> repository. The baseline model, the weather file of the
> measured year, and the scripts for model generation, simulation, and validation that it names are not part
> of this release (see the README at the root).

HVAC model construction, validation, simulation and MCP interfaces for Step 3.

Execution sequence:

```text
LLM proposes tool calls -> Python validates engineering bounds ->
Ruby applies OpenStudio changes -> EnergyPlus simulates ->
Python verifies the requested IDF effects -> eligible cases are ranked
```

Ranking requires completed simulation, zero Severe/Fatal errors, passing
physical and PMV checks, and verified tool effects.

## Step 3 Use

The main project entry point is:

```powershell
python steps\step3_hvac\stage3_hvac_energyplus.py --json-spec <hvac_spec.json> --case-id <case_id> --overwrite
```

Stage 3 passes the selected Step 2 envelope OSM to this toolkit. The Block M
workflow is self-contained around the active project model and weather file.

## Core Tool Surface

| Function | Purpose |
|---|---|
| `inspect_osm_hvac_topology(osm_path)` | Inspect zones, loops, equipment, and supported targets. |
| `validate_hvac_tool_plan(plan)` | Check schema and engineering bounds before apply. |
| `apply_hvac_tool_plan(plan, input_osm, output_root)` | Apply a validated plan to an OSM. |
| `simulate_hvac_case(case_id, osm_path, output_root, plan)` | Run OpenStudio/EnergyPlus; the matching plan is mandatory for effect verification. |
| `run_hvac_tool_plan(plan, input_osm, output_root)` | Apply and simulate a validated plan. |
| `build_hvac_evidence_card(plan, result, topology, diagnostic)` | Summarize case evidence; topology and diagnostic are optional. |
| `rank_hvac_candidates(results)` | Rank only main-pipeline rows with current hash evidence, physical approval, zero severe/fatal errors, explicit tool verification, valid occupied PMV evidence and a passing PMV-versus-original check; air-thermostat unmet hours are diagnostic only. |

## Concrete HVAC Series Catalog

`series.py` expands the formal Step 3 tool surface into 30 named HVAC series:

- 10 DOAS + FCU series
- 10 DOAS + low-temperature radiant prototype series
- 10 DOAS + active chilled-beam series

A Step 3 design may also be built or changed by its own OpenStudio script; see
`steps/step3_hvac/README.md` and the model-level rules in
`steps/shared/model_rules.py`.

FCU fans keep the efficiency of the baseline FCU fans in every series and
tool; `fan_efficiency` and `fan_motor_efficiency` apply to the DOAS supply and
relief fans.

Air-side options (arguments of every system constructor, off in the catalog):

- `add_heat_recovery`: rotary sensible and latent wheel on the outdoor air
  (`hrv_sensible_effectiveness` 0.65, `hrv_latent_effectiveness` 0.55 by
  default, at most 0.80). Available for DOAS+FCU, radiant and chilled beam.
- `economizer_type`: a DOAS supplies 100% outdoor air and has no return air to
  mix; the economizer only bypasses the heat recovery wheel when outdoor air
  is favourable, and it requires `add_heat_recovery=true`.
- `enable_dcv`: outdoor air follows current occupancy (8 L/s per person).
  DOAS+FCU and radiant: the DOAS fan becomes variable volume and each zone
  gets a variable-volume outdoor-air terminal sequenced after the FCU or
  radiant equipment; a fraction schedule on the zones' outdoor-air
  specification is removed, so occupancy alone sets the outdoor air. Not
  available for chilled beams (active beams need constant primary air for
  induction).

Water-to-water heat pumps (WSHP series) use the EnergyPlus EquationFit model.
Each heat pump's curves are shifted so capacity and power equal their
reference values at its design point (load-loop return, source-loop supply,
reference flows); the heating heat pump has its own power curve. The source
loop is a water-loop heat pump loop: a cooling tower rejects heat above the
upper setpoint (29.4 C) and a gas boiler keeps the loop at or above
`wshp_source_minimum_c` (15.6 C) when heat pumps extract heat; the loop stays
available all year. The sizing pass hard-sizes a WSHP source loop, its pump
and cooling tower to the heat pumps' source-side design flow where
EnergyPlus sizes them lower.

Chilled-beam catalog defaults: four-pipe beam terminals (`beam_type=four_pipe`;
cooling-only series put the heating coil on a zero-capacity loop with its
availability off), 15/18C terminal chilled water, 7/12C DOAS chilled water for
latent control, `primary_airflow_multiplier=1.0`, active-baseline-derived fan
pressure and `beam_cooling_capacity_per_length_w_m=960.0`. The zone beam length
is sized before simulation to the zone design cooling load (sizing factor
included) divided by the rated capacity per length, without credit for the
primary air. The rated chilled-water flow per length carries the rated
capacity at the 3 K beam-loop temperature difference, so the terminal chiller,
sized from the beam flows, matches the beams. The capacity basis is ASHRAE
Houston chilled-beam application guidance listing active chilled beam cooling up
to 1000 Btu/h/ft; that converts to 961 W/m, so 960 W/m is recorded as the
upper-bound product selection used in the Step 3 tool defaults.

List or write the catalog:

```powershell
python hvac_toolkit\series.py --list
python hvac_toolkit\series.py --write-specs E:\LLM_Output\<run_name>\series_specs
```

Each entry maps directly to a toolkit construction plan and records the mapping
in `implementation_note`.

DOAS availability uses the input ventilation or occupancy calendar. FCU and
radiant terminals use an explicit HVAC availability schedule when present,
otherwise they remain available under thermostat control. Requested DOAS prestart
shifts off-to-on transitions while preserving the calendar day types.

## Multi-Climate Seasonal Availability

When the OSM weather file identifies one of the project climates, the Ruby
adapter automatically assigns complementary annual availability schedules to
heating and cooling source equipment and their associated coils:

| Climate/city | Heating source available |
|---|---|
| Severe cold / Shenyang (Harbin design basis) | Oct 09-Apr 14 |
| Cold / Beijing | Oct 27-Apr 02 |
| Hot summer, cold winter / Shanghai | Dec 03-Mar 20 |
| Mild / Kunming | Nov 13-Feb 28 |

Cooling sources stay available all year; the DOAS cooling coil is off during
the heating period. The dates come from the project's multi-climate system
document (not included in the repository). Models whose weather/site metadata
does not match these cities retain their existing source schedules.

## COP Defaults And Sources

Step 3 reads equipment-reference COP values from `config/climate_profiles.json`.
These values feed the series catalog and performance-curve rebase helper.
The COP of the existing chiller is read from the baseline model; the table below records
equipment-reference assumptions:

| City | Air-cooled chiller | Generic water-cooled chiller | Centrifugal water-cooled chiller |
|---|---:|---:|---:|
| Hong Kong | 3.3 | 5.5 | 5.9 |
| Beijing | 3.0 | 5.5 | 6.2 |
| Shanghai | 3.2 | 5.5 | 6.3 |
| Shenyang | 2.9 | 5.5 | 6.1 |
| Kunming | 3.0 | 5.5 | 6.1 |

Hong Kong equipment-reference values are recorded with a BEC 2024 basis.
Mainland air-cooled values follow GB 55015-2021 Table 3.2.9-1,
air-cooled/evaporatively cooled screw units with CC > 50 kW, by climate region.
Mainland centrifugal values follow GB 55015-2021 Table 3.2.9-1,
water-cooled centrifugal units with CC > 2110 kW. The generic water-cooled
chiller default is the conservative Step 3 baseline value of 5.5 for
non-centrifugal water-cooled selections.

HVAC physical constraints apply the following location-specific heat-pump rules:

| City | Current Step 3 heat-pump sources | Heating COP default / minimum |
|---|---|---|
| Beijing | air-source heat pump only | ASHP `2.4` |
| Shenyang | air-source heat pump only | ASHP `2.0` |
| Shanghai | air-source or water-source heat pump | ASHP `2.0`; WSHP `4.0` |
| Kunming | air-source or water-source heat pump | ASHP `2.0`; WSHP `4.0` |

The ASHP heating COP baseline of Beijing (cold zone, 2.4) and Shenyang (severe
cold zone, 2.0) follows GB 55015-2021 clause 5.4.3; elsewhere it follows GB 50189-2015 clause 4.2.15 and
GB 50736-2012 clause 8.2.14: winter design condition COP >= 2.0 for
hot/chilled-water units. The air-to-water heating heat pump is therefore modeled
with temperature-dependent capacity and efficiency curves (EnergyPlus
air-to-water heat-pump example data) that equal 1 at the leaving hot-water
temperature of the unit and the winter design outdoor temperature of the city
(`air_source_reference_outdoor_air_c` in `config/climate_profiles.json`:
Shenyang -20.7, Beijing -9.9, Shanghai -2.2, Kunming 0.9 C), so the reference
COP and the autosized capacity apply at that condition. The WSHP heating COP baseline follows GB 19577-2024
Table 4, using the grade 3 lower-bound COP/ACOP = 4.0 for water(source) /
ground-source heat pump units. Ground-source heat pumps are excluded from the
current Step 3 candidate set.

Low-lift cooling/heating alternatives apply the project literature rule of
about +3.5% COP per 1 C leaving-water improvement, from `Experimental
investigation of a mechanical vapour compression chiller at elevated chilled
water temperatures`. For cooling, higher leaving chilled water improves COP; for
heating heat pumps, lower leaving hot water improves COP.

## Construction Paths

`apply_standards_hvac_system` constructs supported ASHRAE system families using the OpenStudio
`openstudio-standards` gem and exposes supported `system_type` values through
`schema.py`.

The explicit construction and tuning tools support water-cooled plants, DOAS
coil control, separate terminal loops, chilled beams, heat recovery, DCV and
fan/pump/chiller parameters. Their accepted arguments are defined in `registry.py`
and checked by `schema.py`.

## Case evidence

Use the active input OSM, current design spec, physical-constraint reports,
EnergyPlus logs and `hvac_toolkit_eui_results.csv` as the case evidence.
## MCP server

Start the MCP stdio server from the repository root:

```powershell
python -m hvac_toolkit.mcp_server
```

It implements `initialize`, `ping`, `tools/list`, and `tools/call`. Configure an
MCP client to launch the command from this repository root. Protocol output is
written only to stdout; Python tracebacks are isolated on stderr. The
`mcp_tools.py` command line interface is available for manual debugging.

For a remote/client connector, the same tools are available through stateless
Streamable HTTP at `/mcp`:

```powershell
python -m hvac_toolkit.mcp_server --transport http --host 127.0.0.1 --port 8765
```

Remote clients require a reachable HTTPS endpoint with authentication.
The command above binds to the local machine at `127.0.0.1`.
