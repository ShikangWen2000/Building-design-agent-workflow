# Style requirements

These are the rules a submitted model must satisfy. The validator
(`validator/submit.py`) checks every rule and simulates the model; a submission
is valid only when every check passes. Numbers are in `config/task_config.json`;
where this text and that file differ, the file applies.

## 1. Project and objective

- Building: an 18-story office building in Hong Kong ("Block M") on a
  45 m x 45 m site between other buildings.
- Inputs: `inputs/hong_kong/baseline.osm` (the existing building with its
  surroundings, loads, schedules, constructions and HVAC system),
  `inputs/hong_kong/weather_2021_measured.epw`, and the start model
  `inputs/start/step2_selected_e10.osm` (the selected envelope design of an
  energy-design run; see `inputs/start/start_reference.json`).
- Objective: redesign the start model in the manner of three architects, one
  after the other (`requirements/style_brief.md`): Zaha Hadid, then Frank
  Gehry, then Jean Nouvel. Each style starts from the design selected for the
  style before; the first starts from the start model. A styled design must be
  recognisably in the manner of its architect and remain a valid,
  simulation-ready model under the same rules as the energy design.
- Energy is reported, not optimised: every valid design is simulated and its
  **site energy use intensity** (kWh/m2 per year, all end uses and fuels of the
  EnergyPlus annual summary divided by the net conditioned area) is compared
  with the design the style started from. Lower is welcome; the style comes
  first.

| Design variables | Everything else |
|---|---|
| building form (footprints, tiers, rotation), windows, exterior shading | loads, schedules, constructions, thermostats, zoning rule, core, baseline HVAC, surroundings |

## 2. Fixed in every design

The following are taken from the baseline model and may not be changed in any
design. Use the objects that are already in the baseline model.

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
  Zone multipliers are 1.
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

## 4. Massing

Design variables: the footprint polygons, their grouping into tiers, and the
rotation of the building. The limits are those of the energy design's massing
step, with more freedom of form for the styles (marked *style*):

- 18 stories of 4.0 m; floors at z = 0, 4, ..., 68 m; top at 72 m.
- One to 18 tiers (*style*; the energy design allows four). A tier is a range
  of consecutive stories with one footprint polygon. The tiers cover every
  story exactly once.
- A footprint is a simple polygon with at most 24 vertices (*style*; energy
  design 12) and no facade segment shorter than 1.0 m. Curves are drawn as
  polygons.
- After rotation, every footprint lies inside the site rectangle.
- The core is a fixed rectangle of 21 m x 12 m (21 m along the local x axis),
  centred on the site centre, rotating with the building. It lies strictly
  inside every footprint, at least 4.5 m from the facade everywhere, and takes
  at most 30% of the gross area of any tier.
- An upper tier extends at most 1.5 m beyond the tier below (bounding boxes).
  Any number of tier transitions may expand upward (*style*; energy design one).
- Net floor area (footprint area minus core area, summed over the stories) is
  16 315 m2 +/- 250 m2.

Each submission has a `massing.json` next to the model:

```json
{
  "rotation_deg": 10.0,
  "tiers": [
    {"story_start": 0, "story_end": 18, "footprint": [[x, y], [x, y], [x, y], [x, y]]}
  ]
}
```

Footprint vertices are in metres in the building's local frame with the site
centre at (0, 0); `story_end` is exclusive; `rotation_deg` is the
counter-clockwise rotation, seen from above, applied to the footprints and the
core about the site centre. `inputs/start/start_massing.json` is the
declaration of the start model.

Rules for the model:

- The model geometry is the declared massing. On each story the floor area is
  the footprint minus the core; exterior walls (boundary Outdoors, sun and wind
  exposed) stand on the footprint; the walls facing the core stand on the core
  rectangle, with boundary Adiabatic and no windows. The core itself is not
  modelled as a space.
- The ground floor is Adiabatic. Floors and ceilings between stories are
  matched Surface pairs with identical vertices. A roof, and a floor that
  overhangs the story below, has boundary Outdoors. The top roof is Outdoors.
- The core is modelled as a shading box: shading surfaces cover each of the
  four sides of the core rectangle from the ground (z = 0) to the top of the
  building (72 m), and the core rectangle at 72 m. A side or the top may be
  split into several surfaces.
- The HVAC system is the baseline system (section 6), extended to the thermal
  zones of the new massing.

## 5. Envelope

The windows and the exterior shading follow the rules of the energy design's
envelope step:

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
- A design must differ from the model its style starts from, and from every
  design submitted before in the run.

## 6. The baseline HVAC system

The HVAC system is that of the baseline model, with one set
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

## 7. How a submission is simulated

Every model is simulated the same way by the validator: 6 timesteps per hour,
full year, the project weather file. Before the annual run the validator
applies its standard cooling-coil sizing passes (`validator/coil_sizing.py`):
fan-coil cooling coils whose zone heating flow exceeds the cooling flow are
sized on the cooling flow.
