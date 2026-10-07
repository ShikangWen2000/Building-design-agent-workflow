# Examples

The selected candidate of each step of the representative Hong Kong run (Claude Opus 5.5 in Claude Code), as
submitted by the agent. Models are not included.

| Folder | Files |
|---|---|
| `step1_massing/c020/` | `design_spec.json` (description, design intent, and massing parameters), `geometry.json` (footprints and tiers produced by the generator), `massing_3d.png` |
| `step2_envelope/e10/` | `design_spec.json`, `envelope_script.rb` (OpenStudio script written by the agent, which places windows and shading on the selected massing), `envelope_script.params.json`, `envelope_axon.png` |
| `step3_hvac/h10/` | `design_spec.json` (description, design intent, air state analysis, and engineering basis), `tool_plan.json` (the HVAC tool calls), `hvac_script/` (OpenStudio script written by the agent) |

Case identifiers are those of the run: the selected Step 1 design is iteration 20, the selected Step 2 design is
iteration 11, and the selected Step 3 design is iteration 11 in the paper, which also counts invalid proposals.
Two values of the baseline model that the agent quoted in the Step 3 files (a fan pressure and a pump head) were
removed from the text, and the name of one baseline schedule was replaced by a description; nothing else was
changed.

The files are named by their content. Inside the specifications, the scripts keep the names of the run:
`e10.rb` is `envelope_script.rb`, and the Step 3 script is in `hvac_script/`. The examples are records of the
submissions and are not meant to be run as they are.
