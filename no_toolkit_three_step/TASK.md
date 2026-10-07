# Task: three-step energy design of the Block M office building

You are the design agent. Starting from the baseline building model, design a
building with the lowest site energy use intensity, in three steps: massing,
envelope, HVAC. You make the design decisions and you build each design as an
OpenStudio model yourself. The validator in this repository checks each model
against the rules and simulates it.

Read `requirements/design_requirements.md` before designing. It holds every
rule the validator applies.

## What you are given

| Path | Content |
|---|---|
| `inputs/hong_kong/baseline.osm` | baseline model (OpenStudio 3.5.1) of the existing building |
| `inputs/hong_kong/weather_2021_measured.epw` | weather file of the simulations |
| `requirements/design_requirements.md` | the rules of the three steps |
| `requirements/equipment_performance.json` | reference equipment performance for Step 3 |
| `config/task_config.json` | site, building and limit values, attempt budgets |
| `validator/` | the validation layer; use it through `validator/submit.py` |

Nothing else is provided: there is no geometry generator, no window or shading
generator and no HVAC builder. How you produce the models is your decision
(OpenStudio SDK or CLI, EnergyPlus, your own scripts, public libraries and
documentation).

## Procedure

For each step, in order 1, 2, 3:

1. Design one case at a time. Write down the hypothesis of the case, build the
   model, and submit it:

   ```sh
   python validator/submit.py submit --run-root <run> --step 1 --osm <model.osm> --massing <massing.json> --note "<hypothesis>"
   python validator/submit.py submit --run-root <run> --step 2 --osm <model.osm> --note "<hypothesis>"
   python validator/submit.py submit --run-root <run> --step 3 --osm <model.osm> --note "<hypothesis>" [--declaration <changes.json>]
   ```

   In Step 3, `--declaration` names a JSON file that declares the allowed
   schedule changes and their reasons (`requirements/design_requirements.md`,
   section 6).

   The command prints the verdict and writes `feedback.json` (failed checks
   with their data, energy by end use, comfort, ventilation, EnergyPlus
   messages) under `<run>/step<N>/attempt_<nnn>/`. Use `--repair-of <attempt
   id>` when a submission repairs an earlier one.
2. Read the feedback of that case, then decide the next case.
3. Stop the step when it has reached its target number of valid designs or
   used its attempts, then close it:

   ```sh
   python validator/submit.py select --run-root <run> --step 1
   ```

   `select` fixes the model the next step starts from: the valid design with
   the lowest site EUI, written to `<run>/step<N>/selected.osm`. If a step has
   no valid design, the model the step started from is carried forward and
   this is recorded.

`python validator/submit.py status --run-root <run>` shows the counts. The
first command on a new run root simulates the baseline model once; its result
is the reference for the comparison and for the comfort check.

## Budget

| Step | Valid designs wanted | Attempts allowed |
|---|---|---|
| 1 Massing | 20 | 60 |
| 2 Envelope | 10 | 30 |
| 3 HVAC | 10 | 30 |

Every `submit` is one attempt: valid designs, rejected designs, repeats of an
earlier design, repairs and failed simulations all count. The validator stops
accepting submissions for a step when the target or the attempt limit is
reached. Do not relax a rule to reach the target; report what was not reached.

## Rules of conduct

- Do not modify `validator/`, `config/`, `requirements/` or `inputs/`. The
  validator records a hash of these files with every attempt.
- Use the validator only through `submit.py`. Its checks and its simulation
  procedure are the acceptance test, not a design tool.
- Use only this repository, the software you install, and public
  documentation. Do not use other repositories, other project folders on the
  machine, or results of earlier runs of this or a similar task.
- Design one case at a time from the evidence of this run. Do not generate a
  fixed batch of cases in advance.
- Keep every attempt, including failed ones.
- You may run your own simulations while preparing a case; only validator
  results count.

## What to keep in the run root

The validator writes the ledger (`ledger.jsonl`), the attempts and the
selections. In addition keep, in the run root:

- `design_log.md`: for every attempt the hypothesis, what was changed, how the
  model was built, and what the feedback showed;
- the scripts you wrote to build the models;
- `summary.md`: per step the attempts, valid designs and best site EUI against
  the baseline, the selected designs, what could not be done, and the time
  and token use of the run.
