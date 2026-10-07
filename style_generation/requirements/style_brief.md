# Style brief

Three redesigns of the same building, done one after the other. Each brief is
the instruction for that style; the reference image is the visual precedent.
Open the reference before designing (the images are not stored in this
repository; use the links).

The first style starts from the start model (`inputs/start/step2_selected_e10.osm`).
Each later style starts from the design selected for the style before it. In
the briefs, `<EUI>` is the site EUI of that starting design, as reported by the
validator (`style_starting_site_eui_kwh_m2` in the feedback, or `status`).

## 1. `zaha_hadid`

> Using the current best design in Step 2 as the baseline, redesign the
> building in a Zaha Hadid-inspired style.

Reference: Jockey Club Innovation Tower, The Hong Kong Polytechnic University
(Zaha Hadid Architects), photograph by S. Wallroth, Wikimedia Commons:
<https://commons.wikimedia.org/wiki/File:Wikimania_2013_04404.JPG>

## 2. `frank_gehry`

> With the current design consuming `<EUI>` kWh/m2, I aim to redesign the
> building in the style of Frank Gehry.

Reference: Walt Disney Concert Hall, Los Angeles (Frank Gehry), Wikipedia:
<https://en.wikipedia.org/wiki/Walt_Disney_Concert_Hall>

## 3. `jean_nouvel`

> With the current design consuming `<EUI>` kWh/m2, I aim to redesign the
> building in the style of Jean Nouvel.

Reference: La Tour Horizons, Boulogne-Billancourt (Jean Nouvel), Openverse:
<https://openverse.org/image/fde034d1-199b-4f3d-8338-ef86aaadb92e>

## What a styled design is judged on

- Validity: every rule of `requirements/style_requirements.md`, checked by the
  validator.
- Style: how recognisably the massing, the window pattern and the shading
  carry the architect's manner, within those rules. Explain this for each
  design in its note and in the design log, with the features you took from
  the reference.
- Energy: the site EUI against the design the style started from, reported
  for every valid design.
