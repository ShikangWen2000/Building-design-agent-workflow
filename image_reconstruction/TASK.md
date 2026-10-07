# Task: reconstruct design images as simulation-ready building models

You are the modelling agent. Each of five images shows a design for the Block M
office building in Hong Kong. Reconstruct each image as an OpenStudio model of
the building: its massing, its windows and its exterior shading, valid under
the rules of the energy design's massing and envelope steps. You make the
modelling decisions and you build each model yourself. The validator in this
repository checks each model against the rules and simulates it.

Read `requirements/image_brief.md` (the brief, the cases and how they are
judged) and `requirements/reconstruction_requirements.md` (every rule the
validator applies) before modelling.

## What you are given

| Path | Content |
|---|---|
| `inputs/images/case_*.png` | the five design images |
| `inputs/hong_kong/baseline.osm` | the existing building (OpenStudio 3.5.1); reference for every fixed item |
| `inputs/hong_kong/weather_2021_measured.epw` | weather file of the simulations |
| `requirements/image_brief.md` | the design brief, the cases and the judging |
| `requirements/reconstruction_requirements.md` | the rules |
| `config/task_config.json` | site, building and limit values, cases, attempt budget |
| `validator/` | the validation layer; use it through `validator/submit.py` |

Nothing else is provided: there is no geometry, window, shading or image
analysis tool for you. How you read the images and produce the models is your
decision (OpenStudio SDK or CLI, your own scripts, public libraries and
documentation).

## Procedure

Run once at the start (it simulates the existing building):

```sh
python validator/submit.py reference --run-root <run>
```

For each case, in any order:

1. Look at the image. Write down what you read from it (number of volumes,
   footprints and their changes over the height, setbacks, terraces, rotation,
   window pattern, shading) and how you will model it within the rules.
2. Build the model and its `massing.json`, and submit:

   ```sh
   python validator/submit.py submit --run-root <run> --case case_008 --osm <model.osm> --massing <massing.json> --note "<reading and intent>"
   ```

   The command prints the verdict and writes `feedback.json` (failed checks
   with their data, energy by end use, orientation WWR, floor area, EnergyPlus
   messages) and `render.png` (an axonometric preview made from the model)
   under `<run>/<case>/attempt_<nnn>/`. Use `--repair-of <attempt id>` when a
   submission repairs an earlier one.
3. Compare the render with the image and read the feedback, then decide the
   next attempt.
4. Stop the case when it has reached its target number of valid designs or
   used its attempts, then close it with the valid design that reproduces the
   image best:

   ```sh
   python validator/submit.py select --run-root <run> --case case_008 --attempt <attempt id> --reason "<why this one>"
   ```

`python validator/submit.py status --run-root <run>` shows the counts.

## Budget

| Case | Valid designs wanted | Attempts allowed |
|---|---|---|
| each of the five | 2 | 6 |

Every `submit` is one attempt: valid designs, rejected designs, repeats,
repairs and failed simulations all count. The validator stops accepting
submissions for a case when the target or the attempt limit is reached. Do not
relax a rule to reach the target; report what was not reached.

## Rules of conduct

- Do not modify `validator/`, `config/`, `requirements/` or `inputs/`. The
  validator records a hash of these files with every attempt.
- Use the validator only through `submit.py`. Its checks and its simulation
  procedure are the acceptance test, not a modelling tool.
- Use only this repository, the software you install and public
  documentation. Do not use other repositories, other project folders on the
  machine, or results of earlier runs of this or a similar task.
- Model one attempt at a time from the evidence of this run.
- Keep every attempt, including failed ones.

## What to keep in the run root

The validator writes the ledger (`ledger.jsonl`), the attempts, the renders
and the selections. In addition keep, in the run root:

- `modelling_log.md`: for every attempt what you read from the image, how the
  model was built, which features of the image could not be modelled within
  the rules and why, and what the feedback and the render showed;
- the scripts you wrote to build the models;
- `summary.md`: per case the attempts, valid designs, the selected design and
  why it was chosen, the features reproduced and not reproduced, its site EUI
  against the existing building, and the time and token use of the run.
