"""Validation and normalization for HVAC tool-call plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import sys
from typing import Any

try:
    from .registry import TOOL_SPECS
except ImportError:  # Allow direct execution from this folder.
    from registry import TOOL_SPECS

SHARED_DIR = Path(__file__).resolve().parents[1] / "steps" / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))
from minimal_io import validate_identifier
try:
    from physical_constraints import load_original_baseline_pressure_reference
    from project_context import active_chiller_reference_conditions
except Exception:
    load_original_baseline_pressure_reference = None
    active_chiller_reference_conditions = None


def _active_baseline_reference() -> dict[str, float]:
    if load_original_baseline_pressure_reference is None:
        return {}
    try:
        return load_original_baseline_pressure_reference()
    except Exception:
        return {}


def _baseline_float(baseline: dict[str, Any], key: str, errors: list[str], *, required: bool = True) -> float | None:
    value = baseline.get(key)
    if value is None:
        if required:
            errors.append(f"Active baseline pressure reference missing `{key}`.")
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"Active baseline pressure reference `{key}` is not numeric.")
        return None

COP_SANITY_MIN = 0.1
COP_SANITY_MAX = 20.0

DOAS_COOLING_SAT_DEFAULT_C = 12.0
DOAS_COOLING_SAT_MIN_C = 10.0
DOAS_COOLING_SAT_MAX_C = 16.0

ECONOMIZER_TYPES = {
    "NoEconomizer",
    "FixedDryBulb",
    "DifferentialDryBulb",
    "FixedEnthalpy",
    "DifferentialEnthalpy",
}

TERMINAL_TYPES = {
    "fcu_retained",
    "doas_fcu",
    "doas_chilled_beam_conceptual",
    "doas_radiant_ceiling_panel_conceptual",
    "radiant_ceiling_panel_conceptual",
}

STANDARDS_TEMPLATES = {"90.1-2019", "90.1-2016", "90.1-2013", "90.1-2010"}

# Enum extracted from openstudio-standards 0.3.0 source, with the refrigerant
# split-terminal option omitted because the Step 3 catalog excludes that family.
# (lib/openstudio-standards/prototypes/common/objects/Prototype.hvac_systems.rb,
# def model_add_hvac_system, case system_type ...). Keep in sync with the
# installed gem version by re-running ruby/probe_standards_systems2.rb.
STANDARDS_SYSTEM_TYPES = {
    "PTAC",
    "PTHP",
    "PSZ-AC",
    "PSZ-HP",
    "PSZ-VAV",
    "Fan Coil",
    "Radiant Slab",
    "Baseboards",
    "Unit Heaters",
    "High Temp Radiant",
    "Window AC",
    "Residential AC",
    "Forced Air Furnace",
    "Residential Forced Air Furnace",
    "Residential Forced Air Furnace with AC",
    "Residential Air Source Heat Pump",
    "Residential Minisplit Heat Pumps",
    "VAV Reheat",
    "VAV No Reheat",
    "VAV Gas Reheat",
    "PVAV Reheat",
    "PVAV PFP Boxes",
    "VAV PFP Boxes",
    "Water Source Heat Pumps",
    "Ground Source Heat Pumps",
    "DOAS Cold Supply",
    "DOAS",
    "DOAS with DCV",
    "DOAS with Economizing",
    "ERVs",
    "Evaporative Cooler",
    "Ideal Air Loads",
}

STANDARDS_HEAT_FUELS = {"NaturalGas", "Electricity", "DistrictHeating", "AirSourceHeatPump", "ASHP"}
STANDARDS_COOL_FUELS = {"Electricity", "DistrictCooling"}

STANDARDS_HOT_WATER_LOOP_TYPES = {"HighTemperature", "LowTemperature", "DistrictAmbient"}
STANDARDS_CHW_LOOP_COOLING_TYPES = {"AirCooled", "WaterCooled"}
STANDARDS_HEAT_PUMP_LOOP_COOLING_TYPES = {"EvaporativeFluidCooler", "CoolingTower", "DryCooler"}
STANDARDS_AIR_LOOP_HEATING_TYPES = {"Water", "DX", "Electric", "Gas"}
STANDARDS_AIR_LOOP_COOLING_TYPES = {"Water", "DX"}
STANDARDS_FAN_COIL_CAPACITY_CONTROL_METHODS = {
    "CyclingFan",
    "ConstantFanVariableFlow",
    "VariableFanVariableFlow",
    "VariableFanConstantFlow",
}

RADIANT_PANEL_TYPES = {"radiant_ceiling_panel"}


def _water_cooled_reference_condenser_c() -> float:
    if active_chiller_reference_conditions is None:
        return 32.0
    try:
        return active_chiller_reference_conditions()["water_cooled_entering_condenser_c"]
    except Exception:
        return 32.0

COOLING_SOURCES = {
    "air_cooled_chiller",
    "water_cooled_chiller",
    "air_source_heat_pump",
    "water_source_heat_pump",
}

HEATING_SOURCES = {
    "none",
    "boiler",
    "air_source_heat_pump_heating",
    "water_source_heat_pump_heating",
    "air_source_heat_pump_heating_cooling",
    "water_source_heat_pump_heating_cooling",
}


@dataclass
class ToolPlanValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _num(args: dict[str, Any], key: str, errors: list[str], *, lo: float | None = None, hi: float | None = None) -> float | None:
    value = args.get(key)
    if not _is_number(value):
        errors.append(f"'{key}' must be numeric")
        return None
    out = float(value)
    if lo is not None and out < lo:
        errors.append(f"'{key}' {out} below {lo}")
    if hi is not None and out > hi:
        errors.append(f"'{key}' {out} above {hi}")
    return out


def _cop_num(args: dict[str, Any], key: str, errors: list[str]) -> float | None:
    """Reject only nonnumeric or physically impossible COP values.

    Step 3 does not expose COP as a search range. Concrete source/basis checks
    are handled in the physical-constraints layer.
    """
    return _num(args, key, errors, lo=COP_SANITY_MIN, hi=COP_SANITY_MAX)


def _radiant_panel_flux_w_per_m2(panel_temp_c: float, air_temp_c: float, *, heating: bool) -> float:
    delta_t = panel_temp_c - air_temp_c
    coefficient, exponent = (0.87, 0.25) if heating else (2.13, 0.31)
    convective = coefficient * abs(delta_t) ** exponent * delta_t
    radiative = 5.0e-8 * ((panel_temp_c + 273.15) ** 4 - (air_temp_c + 273.15) ** 4)
    return convective + radiative


def validate_tool_plan(plan: dict[str, Any]) -> ToolPlanValidation:
    errors: list[str] = []
    warnings: list[str] = []
    baseline = _active_baseline_reference()
    baseline_terminal_fan_pressure_pa = _baseline_float(baseline, "fan_pressure_pa", errors, required=False)
    baseline_fan_pressure_pa = baseline_terminal_fan_pressure_pa
    baseline_doas_relief_fan_pressure_pa = _baseline_float(baseline, "doas_relief_fan_pressure_pa", errors)
    baseline_doas_supply_fan_pressure_pa = _baseline_float(baseline, "doas_supply_fan_pressure_pa", errors)
    baseline_pump_head_pa = _baseline_float(baseline, "pump_head_pa", errors)

    if not isinstance(plan, dict):
        return ToolPlanValidation(False, ["HVAC tool plan must be a JSON object"], [])

    if not isinstance(plan.get("case_id"), str) or not plan["case_id"]:
        errors.append("'case_id' must be a non-empty string")
    else:
        try:
            validate_identifier(plan["case_id"], "case_id")
        except ValueError as exc:
            errors.append(str(exc))

    calls = plan.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        errors.append("'tool_calls' must be a non-empty list")
        return ToolPlanValidation(False, errors, warnings)

    seen_water_cooled = False
    tool_names = {c.get("tool") for c in calls if isinstance(c, dict)}
    for i, call in enumerate(calls):
        if not isinstance(call, dict):
            errors.append(f"tool_calls[{i}] must be an object")
            continue
        name = call.get("tool")
        args = call.get("args", {})
        if name not in TOOL_SPECS:
            errors.append(f"tool_calls[{i}].tool {name!r} is not registered")
            continue
        if not isinstance(args, dict):
            errors.append(f"tool_calls[{i}].args must be an object")
            continue
        spec = TOOL_SPECS[name]
        unknown = set(args) - set(spec.required) - set(spec.optional)
        if unknown:
            errors.append(f"tool_calls[{i}] {name} has unsupported arguments: {sorted(unknown)}")
        if spec.implementation == "record_only":
            errors.append(f"{name} only records prose and is not an executable HVAC change")
        for unsupported in ("high_humidity_lockout", "max_outdoor_air_fraction", "cooling_tower_fan_power_ratio"):
            if unsupported in args:
                errors.append(f"{name}: {unsupported} is not implemented in native objects; omit it rather than silently ignoring it")
        for key in spec.required:
            if key not in args:
                errors.append(f"{name} missing required arg '{key}'")
        if "target_loops" in args and (
            not isinstance(args["target_loops"], list)
            or not args["target_loops"]
            or not all(isinstance(loop, str) and loop.strip() for loop in args["target_loops"])
        ):
            errors.append("target_loops must be a non-empty list of plant-loop names")
        if "terminal_cooling_capacity_kw" in args:
            _num(args, "terminal_cooling_capacity_kw", errors, lo=10.0, hi=20000.0)
            basis = args.get("capacity_normalization_basis")
            if not isinstance(basis, str) or not basis.strip():
                errors.append(
                    "terminal_cooling_capacity_kw requires a non-empty capacity_normalization_basis"
                )

        if name == "apply_air_cooled_chiller":
            _cop_num(args, "chiller_cop", errors)
            _validate_optional_chw(args, errors)

        elif name == "apply_water_cooled_chiller_system":
            seen_water_cooled = True
            _cop_num(args, "chiller_cop", errors)
            reference_c = _water_cooled_reference_condenser_c()
            _num(args, "condenser_water_supply_c", errors, lo=reference_c, hi=reference_c)
            if "condenser_pump_head_pa" in args:
                _num(args, "condenser_pump_head_pa", errors, lo=100000.0, hi=350000.0)
            _validate_optional_chw(args, errors)

        elif name == "configure_chw_loop":
            _validate_chw(args, errors)

        elif name == "configure_supply_fans":
            _num(args, "fan_efficiency", errors, lo=0.10, hi=0.65)
            _num(args, "fan_motor_efficiency", errors, lo=0.20, hi=0.97)
            fan_pa = _num(args, "fan_pressure_pa", errors)
            if fan_pa is not None and baseline_fan_pressure_pa is not None and fan_pa < baseline_fan_pressure_pa - 1e-6 and not args.get("engineering_justification"):
                errors.append("fan_pressure_pa below baseline requires engineering_justification")
            doas_relief_pa = _num(args, "doas_relief_fan_pressure_pa", errors) if "doas_relief_fan_pressure_pa" in args else None
            if doas_relief_pa is not None and baseline_doas_relief_fan_pressure_pa is not None and doas_relief_pa < baseline_doas_relief_fan_pressure_pa - 1e-6 and not args.get("engineering_justification"):
                errors.append("doas_relief_fan_pressure_pa below original DOAS baseline requires engineering_justification")
            doas_supply_pa = _num(args, "doas_supply_fan_pressure_pa", errors) if "doas_supply_fan_pressure_pa" in args else None
            if doas_supply_pa is not None and baseline_doas_supply_fan_pressure_pa is not None and doas_supply_pa < baseline_doas_supply_fan_pressure_pa - 1e-6 and not args.get("engineering_justification"):
                errors.append("doas_supply_fan_pressure_pa below original DOAS baseline requires engineering_justification")

        elif name == "configure_chw_pumps":
            pump_pa = _num(args, "pump_head_pa", errors)
            _num(args, "pump_motor_efficiency", errors, lo=0.20, hi=0.97)
            if pump_pa is not None and baseline_pump_head_pa is not None and pump_pa < baseline_pump_head_pa - 1e-6 and not args.get("engineering_justification"):
                errors.append("pump_head_pa below baseline requires engineering_justification")

        elif name == "configure_economizer":
            econ = args.get("economizer_type")
            if econ not in ECONOMIZER_TYPES:
                errors.append(f"economizer_type must be one of {sorted(ECONOMIZER_TYPES)}")
            if econ not in {"NoEconomizer", "FixedEnthalpy", "DifferentialEnthalpy"}:
                warnings.append("dry-bulb economizers should be justified against the active project climate and humidity conditions")
            if "max_outdoor_air_fraction" in args:
                _num(args, "max_outdoor_air_fraction", errors, lo=0.0, hi=1.0)

        elif name == "add_heat_recovery_ventilation":
            _num(args, "sensible_effectiveness", errors, lo=0.0, hi=0.80)
            _num(args, "latent_effectiveness", errors, lo=0.0, hi=0.80)

        elif name == "configure_radiant_ceiling_panel":
            panel_type = args.get("panel_type")
            if panel_type not in RADIANT_PANEL_TYPES:
                errors.append(f"panel_type must be one of {sorted(RADIANT_PANEL_TYPES)}")
            _num(args, "cooling_design_capacity_w_per_m2", errors, lo=20.0, hi=120.0)
            if "cooling_control_temperature_c" in args:
                _num(args, "cooling_control_temperature_c", errors, lo=22.0, hi=27.0)
            _num(args, "dewpoint_safety_offset_c", errors, lo=0.0, hi=4.0)
            if args.get("requires_doas") is not True:
                errors.append("radiant ceiling cooling requires_doas=true for ventilation and latent-load control")
            condensation = args.get("condensation_control")
            if not isinstance(condensation, str) or not condensation:
                errors.append("radiant ceiling panel requires non-empty condensation_control")

        elif name == "add_radiant_ceiling_metal_panels":
            if args.get("panel_scope") not in {"all_ceilings", "roof_only"}:
                errors.append("panel_scope must be all_ceilings or roof_only")

        elif name == "configure_terminal_strategy":
            terminal = args.get("terminal_type")
            if terminal not in TERMINAL_TYPES:
                errors.append(f"terminal_type must be one of {sorted(TERMINAL_TYPES)}")
            if terminal == "doas_chilled_beam_conceptual" and "construct_chilled_beam_terminals" not in tool_names:
                warnings.append("chilled beam is recorded as strategy intent unless construct_chilled_beam_terminals is also present")
            if (
                terminal in {"doas_radiant_ceiling_panel_conceptual", "radiant_ceiling_panel_conceptual"}
                and "configure_radiant_ceiling_panel" not in tool_names
            ):
                warnings.append("radiant ceiling panel terminal is conceptual unless configure_radiant_ceiling_panel is also present")

        elif name == "construct_doas_fcu_chiller_boiler":
            topology = args.get("fcu_chw_topology", "shared")
            if topology not in {"shared", "split"}:
                errors.append("fcu_chw_topology must be shared or split")
            _validate_doas_economizer(args, errors)
            if name == "construct_doas_fcu_chiller_boiler" and topology == "shared":
                for key in ("terminal_cooling_capacity_kw", "terminal_chiller_cop", "terminal_chiller_reference_leaving_c"):
                    if key in args:
                        errors.append(f"{key} requires fcu_chw_topology=split")
                for key in ("high_lift_source", "low_lift_source"):
                    if key in args and args[key] != args.get("cooling_source", "water_cooled_chiller"):
                        errors.append(f"{key} conflicts with shared FCU cooling_source")
                for key, expected in (("terminal_chw_supply_c", args.get("chw_supply_c", 7.0)), ("terminal_chw_delta_t_c", args.get("chw_delta_t_c", 5.0))):
                    if key in args and args[key] != expected:
                        errors.append(f"{key} conflicts with shared FCU water loop")
            if "cooling_sizing_factor" in args:
                _num(args, "cooling_sizing_factor", errors, lo=1.0, hi=1.30)
            if "availability_prestart_hours" in args:
                _num(args, "availability_prestart_hours", errors, lo=0.0, hi=4.0)
            cooling_source = args.get("cooling_source")
            if cooling_source is not None and cooling_source not in COOLING_SOURCES:
                errors.append(f"cooling_source must be one of {sorted(COOLING_SOURCES)}")
            for source_key in ("high_lift_source", "low_lift_source"):
                source = args.get(source_key)
                if source is not None and source not in COOLING_SOURCES:
                    errors.append(f"{source_key} must be one of {sorted(COOLING_SOURCES)}")
            heating_source = args.get("heating_source")
            if heating_source is not None and heating_source not in HEATING_SOURCES:
                errors.append(f"heating_source must be one of {sorted(HEATING_SOURCES)}")
            if "chiller_cop" in args:
                _cop_num(args, "chiller_cop", errors)
            if "terminal_chiller_cop" in args:
                _cop_num(args, "terminal_chiller_cop", errors)
            if "ashp_cooling_cop" in args:
                _cop_num(args, "ashp_cooling_cop", errors)
            if "ashp_heating_cop" in args:
                _cop_num(args, "ashp_heating_cop", errors)
            if "wshp_cooling_cop" in args:
                _cop_num(args, "wshp_cooling_cop", errors)
            if "wshp_heating_cop" in args:
                _cop_num(args, "wshp_heating_cop", errors)
            if "wshp_source_supply_c" in args:
                _num(args, "wshp_source_supply_c", errors, lo=20.0, hi=35.0)
            if "wshp_source_delta_t_c" in args:
                _num(args, "wshp_source_delta_t_c", errors, lo=3.0, hi=8.0)
            if "wshp_source_minimum_c" in args:
                _num(args, "wshp_source_minimum_c", errors, lo=10.0, hi=25.0)
            _validate_optional_chw(args, errors)
            if "terminal_chw_supply_c" in args:
                _num(args, "terminal_chw_supply_c", errors, lo=5.0, hi=16.0)
            if "terminal_chw_delta_t_c" in args:
                _num(args, "terminal_chw_delta_t_c", errors, lo=2.0, hi=8.0)
            if "terminal_chiller_reference_leaving_c" in args:
                _num(args, "terminal_chiller_reference_leaving_c", errors, lo=5.0, hi=16.0)
            if "condenser_water_supply_c" in args:
                reference_c = _water_cooled_reference_condenser_c()
                _num(args, "condenser_water_supply_c", errors, lo=reference_c, hi=reference_c)
            for key in ("chw_pump_head_pa", "hw_pump_head_pa", "condenser_pump_head_pa"):
                if key in args:
                    _num(args, key, errors, lo=100000.0, hi=350000.0)
            if "pump_motor_efficiency" in args:
                _num(args, "pump_motor_efficiency", errors, lo=0.20, hi=0.97)
            if "hw_supply_c" in args:
                _num(args, "hw_supply_c", errors, lo=35.0 if heating_source and heating_source.startswith("water_source_heat_pump") else 40.0, hi=80.0)
            if "hw_delta_t_c" in args:
                _num(args, "hw_delta_t_c", errors, lo=5.0, hi=20.0)
            if "boiler_efficiency" in args:
                _num(args, "boiler_efficiency", errors, lo=0.70, hi=0.98)
            if "fan_efficiency" in args:
                _num(args, "fan_efficiency", errors, lo=0.10, hi=0.65)
            if "fan_motor_efficiency" in args:
                _num(args, "fan_motor_efficiency", errors, lo=0.20, hi=0.97)
            fan_pa = _num(args, "fan_pressure_pa", errors) if "fan_pressure_pa" in args else None
            if fan_pa is not None and baseline_terminal_fan_pressure_pa is not None and fan_pa < baseline_terminal_fan_pressure_pa - 1e-6 and not args.get("engineering_justification"):
                errors.append("fan_pressure_pa below original terminal fan baseline requires engineering_justification")
            doas_relief_pa = _num(args, "doas_relief_fan_pressure_pa", errors) if "doas_relief_fan_pressure_pa" in args else None
            if doas_relief_pa is not None and baseline_doas_relief_fan_pressure_pa is not None and doas_relief_pa < baseline_doas_relief_fan_pressure_pa - 1e-6 and not args.get("engineering_justification"):
                errors.append("doas_relief_fan_pressure_pa below original DOAS relief baseline requires engineering_justification")
            doas_supply_pa = _num(args, "doas_supply_fan_pressure_pa", errors) if "doas_supply_fan_pressure_pa" in args else None
            if doas_supply_pa is not None and baseline_doas_supply_fan_pressure_pa is not None and doas_supply_pa < baseline_doas_supply_fan_pressure_pa - 1e-6 and not args.get("engineering_justification"):
                errors.append("doas_supply_fan_pressure_pa below original DOAS supply baseline requires engineering_justification")
            if "central_cooling_design_sat_c" in args:
                _num(args, "central_cooling_design_sat_c", errors, lo=12.0, hi=18.0)
            if "central_heating_design_sat_c" in args:
                _num(args, "central_heating_design_sat_c", errors, lo=18.0, hi=50.0)
            if "cooling_setpoint_c" in args:
                _num(args, "cooling_setpoint_c", errors, lo=22.0, hi=26.0)
            if "economizer_type" in args and args.get("economizer_type") not in ECONOMIZER_TYPES:
                errors.append(f"economizer_type must be one of {sorted(ECONOMIZER_TYPES)}")
            if "enable_dcv" in args and not isinstance(args.get("enable_dcv"), bool):
                errors.append("enable_dcv must be true or false")
            if "add_heat_recovery" in args and not isinstance(args.get("add_heat_recovery"), bool):
                errors.append("add_heat_recovery must be true or false")
            if "replace_existing_hvac" in args and not isinstance(args.get("replace_existing_hvac"), bool):
                errors.append("replace_existing_hvac must be true or false")
            if "hrv_sensible_effectiveness" in args:
                _num(args, "hrv_sensible_effectiveness", errors, lo=0.0, hi=0.80)
            if "hrv_latent_effectiveness" in args:
                _num(args, "hrv_latent_effectiveness", errors, lo=0.0, hi=0.80)
            if "doas_cooling_sat_c" in args:
                _num(args, "doas_cooling_sat_c", errors, lo=8.0, hi=18.0)
            if "fcu_cooling_sat_c" in args:
                _num(args, "fcu_cooling_sat_c", errors, lo=10.0, hi=18.0)
            for key in ("fcu_heating_sat_c",):
                if key in args:
                    _num(args, key, errors, lo=18.0, hi=50.0)

        elif name == "apply_standards_hvac_system":
            tpl = args.get("standard_template")
            if tpl not in STANDARDS_TEMPLATES:
                errors.append(f"standard_template must be one of {sorted(STANDARDS_TEMPLATES)}")
            sys_type = args.get("system_type")
            if sys_type not in STANDARDS_SYSTEM_TYPES:
                errors.append(
                    f"system_type {sys_type!r} is not in the openstudio-standards 0.3.0 enum "
                    f"({len(STANDARDS_SYSTEM_TYPES)} values; see hvac_toolkit/schema.py)"
                )
            if "main_heat_fuel" in args and args["main_heat_fuel"] is not None and args["main_heat_fuel"] not in STANDARDS_HEAT_FUELS:
                errors.append(f"main_heat_fuel must be one of {sorted(STANDARDS_HEAT_FUELS)} or null")
            if "zone_heat_fuel" in args and args["zone_heat_fuel"] is not None and args["zone_heat_fuel"] not in STANDARDS_HEAT_FUELS:
                errors.append(f"zone_heat_fuel must be one of {sorted(STANDARDS_HEAT_FUELS)} or null")
            if "cool_fuel" in args and args["cool_fuel"] is not None and args["cool_fuel"] not in STANDARDS_COOL_FUELS:
                errors.append(f"cool_fuel must be one of {sorted(STANDARDS_COOL_FUELS)} or null")
            if "hot_water_loop_type" in args and args["hot_water_loop_type"] not in STANDARDS_HOT_WATER_LOOP_TYPES:
                errors.append(f"hot_water_loop_type must be one of {sorted(STANDARDS_HOT_WATER_LOOP_TYPES)}")
            if "chilled_water_loop_cooling_type" in args and args["chilled_water_loop_cooling_type"] not in STANDARDS_CHW_LOOP_COOLING_TYPES:
                errors.append(f"chilled_water_loop_cooling_type must be one of {sorted(STANDARDS_CHW_LOOP_COOLING_TYPES)}")
            if "heat_pump_loop_cooling_type" in args and args["heat_pump_loop_cooling_type"] not in STANDARDS_HEAT_PUMP_LOOP_COOLING_TYPES:
                errors.append(f"heat_pump_loop_cooling_type must be one of {sorted(STANDARDS_HEAT_PUMP_LOOP_COOLING_TYPES)}")
            if "air_loop_heating_type" in args and args["air_loop_heating_type"] not in STANDARDS_AIR_LOOP_HEATING_TYPES:
                errors.append(f"air_loop_heating_type must be one of {sorted(STANDARDS_AIR_LOOP_HEATING_TYPES)}")
            if "air_loop_cooling_type" in args and args["air_loop_cooling_type"] not in STANDARDS_AIR_LOOP_COOLING_TYPES:
                errors.append(f"air_loop_cooling_type must be one of {sorted(STANDARDS_AIR_LOOP_COOLING_TYPES)}")
            if "fan_coil_capacity_control_method" in args and args["fan_coil_capacity_control_method"] not in STANDARDS_FAN_COIL_CAPACITY_CONTROL_METHODS:
                errors.append(
                    f"fan_coil_capacity_control_method must be one of "
                    f"{sorted(STANDARDS_FAN_COIL_CAPACITY_CONTROL_METHODS)}"
                )
            if "zone_equipment_ventilation" in args and not isinstance(args["zone_equipment_ventilation"], bool):
                errors.append("zone_equipment_ventilation must be true or false")
            if "replace_existing_hvac" in args and not isinstance(args["replace_existing_hvac"], bool):
                errors.append("replace_existing_hvac must be true or false")
            target_zones = args.get("target_zones")
            if target_zones is not None and (
                not isinstance(target_zones, list)
                or not all(isinstance(z, str) and z for z in target_zones)
            ):
                errors.append("target_zones must be null or a list of non-empty zone-name strings")

        elif name == "construct_doas_lowtemp_radiant":
            if "cooling_sizing_factor" in args:
                _num(args, "cooling_sizing_factor", errors, lo=1.0, hi=1.30)
            high_lift_source = args.get("high_lift_source")
            if high_lift_source is not None and high_lift_source not in COOLING_SOURCES:
                errors.append(f"high_lift_source must be one of {sorted(COOLING_SOURCES)}")
            low_lift_source = args.get("low_lift_source")
            if low_lift_source is not None and low_lift_source not in COOLING_SOURCES:
                errors.append(f"low_lift_source must be one of {sorted(COOLING_SOURCES)}")
            heating_source = args.get("heating_source")
            if heating_source is not None and heating_source not in HEATING_SOURCES:
                errors.append(f"heating_source must be one of {sorted(HEATING_SOURCES)}")
            capacity_method = args.get("cooling_capacity_method")
            if capacity_method is None:
                # Backward compatibility: an explicit W/m2 value means the old
                # fixed-capacity mode; otherwise transfer the design-day zone
                # load through EnergyPlus autosizing.
                capacity_method = "capacity_per_floor_area" if "cooling_capacity_w_per_m2" in args else "autosize"
            if capacity_method not in {"autosize", "capacity_per_floor_area"}:
                errors.append("cooling_capacity_method must be 'autosize' or 'capacity_per_floor_area'")
            if capacity_method == "capacity_per_floor_area":
                if "cooling_capacity_w_per_m2" not in args:
                    errors.append("cooling_capacity_method='capacity_per_floor_area' requires cooling_capacity_w_per_m2")
                else:
                    _num(args, "cooling_capacity_w_per_m2", errors, lo=20.0, hi=120.0)
            elif "cooling_capacity_w_per_m2" in args:
                errors.append("cooling_capacity_w_per_m2 cannot be combined with cooling_capacity_method='autosize'")
            if "cooling_control_temperature_c" in args:
                _num(args, "cooling_control_temperature_c", errors, lo=22.0, hi=27.0)
            if "heating_season_cooling_control_temperature_c" in args:
                _num(args, "heating_season_cooling_control_temperature_c", errors, lo=22.0, hi=27.0)
            _num(args, "dewpoint_safety_offset_c", errors, lo=0.0, hi=4.0)
            if args.get("requires_doas") is not True:
                errors.append("low-temp radiant cooling requires_doas=true for ventilation and latent-load control")
            condensation = args.get("condensation_control")
            if not isinstance(condensation, str) or not condensation:
                errors.append("low-temp radiant requires non-empty condensation_control")
            if "heating_capacity_w_per_m2" in args:
                _num(args, "heating_capacity_w_per_m2", errors, lo=20.0, hi=120.0)
            cooling_surface = None
            cooling_air = None
            heating_surface = None
            heating_air = None
            if "cooling_design_surface_temperature_c" in args:
                cooling_surface = _num(args, "cooling_design_surface_temperature_c", errors, lo=15.0, hi=24.0)
            if "cooling_design_air_temperature_c" in args:
                cooling_air = _num(args, "cooling_design_air_temperature_c", errors, lo=22.0, hi=30.0)
            if "heating_design_surface_temperature_c" in args:
                heating_surface = _num(args, "heating_design_surface_temperature_c", errors, lo=24.0, hi=35.0)
            if "heating_design_air_temperature_c" in args:
                heating_air = _num(args, "heating_design_air_temperature_c", errors, lo=15.0, hi=24.0)
            if cooling_surface is not None and cooling_air is not None:
                if cooling_surface >= cooling_air:
                    errors.append("cooling design surface temperature must be below cooling design air temperature")
                else:
                    expected = abs(_radiant_panel_flux_w_per_m2(cooling_surface, cooling_air, heating=False))
                    specified = args.get("cooling_capacity_w_per_m2") if capacity_method == "capacity_per_floor_area" else None
                    if _is_number(specified) and abs(float(specified) - expected) / expected > 0.05:
                        warnings.append(
                            f"cooling_capacity_w_per_m2={float(specified):.2f} differs from "
                            f"the radiant-panel equation result {expected:.2f} W/m2"
                        )
            if heating_surface is not None and heating_air is not None:
                if heating_surface <= heating_air:
                    errors.append("heating design surface temperature must exceed heating design air temperature")
                else:
                    expected = abs(_radiant_panel_flux_w_per_m2(heating_surface, heating_air, heating=True))
                    specified = args.get("heating_capacity_w_per_m2")
                    if _is_number(specified) and abs(float(specified) - expected) / expected > 0.05:
                        warnings.append(
                            f"heating_capacity_w_per_m2={float(specified):.2f} differs from "
                            f"the radiant-panel equation result {expected:.2f} W/m2"
                        )
            terminal_water = args.get("terminal_chw_supply_c")
            if _is_number(terminal_water) and cooling_surface is not None:
                if cooling_surface <= float(terminal_water):
                    errors.append("cooling design surface temperature must exceed terminal chilled-water supply temperature")
                elif abs((cooling_surface - float(terminal_water)) - 1.5) > 0.5:
                    warnings.append("cooling water-to-surface temperature rise differs from the validated 1.5 C basis")
            heating_water = args.get("hw_supply_c")
            if _is_number(heating_water) and heating_surface is not None:
                if heating_surface >= float(heating_water):
                    errors.append("heating design surface temperature must be below hot-water supply temperature")
                elif abs((float(heating_water) - heating_surface) - 5.0) > 1.0:
                    warnings.append("heating water-to-surface temperature drop differs from the validated 5 C basis")
            if "heating_control_temperature_c" in args:
                _num(args, "heating_control_temperature_c", errors, lo=15.0, hi=24.0)
            if "cooling_control_throttling_range_c" in args:
                _num(args, "cooling_control_throttling_range_c", errors, lo=0.5, hi=5.0)
            if "heating_control_throttling_range_c" in args:
                _num(args, "heating_control_throttling_range_c", errors, lo=0.5, hi=5.0)
            if "availability_prestart_hours" in args:
                _num(args, "availability_prestart_hours", errors, lo=0.0, hi=4.0)
            if "radiant_surface_type" in args and args["radiant_surface_type"] not in {
                "Ceilings",
                "Floors",
                "CeilingsAndFloors",
                "AllSurfaces",
            }:
                errors.append(
                    "radiant_surface_type must be one of Ceilings, Floors, CeilingsAndFloors, AllSurfaces"
                )
            if "temperature_control_type" in args and args["temperature_control_type"] not in {
                "MeanAirTemperature",
                "MeanRadiantTemperature",
                "OperativeTemperature",
                "OutdoorDryBulbTemperature",
                "OutdoorWetBulbTemperature",
            }:
                errors.append(
                    "temperature_control_type must be one of MeanAirTemperature, MeanRadiantTemperature, "
                    "OperativeTemperature, OutdoorDryBulbTemperature, OutdoorWetBulbTemperature"
                )
            if "setpoint_control_type" in args and args["setpoint_control_type"] not in {
                "ZeroFlowPower",
                "HalfFlowPower",
            }:
                errors.append("setpoint_control_type must be one of ZeroFlowPower, HalfFlowPower")
            if "terminal_chw_supply_c" in args:
                _num(args, "terminal_chw_supply_c", errors, lo=14.0, hi=20.0)
            if "terminal_chw_delta_t_c" in args:
                _num(args, "terminal_chw_delta_t_c", errors, lo=2.0, hi=6.0)
            if "terminal_chiller_reference_leaving_c" in args:
                _num(args, "terminal_chiller_reference_leaving_c", errors, lo=5.0, hi=20.0)
            if "terminal_chiller_cop" in args:
                _cop_num(args, "terminal_chiller_cop", errors)
            if "terminal_ashp_cooling_cop" in args:
                _cop_num(args, "terminal_ashp_cooling_cop", errors)
            if "terminal_wshp_cooling_cop" in args:
                _cop_num(args, "terminal_wshp_cooling_cop", errors)
            if "beam_type" in args and args.get("beam_type") not in {"cooled", "four_pipe", "four-pipe", "fourpipe"}:
                errors.append("beam_type must be one of cooled, four_pipe, four-pipe, fourpipe")
            if "beam_cooling_capacity_per_length_w_m" in args:
                _num(args, "beam_cooling_capacity_per_length_w_m", errors, lo=50.0, hi=2000.0)
            if "beam_heating_capacity_per_length_w_m" in args:
                _num(args, "beam_heating_capacity_per_length_w_m", errors, lo=50.0, hi=2500.0)
            if "beam_chw_flow_per_length_m3_s_m" in args:
                _num(args, "beam_chw_flow_per_length_m3_s_m", errors, lo=0.000001, hi=0.001)
            if "beam_hw_flow_per_length_m3_s_m" in args:
                _num(args, "beam_hw_flow_per_length_m3_s_m", errors, lo=0.000001, hi=0.001)
            if "beam_cooling_room_air_chw_delta_t_c" in args:
                _num(args, "beam_cooling_room_air_chw_delta_t_c", errors, lo=2.0, hi=20.0)
            if "beam_heating_room_air_hw_delta_t_c" in args:
                _num(args, "beam_heating_room_air_hw_delta_t_c", errors, lo=2.0, hi=40.0)
            if "beam_length_m" in args:
                _num(args, "beam_length_m", errors, lo=0.1, hi=500.0)
            if "beam_length_per_area_m_m2" in args:
                _num(args, "beam_length_per_area_m_m2", errors, lo=0.005, hi=1.0)
            if "beam_rated_primary_airflow_per_length_m3_s_m" in args:
                _num(args, "beam_rated_primary_airflow_per_length_m3_s_m", errors, lo=0.001, hi=0.1)
            if "heating_available_all_year" in args and not isinstance(args.get("heating_available_all_year"), bool):
                errors.append("heating_available_all_year must be true or false")
            if "cooling_available_all_year" in args and not isinstance(args.get("cooling_available_all_year"), bool):
                errors.append("cooling_available_all_year must be true or false")
            if "chiller_cop" in args:
                _cop_num(args, "chiller_cop", errors)
            if "ashp_cooling_cop" in args:
                _cop_num(args, "ashp_cooling_cop", errors)
            if "ashp_heating_cop" in args:
                _cop_num(args, "ashp_heating_cop", errors)
            if "wshp_cooling_cop" in args:
                _cop_num(args, "wshp_cooling_cop", errors)
            if "wshp_heating_cop" in args:
                _cop_num(args, "wshp_heating_cop", errors)
            if "wshp_source_supply_c" in args:
                _num(args, "wshp_source_supply_c", errors, lo=20.0, hi=35.0)
            if "wshp_source_delta_t_c" in args:
                _num(args, "wshp_source_delta_t_c", errors, lo=3.0, hi=8.0)
            if "wshp_source_minimum_c" in args:
                _num(args, "wshp_source_minimum_c", errors, lo=10.0, hi=25.0)
            _validate_optional_chw(args, errors)
            if "condenser_water_supply_c" in args:
                reference_c = _water_cooled_reference_condenser_c()
                _num(args, "condenser_water_supply_c", errors, lo=reference_c, hi=reference_c)
            for key in ("chw_pump_head_pa", "hw_pump_head_pa", "condenser_pump_head_pa"):
                if key in args:
                    _num(args, key, errors, lo=100000.0, hi=350000.0)
            if "pump_motor_efficiency" in args:
                _num(args, "pump_motor_efficiency", errors, lo=0.20, hi=0.97)
            if "hw_supply_c" in args:
                _num(args, "hw_supply_c", errors, lo=35.0, hi=80.0)
            if "hw_delta_t_c" in args:
                _num(args, "hw_delta_t_c", errors, lo=5.0, hi=20.0)
            if "boiler_efficiency" in args:
                _num(args, "boiler_efficiency", errors, lo=0.70, hi=0.98)
            if "fan_efficiency" in args:
                _num(args, "fan_efficiency", errors, lo=0.10, hi=0.65)
            if "fan_motor_efficiency" in args:
                _num(args, "fan_motor_efficiency", errors, lo=0.20, hi=0.97)
            fan_pa = _num(args, "fan_pressure_pa", errors) if "fan_pressure_pa" in args else None
            if fan_pa is not None and baseline_terminal_fan_pressure_pa is not None and fan_pa < baseline_terminal_fan_pressure_pa - 1e-6 and not args.get("engineering_justification"):
                errors.append("fan_pressure_pa below original terminal fan baseline requires engineering_justification")
            doas_relief_pa = _num(args, "doas_relief_fan_pressure_pa", errors) if "doas_relief_fan_pressure_pa" in args else None
            if doas_relief_pa is not None and baseline_doas_relief_fan_pressure_pa is not None and doas_relief_pa < baseline_doas_relief_fan_pressure_pa - 1e-6 and not args.get("engineering_justification"):
                errors.append("doas_relief_fan_pressure_pa below original DOAS relief baseline requires engineering_justification")
            doas_supply_pa = _num(args, "doas_supply_fan_pressure_pa", errors) if "doas_supply_fan_pressure_pa" in args else None
            if doas_supply_pa is not None and baseline_doas_supply_fan_pressure_pa is not None and doas_supply_pa < baseline_doas_supply_fan_pressure_pa - 1e-6 and not args.get("engineering_justification"):
                errors.append("doas_supply_fan_pressure_pa below original DOAS supply baseline requires engineering_justification")
            if "central_cooling_design_sat_c" in args:
                _num(args, "central_cooling_design_sat_c", errors, lo=12.0, hi=18.0)
            if "central_heating_design_sat_c" in args:
                _num(args, "central_heating_design_sat_c", errors, lo=18.0, hi=50.0)
            if "cooling_setpoint_c" in args:
                _num(args, "cooling_setpoint_c", errors, lo=22.0, hi=26.0)
            if "economizer_type" in args and args.get("economizer_type") not in ECONOMIZER_TYPES:
                errors.append(f"economizer_type must be one of {sorted(ECONOMIZER_TYPES)}")
            if "enable_dcv" in args and not isinstance(args.get("enable_dcv"), bool):
                errors.append("enable_dcv must be true or false")
            _validate_doas_economizer(args, errors)
            if "add_heat_recovery" in args and not isinstance(args.get("add_heat_recovery"), bool):
                errors.append("add_heat_recovery must be true or false")
            if "replace_existing_hvac" in args and not isinstance(args.get("replace_existing_hvac"), bool):
                errors.append("replace_existing_hvac must be true or false")
            if "hrv_sensible_effectiveness" in args:
                _num(args, "hrv_sensible_effectiveness", errors, lo=0.0, hi=0.80)
            if "hrv_latent_effectiveness" in args:
                _num(args, "hrv_latent_effectiveness", errors, lo=0.0, hi=0.80)
            if "doas_cooling_sat_c" in args:
                _validate_construct_doas_cooling_sat(args, errors)

        elif name == "configure_existing_lowtemp_radiant":
            _num(args, "cooling_capacity_w_per_m2", errors, lo=20.0, hi=120.0)
            _num(args, "cooling_control_temperature_c", errors, lo=22.0, hi=27.0)
            _num(args, "dewpoint_safety_offset_c", errors, lo=0.0, hi=4.0)
            if "cooling_control_throttling_range_c" in args:
                _num(args, "cooling_control_throttling_range_c", errors, lo=0.5, hi=5.0)

        elif name == "configure_existing_chilled_beam":
            _validate_chilled_beam_args(args, errors)

        elif name in {"construct_chilled_beam_terminals", "construct_doas_chilled_beam"}:
            if "cooling_sizing_factor" in args:
                _num(args, "cooling_sizing_factor", errors, lo=1.0, hi=1.30)
            if name == "construct_doas_chilled_beam" and "availability_prestart_hours" in args:
                _num(args, "availability_prestart_hours", errors, lo=0.0, hi=4.0)
            _validate_chilled_beam_args(args, errors)
            if "cooling_setpoint_c" in args:
                _num(args, "cooling_setpoint_c", errors, lo=22.0, hi=26.0)
            high_lift_source = args.get("high_lift_source")
            if high_lift_source is not None and high_lift_source not in COOLING_SOURCES:
                errors.append(f"high_lift_source must be one of {sorted(COOLING_SOURCES)}")
            low_lift_source = args.get("low_lift_source") or args.get("cooling_source")
            if low_lift_source is not None and low_lift_source not in COOLING_SOURCES:
                errors.append(f"low_lift_source/cooling_source must be one of {sorted(COOLING_SOURCES)}")
            heating_source = args.get("heating_source")
            if heating_source is not None and heating_source not in HEATING_SOURCES:
                errors.append(f"heating_source must be one of {sorted(HEATING_SOURCES)}")
            if "chw_supply_c" in args:
                _num(args, "chw_supply_c", errors, lo=5.0, hi=12.0)
            if "chw_delta_t_c" in args:
                _num(args, "chw_delta_t_c", errors, lo=3.0, hi=8.0)
            if "hw_supply_c" in args:
                _num(args, "hw_supply_c", errors, lo=35.0, hi=60.0)
            if "hw_delta_t_c" in args:
                _num(args, "hw_delta_t_c", errors, lo=3.0, hi=12.0)
            if "chiller_cop" in args:
                _cop_num(args, "chiller_cop", errors)
            if "ashp_cooling_cop" in args:
                _cop_num(args, "ashp_cooling_cop", errors)
            if "ashp_heating_cop" in args:
                _cop_num(args, "ashp_heating_cop", errors)
            if "wshp_cooling_cop" in args:
                _cop_num(args, "wshp_cooling_cop", errors)
            if "wshp_heating_cop" in args:
                _cop_num(args, "wshp_heating_cop", errors)
            if "wshp_source_supply_c" in args:
                _num(args, "wshp_source_supply_c", errors, lo=10.0, hi=35.0)
            if "wshp_source_delta_t_c" in args:
                _num(args, "wshp_source_delta_t_c", errors, lo=2.0, hi=8.0)
            if "wshp_source_minimum_c" in args:
                _num(args, "wshp_source_minimum_c", errors, lo=10.0, hi=25.0)
            if "terminal_chiller_cop" in args:
                _cop_num(args, "terminal_chiller_cop", errors)
            if "terminal_chiller_reference_leaving_c" in args:
                _num(args, "terminal_chiller_reference_leaving_c", errors, lo=5.0, hi=20.0)
            if "terminal_ashp_cooling_cop" in args:
                _cop_num(args, "terminal_ashp_cooling_cop", errors)
            if "terminal_wshp_cooling_cop" in args:
                _cop_num(args, "terminal_wshp_cooling_cop", errors)
            if "primary_airflow_multiplier" in args:
                _num(args, "primary_airflow_multiplier", errors, lo=1.0, hi=3.0)
            if name == "construct_doas_chilled_beam":
                if "doas_cooling_sat_c" in args:
                    _validate_construct_doas_cooling_sat(args, errors)
                if "enable_dcv" in args and not isinstance(args.get("enable_dcv"), bool):
                    errors.append("enable_dcv must be true or false")
                if args.get("enable_dcv") is True:
                    errors.append(
                        "enable_dcv=true is not valid for construct_doas_chilled_beam: active beams need "
                        "constant primary air for induction; DCV is modelled for the DOAS+FCU and radiant systems"
                    )
                if "economizer_type" in args and args.get("economizer_type") not in ECONOMIZER_TYPES:
                    errors.append(f"economizer_type must be one of {sorted(ECONOMIZER_TYPES)}")
                _validate_doas_economizer(args, errors)
            target_zones = args.get("target_zones")
            if target_zones is not None and (
                not isinstance(target_zones, list)
                or not all(isinstance(z, str) and z for z in target_zones)
            ):
                errors.append("target_zones must be a list of non-empty zone-name strings")

        elif name == "configure_existing_heat_recovery":
            _num(args, "sensible_effectiveness", errors, lo=0.0, hi=0.80)
            _num(args, "latent_effectiveness", errors, lo=0.0, hi=0.80)

        elif name == "configure_existing_demand_control_ventilation":
            if not isinstance(args.get("enable"), bool):
                errors.append("'enable' must be true or false")
            if args.get("enable") is True:
                warnings.append(
                    "configure_existing_demand_control_ventilation requires an existing DOAS "
                    "variable-volume fan; the Ruby model modifier will reject constant-volume DOAS fans."
                )

    if seen_water_cooled and not any(c.get("tool") == "configure_chw_pumps" for c in calls if isinstance(c, dict)):
        warnings.append("water-cooled system selected; consider configure_chw_pumps for explicit pump assumptions")

    if not plan.get("engineering_basis"):
        warnings.append("missing engineering_basis; cite the active standards priority, baseline evidence, and climate rationale")

    return ToolPlanValidation(not errors, errors, warnings)


def _validate_optional_chw(args: dict[str, Any], errors: list[str]) -> None:
    if "chw_supply_c" in args or "chw_delta_t_c" in args:
        merged = {"chw_supply_c": args.get("chw_supply_c", 7.0), "chw_delta_t_c": args.get("chw_delta_t_c", 5.0),
                  "engineering_justification": args.get("engineering_justification")}
        _validate_chw(merged, errors)


def _validate_chw(args: dict[str, Any], errors: list[str]) -> None:
    _num(args, "chw_supply_c", errors, lo=5.0, hi=9.0)
    delta = _num(args, "chw_delta_t_c", errors, lo=3.0, hi=8.0)
    if delta is not None and delta > 7.0 and not args.get("engineering_justification"):
        errors.append("chw_delta_t_c above 7 K requires engineering_justification")


def _validate_doas_economizer(args: dict[str, Any], errors: list[str]) -> None:
    """A 100% outdoor-air DOAS has no return air to mix; its economizer only bypasses heat recovery."""
    economizer = args.get("economizer_type")
    if economizer not in (None, "NoEconomizer") and args.get("add_heat_recovery") is not True:
        errors.append(
            "economizer_type on a 100% outdoor-air DOAS only bypasses the heat recovery wheel; "
            "it requires add_heat_recovery=true"
        )


def _validate_construct_doas_cooling_sat(
    args: dict[str, Any], errors: list[str]
) -> None:
    sat = _num(
        args,
        "doas_cooling_sat_c",
        errors,
        lo=DOAS_COOLING_SAT_MIN_C,
        hi=DOAS_COOLING_SAT_MAX_C,
    )
    if sat is None or abs(sat - DOAS_COOLING_SAT_DEFAULT_C) <= 1e-6:
        return
    justification = args.get("doas_cooling_sat_justification")
    if not isinstance(justification, str) or not justification.strip():
        errors.append(
            "doas_cooling_sat_c differing from the 12 C default requires "
            "doas_cooling_sat_justification"
        )


def _validate_chilled_beam_args(args: dict[str, Any], errors: list[str]) -> None:
    inlet = _num(args, "design_inlet_water_temperature_c", errors, lo=12.0, hi=18.0)
    outlet = _num(args, "design_outlet_water_temperature_c", errors, lo=14.0, hi=21.0)
    if inlet is not None and outlet is not None and outlet <= inlet:
        errors.append("design_outlet_water_temperature_c must exceed design_inlet_water_temperature_c")
    if "max_chw_flow_m3_s" in args:
        _num(args, "max_chw_flow_m3_s", errors, lo=0.00001, hi=0.02)
    if "beam_length_m" in args:
        _num(args, "beam_length_m", errors, lo=0.5, hi=20.0)
    if "number_of_beams" in args:
        _num(args, "number_of_beams", errors, lo=1, hi=200)
    if "coil_surface_area_per_length_m2_m" in args:
        _num(args, "coil_surface_area_per_length_m2_m", errors, lo=0.01, hi=2.0)


def normalize_tool_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a compact plan for the Ruby modifier.

    Unknown top-level keys are dropped so the Ruby layer receives a stable
    payload. Tool args are kept because the registry validation owns them.
    """
    calls = []
    for call in plan.get("tool_calls", []):
        calls.append({"tool": call["tool"], "args": dict(call.get("args", {}))})
    return {
        "case_id": plan.get("case_id"),
        "strategy_summary": plan.get("strategy_summary", ""),
        "engineering_basis": plan.get("engineering_basis", ""),
        "rationale": plan.get("rationale", ""),
        "tool_calls": calls,
    }
