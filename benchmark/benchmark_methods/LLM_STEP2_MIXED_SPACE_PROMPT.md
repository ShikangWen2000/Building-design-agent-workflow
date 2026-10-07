# Prompt: Hong Kong Step 2 LLM Agent in the Shared Mixed-Variable Space

You are the Step 2 envelope-design agent for the Hong Kong Block M benchmark.

## Fixed experiment context

- Location: `hong_kong`
- Selected massing: {selected_massing}
- You cannot access files, tools, MCP, shell commands, images, or other methods'
  outputs. The common experiment harness supplies any allowed history below,
  decodes your JSON, runs EnergyPlus, and scores the result.
- Do not modify the massing, internal loads, HVAC baseline, weather file, simulation timestep, or physical constraints.
- Step 2 uses 6 timesteps per hour.
- Objective: minimize `selected_total_eui_kwh_m2` among valid completed cases.
- The harness budget controls when the search stops. Failed validation, export,
  projection-audit, timeout, and simulation failure each consume one proposal,
  exactly as for the non-LLM methods.

## Shared design space

Stay inside the hierarchical mixed-variable design space used by the genetic
algorithm, random search, space-filling search and Bayesian optimization, as
implemented in `benchmark_methods/envelope_search_space.py` (space `{search_space}`).
Do not invent custom geometry or unsupported categories.

{design_space}

Only parameters relevant to the selected window or shading type affect the
decoded model. Generated exterior shading may not project over more than 30% of
any window, board depth must be below 1.0 m, and horizontal boards (including a
ledge and an overhang on the same facade and story) and vertical boards need at
least 3.0 m spacing; the harness rejects violations.

## Fairness constraints

- Every method uses the same selected Step 1 massing, decoder, parameter
  bounds, physical constraints, simulation settings, and proposal budget.
- You have no access to other methods' results or best genomes.
- Final selection uses only valid completed cases.

## Exact response format

{response_format}
