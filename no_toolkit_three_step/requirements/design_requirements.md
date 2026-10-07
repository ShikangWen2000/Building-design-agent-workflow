# Design requirements

These are the rules a submitted model must satisfy. The validator
(`validator/submit.py`) checks every rule and simulates the model; a submission
is valid only when every check passes. Numbers are in
`config/task_config.json` and `requirements/equipment_performance.json`; where
this text and those files differ, the files apply.

## 1. Project and objective

- Building: an 18-story office building in Hong Kong ("Block M") on a
  45 m x 45 m site between other buildings.
- Inputs: `inputs/hong_kong/baseline.osm` (OpenStudio 3.5.1 model of the
  existing building, with its surroundings, loads, schedules, constructions and
  HVAC system) and `inputs/hong_kong/weather_2021_measured.epw`.
- Objective: the lowest **site energy use intensity** in kWh/m2 per year: all
  end uses and all fuels of the EnergyPlus annual summary
  (`AnnualBuildingUtilityPerformanceSummary`, End Uses) divided by the net
  conditioned building area. No source-energy or carbon factors.
- The validator computes the result from the submitted model. Numbers computed
  elsewhere are not used.

The design is done in three steps, each starting from the model selected in
the step before:

| Step | Design variables | Everything else |
|---|---|---|
| 1 Massing | building form (footprints, tiers, rotation) | fixed windows, core shading box and no other added shading, baseline HVAC |
| 2 Envelope | windows and exterior shading | Step 1 massing and core shading box, baseline HVAC |
| 3 HVAC | the HVAC system | Step 2 massing and envelope |

## 2. Fixed in every step

The following are taken from the baseline model and may not be changed, in any
step. Use the objects that are already in the baseline model.

- **Site and weather**: site object, design days, year description, special
  days and daylight-saving time (none), ground and
  water-mains temperatures, shadow-calculation, heat-balance and
  surface-convection settings, zone air heat balance and capacitance
  settings. Building north axis as in the baseline model. The validator sets the weather
  file, the timestep (6 per hour), the full-year run period and its own
  output requests.
- **Internal loads and air**: the single space type of the baseline model with
  its people, lighting and electric-equipment objects and definitions, its
  infiltration object (flow per exterior surface area) and its outdoor-air
  specification (per person), including every schedule they use. Every space
  uses this space type and outdoor-air specification, carries no loads of its
  own, counts towards the floor area and has multiplier 1. No other load,
  infiltration, zone ventilation or mixing object may be added.
- **Thermostats**: every thermal zone has a dual-setpoint thermostat with the
  heating and cooling setpoint schedules of the baseline model, unchanged.
  (Step 3 allows two declared changes, section 6.) Zone multipliers are 1.
- **Constructions**: each surface uses the baseline construction of its kind,
  unchanged:

  | Surface | Construction |
  |---|---|
  | exterior wall (boundary Outdoors) | the baseline exterior-wall construction |
  | wall on the core (boundary Adiabatic) and interior wall | the baseline interior-wall construction |
  | roof (RoofCeiling, Outdoors) | the baseline roof construction |
  | interior floor and ceiling (matched Surface pair) | the baseline interior-slab construction |
  | ground floor (lowest floor, Adiabatic) | the baseline ground-floor construction |
  | floor exposed to outdoors (overhanging soffit) | the baseline exposed-floor construction |
  | window (FixedWindow) | the baseline window construction |

  Every sub-surface is a fixed window with multiplier 1 and no shading control.
- **Surrounding buildings**: every shading surface of the baseline model that
  is not entirely inside the site rectangle stays exactly where it is. No
  shading surface has a transmittance schedule.
- **Floor area**: 16 315 m2 +/- 250 m2 (model floor area and the conditioned
  area reported by EnergyPlus).
- **Zoning**: each story is exactly one thermal zone, and each thermal zone is
  exactly one story.
- **Not allowed in any step**: daylighting controls, window shading controls,
  frames and dividers, on-site generation, energy management programs, ideal
  air loads, interval or file schedules, water use, refrigeration, exterior
  lights and equipment, external interfaces.
- EnergyPlus must finish the annual simulation with zero Severe and zero Fatal
  errors, and no district heating or cooling energy.

