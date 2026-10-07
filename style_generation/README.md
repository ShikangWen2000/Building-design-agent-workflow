# Block M style generation task

> Note on this release: this document is kept as it was used in the experiments and describes the complete
> repository. The baseline model, the weather file of the
> measured year, and the scripts for model generation, simulation, and validation that it names are not part
> of this release (see the README at the root).

A self-contained task for an agent: redesign an energy-designed 18-story Hong
Kong office building in the manner of three architects (Zaha Hadid, Frank
Gehry, Jean Nouvel), one after the other, building each design as an
OpenStudio model. The repository provides the start model, the baseline model,
the weather file, the style brief, the rules and a validation layer. It
provides no tools for generating geometry, windows or shading.

The start model is the selected envelope (Step 2) design of a Claude Opus 5.5
run of the three-step energy-design workflow
(`inputs/start/start_reference.json`). The validation layer is that of the
envelope step of the three-step design task (fixed inputs, baseline HVAC,
window and shading rules), with the massing rules of its massing step, since a
style may change the form; the massing limits allow more tiers and vertices
(`config/task_config.json`, `style.massing_overrides`).

- `TASK.md`: the task, the procedure and the attempt budget.
- `requirements/style_brief.md`: the three briefs and their reference images.
- `requirements/style_requirements.md`: the rules.
- `config/task_config.json`: site, building and limit values, style order, budget.
- `inputs/start/`: start model, its massing declaration and its origin.
- `inputs/hong_kong/`: baseline model and weather file.
- `validator/`: the validation layer.

## Requirements

- OpenStudio 3.5.1 (EnergyPlus 22.2). The validator looks for the OpenStudio
  command line program in the environment variable `OPENSTUDIO_EXE`, then in
  `openstudio_exe` of `config/task_config.json`, then on `PATH`.
- Python 3.10 or later. The checks use the standard library only; the render
  of each attempt needs matplotlib (without it no render is written).
- A graphics adapter with OpenGL. The models calculate shading by pixel
  counting, which EnergyPlus runs on the graphics adapter.

## The validation layer

```sh
python validator/submit.py reference --run-root <run>
python validator/submit.py submit    --run-root <run> --style <style> --osm <model.osm> --massing <massing.json> --note "..." [--repair-of <attempt id>]
python validator/submit.py select    --run-root <run> --style <style> [--attempt <attempt id> --reason "..."]
python validator/submit.py status    --run-root <run>
```

`<run>` is a new folder outside the repository. A submission is processed in
this order:

1. the model is loaded and described (`validator/ruby/model_dump.rb`);
2. the model checks run (`validator/checks.py`): the fixed inputs and the
   baseline HVAC are unchanged, the massing rules hold and the model is the
   declared massing, the core shading box is present, the window and shading
   rules hold, the design differs from its starting model and is not a repeat;
3. if they pass, the model is simulated (`validator/simulation.py`): 6
   timesteps per hour, full year, the standard cooling-coil sizing passes;
4. the result checks run: zero Severe and Fatal errors, conditioned area, no
   district energy.

Each attempt is recorded in `<run>/ledger.jsonl` and in
`<run>/<style>/attempt_<nnn>/` (`submitted.osm`, `massing.json`,
`checks.json`, `result.json`, `feedback.json`, `render.png`, the simulation
folder without its large output files).

| File | Purpose |
|---|---|
| `validator/submit.py` | command line, attempt ledger, style selection |
| `validator/checks.py` | model and result checks (`style_checks`; shared with the three-step design task) |
| `validator/geometry.py` | geometry helpers |
| `validator/render.py` | axonometric preview of a model (information only) |
| `validator/simulation.py` | standard simulation and energy results |
| `validator/energy_metrics.py` | site energy by end use from the EnergyPlus summary |
| `validator/coil_sizing.py`, `validator/ruby/cooling_coil_sizing.rb` | cooling-coil sizing passes |
| `validator/ruby/model_dump.rb` | model description read by the checks |
| `validator/ruby/prepare_simulation.rb` | simulation settings and output requests |
