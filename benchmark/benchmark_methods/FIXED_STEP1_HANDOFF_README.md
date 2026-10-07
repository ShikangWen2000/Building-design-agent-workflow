# Fixed Step 1 handoff for Step 2 benchmarks

Use one completed, validated Step 1 selection as the input to every method in
a benchmark comparison. Record its climate context, weather hash, source
revision and handoff hashes in the experiment manifest.

The handoff includes:

- `step1_massing/organized_current/best_massing.json`;
- the selected candidate's geometry and constraint report;
- its fixed-envelope OSM and simulation result;
- the Step 1 energy comparison table and evidence hashes.

Pass the run's massing directory as `--step1-root`:

```powershell
--step1-root E:\LLM_Output\<fixed_run>\step1_massing
```

Each method copies the handoff into its own workflow output. Keep the source
handoff fixed during the comparison. Run Step 2's `--verify-step1-handoff`
control against the selected Step 1 result before interpreting envelope savings.
Record the reproduction report with the method's outputs.
