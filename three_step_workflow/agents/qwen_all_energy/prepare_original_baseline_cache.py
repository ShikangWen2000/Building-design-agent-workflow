from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the occupied-PMV cache for the unchanged original baseline."
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--project-context")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    output_root = Path(args.output_root).resolve()
    os.environ["AUTOMATED_DESIGN_OUTPUT_ROOT"] = str(output_root)
    if args.project_context:
        os.environ["AUTOMATED_DESIGN_PROJECT_CONTEXT"] = str(Path(args.project_context).resolve())

    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "steps" / "step3_hvac"))

    # Import only after installing the run-specific environment. The stage module
    # resolves its climate, weather file, baseline and output paths at import time.
    import stage3_hvac_energyplus as stage3  # noqa: PLC0415
    from hvac_toolkit.runner import run_simulation  # noqa: PLC0415

    cache_path = (
        stage3.STEP3
        / "original_baseline"
        / f"{stage3.CLIMATE_ID}_original_zone_conditions.json"
    )
    existing = stage3.load_original_zone_conditions()
    if existing is not None:
        print(
            json.dumps(
                {
                    "status": "cache_ready",
                    "cache_path": str(cache_path),
                    "pmv_occupied_sample_count": existing.get("pmv_occupied_sample_count"),
                    "pmv_occupied_comfort_pct": existing.get("pmv_occupied_comfort_pct"),
                },
                indent=2,
            )
        )
        return 0

    # stage3.load_original_zone_conditions() simulates the baseline itself when
    # the cache is absent and returns None only if that run failed. Retry once
    # here through the same low-level runner so the failure is reported with
    # its result instead of surfacing later inside the first Step 3 case.
    prepared_osm = stage3.prepare_original_baseline_osm()
    result = run_simulation(
        stage3.ORIGINAL_BASELINE_CASE_ID,
        prepared_osm,
        stage3.OUTPUT_ROOT,
        calibrated_baseline=True,
    )
    if result.get("status") != "completed":
        print(json.dumps(result, indent=2))
        return 2

    cache_signature = stage3._baseline_cache_signature()
    run_dir = Path(str(result["run_dir"]))
    conditions = {key: result.get(key) for key in result if key.startswith("zone_")}
    conditions.update({key: result.get(key) for key in result if key.startswith("pmv_")})
    conditions.update(stage3.parse_ventilation_hours_runtime_zone_conditions(run_dir))
    conditions.update(stage3.parse_pmv_comfort(run_dir, prepared_osm))
    conditions.update(
        {
            "baseline_osm": str(stage3.ORIGINAL_BASELINE_OSM),
            "prepared_osm": str(prepared_osm),
            "run_dir": str(run_dir),
            "status": result.get("status"),
            "climate_id": stage3.CLIMATE_ID,
            "weather_file": stage3.ACTIVE_CONFIG.get("weather_file"),
            "heating_periods": stage3.CLIMATE_PROFILE.get("heating_periods", []),
            "reference_pack": stage3.CLIMATE_PROFILE.get("reference_pack"),
            "cache_signature": cache_signature,
        }
    )
    try:
        conditions["baseline_pressure_reference"] = stage3.load_original_baseline_pressure_reference()
    except Exception as exc:  # The PMV baseline remains usable without this audit addendum.
        conditions["baseline_pressure_reference_error"] = f"{type(exc).__name__}: {exc}"

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path = (
        stage3.STEP3 / "original_baseline" / f"{stage3.CLIMATE_ID}_baseline_profile.json"
    )
    cache_path.write_text(json.dumps(conditions, indent=2), encoding="utf-8")
    profile_path.write_text(json.dumps(conditions, indent=2), encoding="utf-8")
    (run_dir / "baseline_signature.json").write_text(
        json.dumps(cache_signature), encoding="utf-8"
    )

    valid = (
        int(conditions.get("pmv_occupied_sample_count") or 0) > 0
        and conditions.get("pmv_occupied_comfort_pct") is not None
    )
    stage3.cleanup_large_simulation_outputs(run_dir)
    print(
        json.dumps(
            {
                "status": "cache_ready" if valid else "cache_missing_pmv",
                "cache_path": str(cache_path),
                "pmv_occupied_sample_count": conditions.get("pmv_occupied_sample_count"),
                "pmv_occupied_comfort_pct": conditions.get("pmv_occupied_comfort_pct"),
            },
            indent=2,
        )
    )
    return 0 if valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