## 3. Coordinates

- Model coordinates are those of the baseline model. The site rectangle is
  axis-aligned, 45 m x 45 m, centred at the site centre of the baseline model.
- The ground level of the new building is z = 0.
- Orientation of a wall: the compass direction of its outward normal,
  `atan2(nx, ny)` in degrees plus the building north axis. North is
  315-45, east 45-135, south 135-225, west 225-315 degrees.

## 4. Step 1 - massing

Design variables: the footprint polygons, their grouping into tiers, and the
rotation of the building.

Rules for the massing:

- 18 stories of 4.0 m; floors at z = 0, 4, ..., 68 m; top at 72 m.
- One to four tiers. A tier is a range of consecutive stories with one
  footprint polygon. The tiers cover every story exactly once.
- A footprint is a simple polygon with at most 12 vertices and no facade
  segment shorter than 1.0 m.
- After rotation, every footprint lies inside the site rectangle.
- The core is a fixed rectangle of 21 m x 12 m (21 m along the local x axis),
  centred on the site centre, rotating with the building. It lies strictly
  inside every footprint, at least 4.5 m from the facade everywhere, and takes
  at most 30% of the gross area of any tier.
- An upper tier extends at most 1.5 m beyond the tier below (bounding boxes),
  and at most one tier transition expands upward.
- Net floor area (footprint area minus core area, summed over the stories) is
  16 315 m2 +/- 250 m2.

Each Step 1 submission has a `massing.json` next to the model:

```json
{
  "rotation_deg": 0.0,
  "tiers": [
    {"story_start": 0, "story_end": 18, "footprint": [[x, y], [x, y], [x, y], [x, y]]}
  ]
}
```

Footprint vertices are in metres in the building's local frame with the site
centre at (0, 0); `story_end` is exclusive; `rotation_deg` is the
counter-clockwise rotation, seen from above, applied to the footprints and the
core about the site centre.

Rules for the model:

- The model geometry is the declared massing. On each story the floor area is
  the footprint minus the core; exterior walls (boundary Outdoors, sun and wind
  exposed) stand on the footprint; the walls facing the core stand on the core
  rectangle, with boundary Adiabatic and no windows. The core itself is not
  modelled as a space.
- The ground floor is Adiabatic. Floors and ceilings between stories are
  matched Surface pairs with identical vertices. A roof, and a floor that
  overhangs the story below, has boundary Outdoors. The top roof is Outdoors.
- Every exterior wall carries exactly one rectangular window covering 0.56 of
  the wall area, inset by the same distance from all four wall edges. No other
  surface has a window.
- The core is modelled as a shading box: shading surfaces cover each of the
  four sides of the core rectangle from the ground (z = 0) to the top of the
  building (72 m), and the core rectangle at 72 m. A side or the top may be
  split into several surfaces.
- No other shading is added. The shading surfaces of the baseline model that
  lie inside the site rectangle belong to the existing building and are
  removed with it.
- The HVAC system is the baseline system (section 7), extended to the thermal
  zones of the new massing.

## 5. Step 2 - envelope

Design variables: the windows of the exterior walls and exterior shading.

- The massing is the selected Step 1 model: every wall, floor and roof polygon,
  its boundary condition, the core shading box and the zoning are unchanged.
- Windows are on exterior walls only.
- For each orientation, window area over exterior wall area is between 0.50
  and 0.90; a deviation of 0.02 is tolerated.
- Added shading (any shading surface that is not part of the surrounding
  buildings or the core box) is judged per window, seen along the facade
  normal:
  - a shading surface belongs to a window when it stands in front of that
    window's wall (nowhere behind the wall plane, nowhere more than 1.5 m in
    front of it), on the window's story, and either spans at least 90% of the
    window width or has at least half of its own length in front of the
    window; a fin (a board whose normal runs along the facade) belongs to the
    window it stands in front of or whose side edge it sits on (within 0.05 m);
    a piece parallel to the facade (a screen or its frame) belongs to the
    window it is in front of or within 0.20 m beside;
  - every added shading surface must belong to at least one window;
  - the projection of the shading that belongs to a window covers at most 30%
    of that window, and at most 30% of the window area of each orientation;
  - a board stands out less than 1.0 m from the facade;
  - in front of one window, horizontal boards are at least 3.0 m apart
    vertically and fins at least 3.0 m apart horizontally;
  - the added shading surfaces (one side; the core shading box is not
    counted) total at most 30% of the building's exterior surface area (gross
    area of the walls and roofs with an outdoor boundary, windows included).
