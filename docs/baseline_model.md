# Baseline model

The workflow starts from one OpenStudio model of the building to be redesigned, the baseline. The baseline of the
paper (a campus office building in Hong Kong and its four code-based variants for mainland climate zones) is not
distributed. A run takes the model from the path named by `baseline_osm` in
`three_step_workflow/config/climate_profiles.json` (default `climate_inputs/<climate>/baseline.osm`).

## What the workflow takes from the baseline

| Used by | Taken from the baseline |
|---|---|
| Step 1 and Step 2 model generation (`toolkit/ruby/replace_original_geometry.rb`) | Constructions (for the exterior wall, roof, interior wall, interior slab, ground floor, exposed floor, and fixed window; the generator finds some of them by name); space loads and their schedules; thermostat schedules; zone sizing settings; infiltration; one four-pipe fan-coil unit as the template of the zone equipment, with its fan, coils, and chilled-water loop; the dedicated outdoor air system |
| Step 3 limits (`steps/shared/physical_constraints.py`) | Fan pressure rise of the terminal fans and of the supply and relief fans of the dedicated outdoor air system; pump head; total and motor efficiency of the terminal fans; reference COP of the chiller |
| Model rules (`steps/shared/model_rules.py`) | Thermal zoning, internal loads and schedules, outdoor-air requirement and controls, constructions; a candidate must leave them unchanged |
| Comfort check (`hvac_toolkit/runner.py`) | Share of occupied hours with a predicted mean vote between -0.5 and 0.5 in the simulated baseline; a candidate may be at most 1.0 percentage point lower |

None of these values is written in the code or the configuration. `config/block_m.json` holds the design brief
only: site size and the latitude and longitude of Hong Kong, number of stories and story height, floor-area target, core size, and the number of valid designs
required in each step.

## Requirements for a baseline model

- Spaces assigned to thermal zones, with people, lighting, and equipment loads and their schedules.
- Constructions for the exterior wall, roof, interior wall, interior slab, ground floor, and exposed floor, and at
  least one fixed window.
- A dedicated outdoor air system with a supply fan and a relief fan, four-pipe fan-coil units with on-off fans and
  water cooling coils, and a chilled-water loop with at least one `Chiller:Electric:EIR` and a pump.
- Design days and a weather file for the location (`weather.epw`, `weather.ddy`, `weather.stat`).
- For a location that requires heating: a hot-water loop serving the heating coils.

The climate-specific code values (minimum chiller COP, heat-pump COP, heating period) are set in
`config/climate_profiles.json`, with their sources in `hvac_toolkit/README.md`.
