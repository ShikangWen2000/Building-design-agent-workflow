# Step 3 air-process design

Every Step 3 spec includes `air_process_design`: the air states the agent
expects its HVAC system to produce, drawn on a psychrometric chart. After
simulation, the same states are read from EnergyPlus and plotted over the
design. The comparison is feedback for the agent; it does not affect case
eligibility or ranking.

## Scenarios, hours and zone

| Scenario | Default hour (`config/block_m.json`, `hvac.psychrometric_design_hours`) |
|---|---|
| `cooling` | 21 July, hour ending 15:00 |
| `heating` | 20 January, hour ending 09:00 |

Both dates are weekdays in the simulation calendar, so every DOAS
availability schedule runs at these hours. Both hours are compared in one fixed zone: zones are grouped by floor
elevation, the middle level is chosen (the lower of the two middle levels when
the count is even), and the largest zone on that level is used. In Hong Kong the
DOAS heating coil is disabled, so the `heating` scenario describes winter
ventilation.

The Step 3 prompt gives the agent the outdoor dry-bulb temperature and humidity
ratio of the active EPW at both hours, the site pressure, the zone rule and
the fixed DOAS conventions:

- 100% outdoor air.
- Cooling-coil leaving air controlled to 12 C.
- Supply fan downstream of the coils.
- In mainland series with heating, a DOAS heating coil controlled to 20 C.

## Design fields

```json
"air_process_design": {
  "load_basis": "weather, zone load and setpoint assumptions",
  "cooling": {
    "process_notes": "outdoor -> DOAS coil -> fan -> zone supply -> zone, and the terminal",
    "states": {
      "outdoor":          {"temperature_c": <C>, "humidity_ratio_g_kg": <g/kg>},
      "coil_outlet":      {"temperature_c": <C>, "humidity_ratio_g_kg": <g/kg>},
      "doas_zone_supply": {"temperature_c": <C>, "humidity_ratio_g_kg": <g/kg>},
      "zone":             {"temperature_c": <C>, "humidity_ratio_g_kg": <g/kg>}
    }
  },
  "heating": {
    "process_notes": "...",
    "states": {
      "outdoor":          {"temperature_c": <C>, "humidity_ratio_g_kg": <g/kg>},
      "doas_zone_supply": {"temperature_c": <C>, "humidity_ratio_g_kg": <g/kg>},
      "zone":             {"temperature_c": <C>, "humidity_ratio_g_kg": <g/kg>}
    }
  }
}
```

Required states are `outdoor`, `coil_outlet`, `doas_zone_supply` and `zone`
for cooling, and `outdoor`, `doas_zone_supply` and `zone` for heating.

Optional states in either scenario:

| State | Use |
|---|---|
| `mixed` | Mixed-air point (equals outdoor for 100% outdoor air) |
| `heat_recovery_outlet` | Air leaving the heat-recovery exchanger |
| `coil_outlet` | Cooling-coil outlet in the heating scenario |
| `heating_coil_outlet` | Heating-coil outlet |
| `local_terminal_outlet` | FCU supply air |

`radiant_surface_temperature_c` is optional in either scenario. It is compared
with the predicted zone dew point.

## Validation before simulation

`stage3_hvac_interface.py` and `stage3_hvac_energyplus.py` reject a spec whose
design is missing or physically inconsistent:

- Each state lies at or below saturation at the site pressure. The pressure is
  the standard atmosphere at the EPW elevation, with a 0.1 g/kg allowance.
- A cooling coil does not warm or humidify its inlet air.
- A heating coil, the fan and the ducts change temperature only.
- DOAS zone supply keeps the humidity ratio of the last coil state.
- An FCU outlet does not warm or humidify zone air in cooling, and only heats
  it in heating.

Physical checks allow 0.5 C and 0.5 g/kg.

Valid designs are drawn before simulation as
`psychrometric/air_process_design_cooling.png` and
`psychrometric/air_process_design_heating.png`.

## Comparison after simulation

`psychrometric_evidence.py` reads the IDF and hourly SQL of the case:

| State | Source |
|---|---|
| `outdoor`, `mixed` | DOAS outdoor-air mixer nodes. With heat recovery, `outdoor` is the exchanger supply inlet. |
| `coil_outlet`, `heating_coil_outlet` | DOAS coil air outlet nodes |
| `doas_zone_supply` | Outlet node of the zone's air terminal (after reheat for a reheat terminal) |
| `local_terminal_outlet` | FCU air outlet node |
| `zone` | Zone mean air temperature and humidity ratio |
| Radiant surface | Flow-weighted mean and minimum inside-face temperature of the zone's radiant surfaces, against the zone dew point |

Every state is reported with temperature, humidity ratio, enthalpy, relative
humidity and dew point. Each predicted point also gets its temperature,
humidity-ratio and enthalpy error.

A point counts as within the diagnostic tolerance when the error is at most:

- outdoor: 3 C and 3 g/kg;
- all other states: 2 C and 1 g/kg.

The record also shows whether the DOAS delivered air at that hour.

Outputs under `step3_hvac/llm_iterations/<case_id>/psychrometric/`:

| File | Contents |
|---|---|
| `psychrometric_evidence.json` | Zone selection, node names, simulated states, comparison and radiant condensation shut-off counts |
| `air_process_cooling_design_vs_simulated.png`, `air_process_heating_design_vs_simulated.png` | Simulated DOAS path and zone (blue) over the design (red) |

A compact per-point error table is returned as `air_process_comparison` in
`feedback.json` and in the Step 3 feedback registry for the next design.

To rerun a comparison on an existing run directory:

```powershell
python steps\step3_hvac\psychrometric_evidence.py <run_dir> <output_dir> --design-spec <hvac_spec.json> --epw <weather.epw>
```

Node discovery supports the DOAS constructors with a water cooling coil.
Other topologies report `unavailable`, and the other Step 3 checks still apply.

EnergyPlus references: [Engineering Reference, psychrometric functions](https://energyplus.net/assets/nrel_custom/pdfs/pdfs_v22.1.0/EngineeringReference.pdf),
[Input Output Reference, node outputs](https://bigladdersoftware.com/epx/docs/22-2/input-output-reference/group-node-branch-management.html).