- The HVAC system is the baseline system, unchanged from the Step 1 model.

## 6. Step 3 - HVAC

Design variable: the HVAC system (zone equipment, air systems, plant, their
controls and sizing inputs).

- The building is the selected Step 2 model: every surface, window and shading
  surface and the zoning are unchanged. A surface used as a radiant surface may
  use an internal-source construction that keeps every layer of its baseline
  construction, in order, and adds its own layers.
- Every thermal zone has HVAC equipment and receives mechanical outdoor air
  from an air loop with an outdoor-air system. Every air loop with an
  outdoor-air system is a 100% outdoor-air loop (DOAS: 100% outdoor air in
  cooling and in heating).
- **Schedules and loads.** The load objects, the outdoor-air specification and
  their schedules stay as in the baseline (section 2). Schedules are not
  changed, with two exceptions, each declared at submission with its reason
  (`--declaration <file.json>`); an undeclared change makes the design
  invalid:
  - A: the design-day profiles (summer and winter design day) of
    baseline heating and cooling setpoint schedules, for sizing;
  - B: every cooling setpoint of the simulated days (all day profiles other
    than the design days, every rule) raised by one offset 0 < delta <= 1.0 K,
    only in zones with radiant cooling or chilled beams: they lower the mean
    radiant temperature, so the operative temperature is lower at the same air
    temperature. The times, rules and dates of the schedule stay those of the
    baseline.

  ```json
  {"setpoint_design_day_profiles": {"reason": "..."},
   "cooling_setpoint_offset": {"delta_k": 0.5, "reason": "..."}}
  ```

  A new schedule must hold one value all year and may only be used as a
  setpoint or an availability. Control that varies in time may only use a
  baseline schedule, unchanged.
- **Outdoor air.** The outdoor-air flow is set only by the demand-controlled
  ventilation switch (`Demand Controlled Ventilation`) of the loop's
  `Controller:MechanicalVentilation`, whose method stays `ZoneSum` and whose
  availability stays the baseline one. The fields of `Controller:OutdoorAir`
  that set the outdoor-air flow keep the baseline values (minimum and maximum
  outdoor-air flow, minimum limit type, minimum outdoor-air schedule, minimum
  and maximum outdoor-air fraction schedules, time-of-day economizer schedule,
  high-humidity control fields). Air terminals do not use a schedule to set
  their minimum air flow. Zone equipment brings in no outdoor air.
- **Exhaust fan.** Every 100% outdoor-air loop has an exhaust (relief) fan on
  its outdoor-air system: pressure rise at least that of the baseline relief fan, total efficiency at
  most 0.55, motor efficiency at most 0.92 (the supply-fan limits), autosized
  flow, the same availability as the supply fan. With heat recovery the
  exhaust air passes the heat-recovery exchanger.
- **Condensation.** Radiant cooling uses condensation control `SimpleOff` or
  `VariableOff` with a dew-point offset of at least 1 C. Chilled beams are
  judged on the simulation (below).
- Each plant loop has at most one chiller (or one cooling heat pump) on its
  supply side; several chillers may not be staged on one loop.
- The condenser water of water-cooled chillers is held at 32 C all year (the
  reference entering condenser temperature): the condenser loop's supply
  outlet has only scheduled setpoint managers whose schedules are always 32 C,
  and the loop's design exit temperature is 32 C. No wet-bulb or outdoor-air
  reset.
- Plant loop design temperature differences (sizing): a chilled-water loop
  with a design exit temperature below 10 C uses 5 K; a high-temperature
  chilled-water loop (design exit 10 C or more, for radiant or beam terminals)
  at most 5 K; the condenser loop of water-cooled chillers 5 K.
- Chillers, heat pumps and boilers are autosized: capacity, flow rates and
  reference power are autosized, with a component sizing factor of at least
  1.0.
