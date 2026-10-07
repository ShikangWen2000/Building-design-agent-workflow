# MCP-Ready HVAC Tools for an LLM

Use this toolkit only through validated tool plans. Do not edit OSM text
directly and do not rank unsimulated cases.

## Tool Loop

1. Inspect the selected Step 2 envelope OSM with `inspect_osm_hvac_topology`.
2. Author one JSON `tool_plan`.
3. Validate it with `validate_hvac_tool_plan`.
4. Apply it with `apply_hvac_tool_plan`.
5. Simulate it with `simulate_hvac_case`.
6. Rank only candidates that pass tool-effect checks and have zero Severe/Fatal
   EnergyPlus errors.

## LLM Policy

- Judge candidates only from the active Block M run outputs and current
  simulation evidence.
- `apply_standards_hvac_system` builds ASHRAE 90.1-style system families; the
  FCU, radiant and chilled-beam `construct_*` tools and the tuning tools
  set plant, latent-control, heat-recovery, fan, pump and chiller behavior
  explicitly. No tool or family is preferred in advance.
- For compound systems such as DOAS plus zone equipment, chain tool calls and
  make ownership of outdoor air explicit.
- Keep latent control credible for the active climate and humidity conditions.
- Exclude any EnergyPlus Severe/Fatal case from ranking even if the EUI looks
  attractive.

## Radiant Surfaces

The baseline roof and ceilings are ordinary constructions without radiant panels. Radiant constructors, and the `add_radiant_ceiling_metal_panels` tool (`panel_scope`: `all_ceilings` or `roof_only`), keep every existing layer of each roof and ceiling and add a room-side ceiling cavity (when absent) and two 3.175 mm metal panel layers, with the chilled-water internal source between the metal layers. The slab above each converted ceiling receives the reversed construction.

## Current Radiant Defaults

For `construct_doas_lowtemp_radiant` and existing low-temperature radiant
tuning, use these defaults unless active simulation feedback gives a stronger
reason to change them:

- `cooling_capacity_method`: `autosize`
- `temperature_control_type`: `OperativeTemperature`, with
  `cooling_control_temperature_c` 26 C and, in climates with a heating
  period, `heating_season_cooling_control_temperature_c` 24 C during that
  period, where PMV uses 1.0 clo. A cooled ceiling lowers the mean radiant
  temperature, so radiant cooling controls operative rather than air
  temperature. All systems are compared on occupied PMV.
- `setpoint_control_type`: `ZeroFlowPower`
- `doas_cooling_sat_c`: `12.0`
- `terminal_chw_supply_c`: `16.0`

Use `capacity_per_floor_area` only when a documented radiant-panel heat-flux
limit is the intended experiment; it then requires `cooling_capacity_w_per_m2`.
Never combine a W/m2 input with `autosize`. Keep the design-day load and annual
radiant surface/recovery peaks as separate reported quantities.

For `construct_doas_lowtemp_radiant` and `construct_doas_chilled_beam`,
`doas_cooling_sat_c` may be changed only within `10.0-16.0 C`. Any value
other than `12.0 C` must include a non-empty
`doas_cooling_sat_justification` explaining the latent-control, comfort, or
energy basis for the change. This is enforced by the shared Step 3 validation
layer for every project location.

Post-simulation PMV checks use occupied periods from `Zone People Occupant
Count` when available, otherwise the baseline outdoor-air fraction schedule;
heating/non-heating PMV clothing follows the OSM heating source availability
schedule when available. Cooling-season clothing is `0.50 clo`.

## Current Chilled-Beam Defaults

Chilled-beam catalog defaults (change them only with a stated basis):

- `beam_type`: `four_pipe` in every series; cooling-only series keep the
  heating coil on a zero-capacity loop with its availability off.
- `design_inlet_water_temperature_c`: `15.0`
- `design_outlet_water_temperature_c`: `18.0`
- `primary_airflow_multiplier`: `1.0`
- Beam length: omit `beam_length_m` and `beam_length_per_area_m_m2`; the zone
  length is then sized to the zone design cooling load divided by the rated
  capacity per length.
- `beam_chw_flow_per_length_m3_s_m`: omit; the default carries the rated
  capacity at the design inlet/outlet water temperature difference.
- `beam_cooling_capacity_per_length_w_m`: `960.0`
- `beam_cooling_room_air_chw_delta_t_c`: `8.0`
- `fan_pressure_pa`, `doas_relief_fan_pressure_pa`,
  `doas_supply_fan_pressure_pa`: omit unless explicitly tuning fan pressure.
  Defaults are read from the active same-location baseline. Do not use Hong Kong
  pressure values for Beijing, Shanghai, Shenyang, or Kunming.
- DOAS high-lift chilled water stays `7/12C` for latent control.
- Every DOAS in all five locations requires cooling and heating coils. In
  the mainland the heating coil is controlled to `20 C` leaving-air
  temperature during the heating period. Hong Kong keeps the same topology
  with the heating availability always off and a placeholder controller at
  the cooling-coil leaving-air target.

Cooling-capacity source: ASHRAE Houston's chilled-beam application guidance
lists active chilled beam cooling up to `1000 Btu/h/ft`. The conversion
`1000 Btu/h/ft * 0.293071 / 0.3048 = 961 W/m`, so `960 W/m` is recorded as an
upper-bound product selection, not an arbitrary tuning knob.
## Formal series settings

New FCU, radiant and beam constructors set cooling sizing factor 1.05 at
Sizing:Parameters, with component cooling factors 1.0. FCU series use the shared
DOAS/FCU chilled-water plant; `fcu_chw_topology: split` is for diagnostics and
terminal-only capacity/COP overrides. Formal series use zero prestart. Radiant
series use operative-temperature control at 26 C (24 C in the heating period), `SimpleOff` condensation
control and a 2 K dewpoint margin.
