# Task: style-referenced redesign of the Block M office building

You are the design agent. Starting from an energy-designed version of the
building (the start model), redesign it three times, in the manner of three
architects, one after the other: Zaha Hadid, Frank Gehry, Jean Nouvel. You make
the design decisions and you build each design as an OpenStudio model yourself.
The validator in this repository checks each model against the rules and
simulates it.

Read `requirements/style_brief.md` (the three briefs and their references) and
`requirements/style_requirements.md` (every rule the validator applies) before
designing.

## What you are given

| Path | Content |
|---|---|
| `inputs/start/step2_selected_e10.osm` | start model (OpenStudio 3.5.1): massing, windows and shading of the selected envelope design of an energy-design run |
| `inputs/start/start_massing.json` | massing declaration of the start model |
| `inputs/start/start_reference.json` | origin of the start model |
| `inputs/hong_kong/baseline.osm` | the existing building; reference for every fixed item |
| `inputs/hong_kong/weather_2021_measured.epw` | weather file of the simulations |
| `requirements/style_brief.md` | the three briefs and reference images |
| `requirements/style_requirements.md` | the rules |
| `config/task_config.json` | site, building and limit values, style order, attempt budget |
| `validator/` | the validation layer; use it through `validator/submit.py` |

Nothing else is provided: there is no geometry, window, shading or rendering
generator for you. How you produce the models is your decision (OpenStudio SDK
or CLI, EnergyPlus, your own scripts, public libraries and documentation).

## Procedure

Run once at the start (it simulates the baseline and the start model):

```sh
python validator/submit.py reference --run-root <run>
```

For each style, in the order `zaha_hadid`, `frank_gehry`, `jean_nouvel`:

1. Design one case at a time. Write down the design intent (which features of
   the architect and of the reference you translate, and how), build the
   model and its `massing.json`, and submit:

   ```sh
   python validator/submit.py submit --run-root <run> --style zaha_hadid --osm <model.osm> --massing <massing.json> --note "<design intent>"
   ```

   The command prints the verdict and writes `feedback.json` (failed checks
   with their data, energy by end use, orientation WWR, floor area, EnergyPlus
   messages) and `render.png` (an axonometric preview made from the model)
   under `<run>/<style>/attempt_<nnn>/`. Use `--repair-of <attempt id>` when
   a submission repairs an earlier one.
2. Read the feedback and look at the render of that case, then decide the
   next case.
3. Stop the style when it has reached its target number of valid designs or
   used its attempts, then close it by choosing the valid design that
   expresses the style best:

   ```sh
   python validator/submit.py select --run-root <run> --style zaha_hadid --attempt <attempt id> --reason "<why this one>"
   ```

   Without `--attempt` the valid design with the lowest site EUI is taken. The
   selection is written to `<run>/<style>/selected.osm`; the next style starts
   from it. If a style has no valid design, its starting model is carried
   forward and this is recorded.

`python validator/submit.py status --run-root <run>` shows the counts.

## Budget

| Style | Valid designs wanted | Attempts allowed |
|---|---|---|
| each of the three | 3 | 10 |

Every `submit` is one attempt: valid designs, rejected designs, repeats,
repairs and failed simulations all count. The validator stops accepting
submissions for a style when the target or the attempt limit is reached. Do
not relax a rule to reach the target; report what was not reached.

## Rules of conduct

- Do not modify `validator/`, `config/`, `requirements/` or `inputs/`. The
  validator records a hash of these files with every attempt.
- Use the validator only through `submit.py`. Its checks and its simulation
  procedure are the acceptance test, not a design tool.
- Use only this repository, the software you install, public documentation
  and the reference images of the brief. Do not use other repositories, other
  project folders on the machine, or results of earlier runs of this or a
  similar task.
- Design one case at a time from the evidence of this run. Do not generate a
  fixed batch of cases in advance.
- Keep every attempt, including failed ones.

## What to keep in the run root

The validator writes the ledger (`ledger.jsonl`), the attempts, the renders
and the selections. In addition keep, in the run root:

- `design_log.md`: for every attempt the design intent, the features taken
  from the architect and the reference, how the model was built, and what the
  feedback and the render showed;
- the scripts you wrote to build the models;
- `summary.md`: per style the attempts, valid designs, the selected design and
  why it was chosen, its site EUI against the starting design, what could not
  be done, and the time and token use of the run.
