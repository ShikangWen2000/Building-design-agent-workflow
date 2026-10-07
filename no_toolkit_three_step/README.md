# Block M three-step design task

> Note on this release: this document is kept as it was used in the experiments and describes the complete
> repository. The baseline model, the weather file of the
> measured year, and the scripts for model generation, simulation, and validation that it names are not part
> of this release (see the README at the root).

A self-contained design task for an agent: improve the site energy use
intensity of an 18-story Hong Kong office building in three steps (massing,
envelope, HVAC), building each design as an OpenStudio model. The repository
provides the baseline model, the weather file, the design requirements and a
validation layer. It provides no tools for generating geometry, windows,
shading or HVAC systems.

- `TASK.md`: the task, the procedure and the attempt budget.
- `requirements/design_requirements.md`: the rules of the three steps.
- `requirements/equipment_performance.json`: reference equipment performance.
- `config/task_config.json`: site, building and limit values.
- `inputs/hong_kong/`: baseline model and weather file.
- `validator/`: the validation layer.

## Requirements

- OpenStudio 3.5.1 (EnergyPlus 22.2). The validator looks for the OpenStudio
  command line program in the environment variable `OPENSTUDIO_EXE`, then in
  `openstudio_exe` of `config/task_config.json`, then on `PATH`.
- Python 3.10 or later. The validator uses the standard library only.
- A graphics adapter with OpenGL. The baseline model calculates shading by
  pixel counting, which EnergyPlus runs on the graphics adapter.

## The validation layer

```sh
python validator/submit.py baseline --run-root <run>
python validator/submit.py submit   --run-root <run> --step <1|2|3> --osm <model.osm> [--massing <massing.json>] [--note "..."] [--repair-of <attempt id>]
python validator/submit.py select   --run-root <run> --step <1|2|3>
python validator/submit.py status   --run-root <run>
```

`<run>` is a new folder outside the repository. A submission is processed in
this order:

1. the model is loaded and described (`validator/ruby/model_dump.rb`);
2. the model checks run (`validator/checks.py`): the fixed inputs are
   unchanged, the rules of the step hold, the design is not a repeat;
3. if they pass, the model is simulated (`validator/simulation.py`): 6
   timesteps per hour, full year, the standard cooling-coil sizing passes;
4. the result checks run: zero Severe and Fatal errors, conditioned area, no
   district energy, and in Step 3 the outdoor-air and comfort checks.

Each attempt is recorded in `<run>/ledger.jsonl` and in
`<run>/step<N>/attempt_<nnn>/` (`submitted.osm`, `checks.json`, `result.json`,
`feedback.json`, the simulation folder without its large output files).

| File | Purpose |
|---|---|
| `validator/submit.py` | command line, attempt ledger, step selection |
| `validator/checks.py` | model and result checks |
| `validator/geometry.py` | geometry helpers |
| `validator/simulation.py` | standard simulation, energy, comfort and ventilation results |
| `validator/energy_metrics.py` | site energy by end use from the EnergyPlus summary |
| `validator/coil_sizing.py`, `validator/ruby/cooling_coil_sizing.rb` | cooling-coil sizing passes |
| `validator/ruby/model_dump.rb` | model description read by the checks |
| `validator/ruby/prepare_simulation.rb` | simulation settings and output requests |