- The chilled-water loop of every 100% outdoor-air cooling coil supplies at
  most 7 C: design exit temperature, a scheduled supply setpoint that never
  exceeds 7 C, and chillers rated at a leaving temperature of at most 7 C.
- Every 100% outdoor-air loop contains a cooling coil and a heating coil. Hong
  Kong has no heating period: the heating coil or its water loop is disabled
  (zero capacity or availability always off).
- Ventilation: in the simulation, in every zone, the mechanical outdoor air is
  at least the requirement of each hour (occupants x the baseline outdoor air
  per person, 8 L/s, x the baseline outdoor-air fraction schedule) in at least 99% of the occupied hours, and over the occupied
  hours in total it is at least 0.99 of that requirement.
- Humidity: in every zone the relative humidity is at most 70% in at least 95%
  of the ventilation hours (occupied hours with that schedule above zero),
  occupant-weighted.
- Chilled beams: in the hours a beam cools, its chilled-water inlet temperature
  is at least the zone dew point + 1 C in at least 99% of them, for every beam.
- Reported, without a limit: cooling-setpoint-not-met hours and ASHRAE 55
  (simple) discomfort hours of the building and of each zone, with the values
  of the baseline model for reference (`feedback.json`, `result.json`).
- Comfort: the share of occupied hours with PMV between -0.5 and +0.5
  (occupant-weighted; 1.1 met, 0.5 clo, air speed 0.1 m/s) is at most one
  percentage point below that of the baseline model.
- Equipment performance may not be better than the reference values of
  `requirements/equipment_performance.json`:
  - electric chillers: reference COP at most 3.3 (air-cooled, 35 C entering
    condenser air) or 5.5 (water-cooled, 32 C entering condenser water) at 7 C
    leaving chilled water, plus 3.5% per degree of reference leaving
    chilled-water temperature above 7 C, up to 16 C; the capacity and EIR
    temperature curves are the reference curve set of the condenser type,
    divided by their value at the chiller's reference conditions, and the
    part-load curve is the reference part-load curve;
  - FCU (zone-equipment) fans: total and motor efficiency at most those of the
    baseline FCU fans;
  - other fans: total efficiency at most 0.55, motor efficiency at most 0.92,
    pressure rise at least that of the corresponding baseline fan (fan inside
    zone equipment, fan on an air loop, relief or exhaust fan);
  - pumps: head at least the baseline pump head, motor efficiency at most 0.90;
  - boilers: thermal efficiency at most 0.90;
  - air-to-air heat recovery: sensible and latent effectiveness at most 0.80;
  - DX cooling coils and variable-refrigerant-flow units: rated cooling COP at
    most 3.3; air-source heat pumps on a plant loop 3.2; water-source heat
    pumps 5.0;
  - absorption chillers, evaporative coolers and thermal storage are not
    allowed.

## 7. The baseline HVAC system (Steps 1 and 2)

In Steps 1 and 2 the HVAC system is that of the baseline model, with one set
of zone equipment per thermal zone:

- per zone: a four-pipe fan coil unit (on-off fan, chilled-water cooling coil,
  electric heating coil of zero capacity) and a constant-volume no-reheat air
  terminal on the outdoor-air loop, in the baseline equipment order;
- one 100% outdoor-air loop serving every zone, with the baseline outdoor-air
  system, chilled-water cooling coil, supply fan and controls;
- one chilled-water loop with the baseline air-cooled chiller and
  variable-speed pump, serving the terminal cooling coil of every zone and
  the outdoor-air cooling coil.

Every component, sizing object, controller, setpoint manager and thermostat
must have the field values of the corresponding baseline object (performance,
control and sizing inputs; names and connections are free). The validator
compares them field by field.

## 8. How a submission is simulated

Every model is simulated the same way by the validator: 6 timesteps per hour,
full year, the project weather file. Before the annual run the validator
applies its standard cooling-coil sizing passes (`validator/coil_sizing.py`):
fan-coil cooling coils whose zone heating flow exceeds the cooling flow are
sized on the cooling flow; in Step 3, chilled-water coils on 100% outdoor-air
loops are also sized on the 0.4% enthalpy design day when that gives the larger
coil, and four-pipe beam lengths are sized to the zone design cooling load.
