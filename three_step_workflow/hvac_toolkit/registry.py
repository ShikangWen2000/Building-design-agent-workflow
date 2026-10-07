"""Tool registry for the Step 3 HVAC design interface.

The registry is intentionally small and explicit. The LLM can choose from
these names, but each tool is validated before any OpenStudio model is touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HvacToolSpec:
    name: str
    description: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    implementation: str = "full"


TOOL_SPECS: dict[str, HvacToolSpec] = {
    "apply_air_cooled_chiller": HvacToolSpec(
        name="apply_air_cooled_chiller",
        description="Set electric EIR chillers on the selected cooling loop(s) to air-cooled operation and assign BEC-plausible COP.",
        required=("chiller_cop",),
        optional=("chw_supply_c", "chw_delta_t_c", "target_loops", "engineering_justification"),
    ),
    "apply_water_cooled_chiller_system": HvacToolSpec(
        name="apply_water_cooled_chiller_system",
        description="Set chillers on the selected cooling loop(s) to water-cooled operation and add/connect a condenser loop with cooling tower.",
        required=("chiller_cop", "condenser_water_supply_c"),
        optional=("chw_supply_c", "chw_delta_t_c", "target_loops", "condenser_pump_head_pa", "engineering_justification"),
    ),
    "configure_chw_loop": HvacToolSpec(
        name="configure_chw_loop",
        description="Set chilled-water supply temperature and loop design delta-T.",
        required=("chw_supply_c", "chw_delta_t_c"),
        optional=("target_loops", "engineering_justification"),
    ),
    "configure_supply_fans": HvacToolSpec(
        name="configure_supply_fans",
        description="Configure fan efficiency, motor efficiency, and terminal/DOAS pressure rise without reducing static pressure silently.",
        required=("fan_efficiency", "fan_motor_efficiency", "fan_pressure_pa"),
        optional=("doas_relief_fan_pressure_pa", "doas_supply_fan_pressure_pa", "engineering_justification"),
    ),
    "configure_chw_pumps": HvacToolSpec(
        name="configure_chw_pumps",
        description="Configure chilled-water pump head and motor efficiency without reducing pump head silently.",
        required=("pump_head_pa", "pump_motor_efficiency"),
        optional=("target_loops", "engineering_justification"),
    ),
    "configure_economizer": HvacToolSpec(
        name="configure_economizer",
        description="Configure the native outdoor-air economizer control type. Humidity lockout and maximum OA fraction overrides are not implemented.",
        required=("economizer_type",),
        optional=(),
    ),
    "add_heat_recovery_ventilation": HvacToolSpec(
        name="add_heat_recovery_ventilation",
        description="Add air-to-air sensible/latent heat recovery to outdoor air systems.",
        required=("sensible_effectiveness", "latent_effectiveness"),
        optional=("bypass_when_economizing",),
    ),
    "configure_radiant_ceiling_panel": HvacToolSpec(
        name="configure_radiant_ceiling_panel",
        description=(
            "Create hydronic radiant ceiling cooling panel terminals for thermal zones, with condensation safeguards."
        ),
        required=("panel_type", "cooling_design_capacity_w_per_m2", "cooling_control_temperature_c", "dewpoint_safety_offset_c"),
        optional=(
            "requires_doas",
            "condensation_control",
            "terminal_chw_supply_c",
            "terminal_chw_delta_t_c",
            "radiant_rated_water_mass_flow_kg_s",
            "radiant_max_chw_flow_m3_s",
            "notes",
            "cooling_source",
            "heating_source",
        ),
    ),
    "add_radiant_ceiling_metal_panels": HvacToolSpec(
        name="add_radiant_ceiling_metal_panels",
        description=(
            "Add metal radiant ceiling panels to the roof and ceilings: each existing construction keeps its layers "
            "and gains a room-side ceiling cavity and two metal panel layers with the chilled-water source between them."
        ),
        required=("panel_scope",),
        optional=("notes",),
    ),
    "configure_terminal_strategy": HvacToolSpec(
        name="configure_terminal_strategy",
        description="Record terminal-unit strategy such as FCU retained, radiant or chilled-beam concept.",
        required=("terminal_type",),
        optional=("notes",),
        implementation="record_only",
    ),
    "construct_doas_fcu_chiller_boiler": HvacToolSpec(
        name="construct_doas_fcu_chiller_boiler",
        description=(
            "Construct a DOAS air loop with no-reheat outdoor-air terminals, zone four-pipe fan coils, "
            "separate DOAS and terminal chilled-water plants, and the project-required DOAS heating coil."
        ),
        required=(),
        optional=(
            "chiller_cop",
            "chw_supply_c",
            "chw_delta_t_c",
            "condenser_water_supply_c",
            "chw_pump_head_pa",
            "hw_supply_c",
            "hw_delta_t_c",
            "hw_pump_head_pa",
            "condenser_pump_head_pa",
            "pump_motor_efficiency",
            "boiler_efficiency",
            "boiler_fuel_type",
            "fan_efficiency",
            "fan_motor_efficiency",
            "fan_pressure_pa",
            "doas_relief_fan_pressure_pa",
            "doas_supply_fan_pressure_pa",
            "central_cooling_design_sat_c",
            "central_heating_design_sat_c",
            "cooling_setpoint_c",
            "economizer_type",
            "enable_dcv",
            "add_heat_recovery",
            "hrv_sensible_effectiveness",
            "hrv_latent_effectiveness",
            "replace_existing_hvac",
            "availability_prestart_hours",
            "hvac_availability_schedule_name",
            "availability_schedule_name",
            "doas_availability_schedule_name",
            "ventilation_schedule_name",
            "doas_cooling_sat_c",
            "fcu_cooling_sat_c",
            "fcu_heating_sat_c",
            "notes",
            "cooling_source",
            "high_lift_source",
            "fcu_chw_topology",
            "low_lift_source",
            "terminal_chw_supply_c",
            "terminal_chw_delta_t_c",
            "terminal_chiller_reference_leaving_c",
            "terminal_chiller_cop",
            "terminal_cooling_capacity_kw",
            "capacity_normalization_basis",
            "heating_source",
            "ashp_cooling_cop",
            "ashp_heating_cop",
            "wshp_cooling_cop",
            "wshp_heating_cop",
            "wshp_source_supply_c",
            "wshp_source_delta_t_c",
            "wshp_source_minimum_c",
            "engineering_justification",
            "cooling_sizing_factor",
        ),
    ),
    "apply_standards_hvac_system": HvacToolSpec(
        name="apply_standards_hvac_system",
        description=(
            "Construct an HVAC system from openstudio-standards using `model_add_hvac_system`. "
            "Adds an ASHRAE 90.1-vintage system with realistic equipment efficiencies, performance "
            "curves, and controls. Chain multiple calls (for example DOAS + Fan Coil) on the same zones "
            "to build compound systems. Set replace_existing_hvac=true on the first call only when "
            "starting from a bare envelope."
        ),
        required=("standard_template", "system_type"),
        optional=(
            "main_heat_fuel",
            "zone_heat_fuel",
            "cool_fuel",
            "target_zones",
            "hot_water_loop_type",
            "chilled_water_loop_cooling_type",
            "heat_pump_loop_cooling_type",
            "air_loop_heating_type",
            "air_loop_cooling_type",
            "zone_equipment_ventilation",
            "fan_coil_capacity_control_method",
            "replace_existing_hvac",
            "notes",
        ),
    ),
    "construct_doas_lowtemp_radiant": HvacToolSpec(
        name="construct_doas_lowtemp_radiant",
        description=(
            "Construct a DOAS air loop with no-reheat outdoor-air terminals, zone low-temperature radiant "
            "variable-flow terminals on a dedicated warm terminal chilled-water loop (with terminal chiller "
            "heat pump), a water-cooled chiller DOAS chilled-water plant, condenser loop, and hot-water "
            "boiler loop serving radiant heating coils. In cooling-only Hong Kong cases the DOAS heating coil "
            "is an always-off topology placeholder controlled at the 12 C cooling-coil leaving setpoint, not reheat."
        ),
        required=(
            "dewpoint_safety_offset_c",
            "requires_doas",
            "condensation_control",
        ),
        optional=(
            "cooling_control_temperature_c",
            "heating_season_cooling_control_temperature_c",
            "cooling_capacity_method",
            "cooling_capacity_w_per_m2",
            "heating_capacity_w_per_m2",
            "heating_control_temperature_c",
            "cooling_design_surface_temperature_c",
            "cooling_design_air_temperature_c",
            "heating_design_surface_temperature_c",
            "heating_design_air_temperature_c",
            "cooling_control_throttling_range_c",
            "heating_control_throttling_range_c",
            "radiant_surface_type",
            "intersect_and_match_surfaces",
            "temperature_control_type",
            "setpoint_control_type",
            "terminal_chw_supply_c",
            "terminal_chw_delta_t_c",
            "terminal_chiller_reference_leaving_c",
            "terminal_chiller_cop",
            "terminal_cooling_capacity_kw",
            "capacity_normalization_basis",
            "terminal_ashp_cooling_cop",
            "terminal_wshp_cooling_cop",
            "chiller_cop",
            "chw_supply_c",
            "chw_delta_t_c",
            "condenser_water_supply_c",
            "chw_pump_head_pa",
            "hw_supply_c",
            "hw_delta_t_c",
            "hw_pump_head_pa",
            "condenser_pump_head_pa",
            "pump_motor_efficiency",
            "boiler_efficiency",
            "boiler_fuel_type",
            "fan_efficiency",
            "fan_motor_efficiency",
            "fan_pressure_pa",
            "doas_relief_fan_pressure_pa",
            "doas_supply_fan_pressure_pa",
            "central_cooling_design_sat_c",
            "central_heating_design_sat_c",
            "cooling_setpoint_c",
            "economizer_type",
            "enable_dcv",
            "add_heat_recovery",
            "hrv_sensible_effectiveness",
            "hrv_latent_effectiveness",
            "replace_existing_hvac",
            "availability_prestart_hours",
            "hvac_availability_schedule_name",
            "availability_schedule_name",
            "doas_availability_schedule_name",
            "ventilation_schedule_name",
            "doas_cooling_sat_c",
            "doas_cooling_sat_justification",
            "notes",
            "high_lift_source",
            "low_lift_source",
            "heating_source",
            "engineering_justification",
            "cooling_sizing_factor",
            "ashp_cooling_cop",
            "ashp_heating_cop",
            "wshp_cooling_cop",
            "wshp_heating_cop",
            "wshp_source_supply_c",
            "wshp_source_delta_t_c",
            "wshp_source_minimum_c",
        ),
    ),
    "configure_existing_lowtemp_radiant": HvacToolSpec(
        name="configure_existing_lowtemp_radiant",
        description="Tune existing low-temperature radiant variable-flow terminals and coils in an OSM template.",
        required=("cooling_capacity_w_per_m2", "cooling_control_temperature_c", "dewpoint_safety_offset_c"),
        optional=("cooling_control_throttling_range_c", "condensation_control", "intersect_and_match_surfaces"),
    ),
    "configure_existing_chilled_beam": HvacToolSpec(
        name="configure_existing_chilled_beam",
        description="Tune existing constant-volume cooled-beam terminals and cooled-beam coils in an OSM template.",
        required=("design_inlet_water_temperature_c", "design_outlet_water_temperature_c"),
        optional=("max_chw_flow_m3_s", "beam_length_m", "number_of_beams", "coil_surface_area_per_length_m2_m"),
    ),
    "construct_chilled_beam_terminals": HvacToolSpec(
        name="construct_chilled_beam_terminals",
        description="Construct active chilled-beam terminals, connect them to an air loop by zone, and connect beam coils to a dedicated terminal cooling plant loop.",
        required=("design_inlet_water_temperature_c", "design_outlet_water_temperature_c"),
        optional=(
            "coil_surface_area_per_length_m2_m",
            "primary_airflow_multiplier",
            "cooling_setpoint_c",
            "target_zones",
            "notes",
            "low_lift_source",
            "cooling_source",
            "heating_source",
            "ashp_cooling_cop",
            "terminal_chiller_cop",
            "terminal_cooling_capacity_kw",
            "capacity_normalization_basis",
            "terminal_chiller_reference_leaving_c",
            "terminal_ashp_cooling_cop",
            "terminal_wshp_cooling_cop",
        ),
    ),
    "construct_doas_chilled_beam": HvacToolSpec(
        name="construct_doas_chilled_beam",
        description=(
            "Construct a complete DOAS + active chilled-beam system with explicit high-lift DOAS plant loop "
            "and low-lift chilled-beam terminal plant loop. Current Block M tuned chilled-beam defaults use "
            "15/18C terminal water, beam_type=four_pipe, primary_airflow_multiplier=1.0, "
            "active-baseline-derived fan pressure, and beam_cooling_capacity_per_length_w_m=960 W/m. "
            "Without beam_length_m or beam_length_per_area_m_m2 the zone beam length is sized before "
            "simulation to the zone design cooling load divided by the rated capacity per length; without "
            "beam_chw_flow_per_length_m3_s_m the rated chilled-water flow per length carries the rated "
            "capacity at the design inlet/outlet water temperature difference. "
            "Capacity basis: ASHRAE Houston chilled-beam application guidance lists active chilled beam "
            "cooling up to 1000 Btu/h/ft; 1000 Btu/h/ft converts to 961 W/m, so 960 W/m is treated as an "
            "upper-bound product selection."
        ),
        required=("design_inlet_water_temperature_c", "design_outlet_water_temperature_c"),
        optional=(
            "high_lift_source",
            "low_lift_source",
            "cooling_source",
            "heating_source",
            "chw_supply_c",
            "chw_delta_t_c",
            "hw_supply_c",
            "hw_delta_t_c",
            "chiller_cop",
            "ashp_cooling_cop",
            "ashp_heating_cop",
            "wshp_cooling_cop",
            "wshp_heating_cop",
            "wshp_source_supply_c",
            "wshp_source_delta_t_c",
            "wshp_source_minimum_c",
            "terminal_chiller_cop",
            "terminal_cooling_capacity_kw",
            "capacity_normalization_basis",
            "terminal_chiller_reference_leaving_c",
            "terminal_ashp_cooling_cop",
            "terminal_wshp_cooling_cop",
            "beam_type",
            "coil_surface_area_per_length_m2_m",
            "beam_cooling_capacity_per_length_w_m",
            "beam_heating_capacity_per_length_w_m",
            "beam_chw_flow_per_length_m3_s_m",
            "beam_hw_flow_per_length_m3_s_m",
            "beam_cooling_room_air_chw_delta_t_c",
            "beam_heating_room_air_hw_delta_t_c",
            "beam_length_m",
            "beam_length_per_area_m_m2",
            "beam_rated_primary_airflow_per_length_m3_s_m",
            "heating_available_all_year",
            "cooling_available_all_year",
            "primary_airflow_multiplier",
            "cooling_setpoint_c",
            "target_zones",
            "fan_efficiency",
            "fan_motor_efficiency",
            "fan_pressure_pa",
            "doas_relief_fan_pressure_pa",
            "doas_supply_fan_pressure_pa",
            "enable_dcv",
            "economizer_type",
            "add_heat_recovery",
            "hrv_sensible_effectiveness",
            "hrv_latent_effectiveness",
            "replace_existing_hvac",
            "availability_prestart_hours",
            "hvac_availability_schedule_name",
            "availability_schedule_name",
            "doas_availability_schedule_name",
            "ventilation_schedule_name",
            "doas_cooling_sat_c",
            "doas_cooling_sat_justification",
            "notes",
            "condenser_water_supply_c",
            "boiler_efficiency",
            "boiler_fuel_type",
            "engineering_justification",
            "cooling_sizing_factor",
        ),
    ),
    "configure_existing_heat_recovery": HvacToolSpec(
        name="configure_existing_heat_recovery",
        description="Tune existing air-to-air sensible/latent heat exchangers in an OSM template.",
        required=("sensible_effectiveness", "latent_effectiveness"),
        optional=("economizer_lockout",),
    ),
    "configure_existing_demand_control_ventilation": HvacToolSpec(
        name="configure_existing_demand_control_ventilation",
        description=(
            "Enable or disable demand-controlled ventilation on existing outdoor-air controllers. "
            "Only use when the existing DOAS air loop has variable-volume supply/relief fan control; "
            "constant-volume DOAS fans are rejected because DCV cannot reduce fan airflow meaningfully."
        ),
        required=("enable",),
        optional=(),
    ),
}


def public_tool_descriptions() -> list[dict[str, Any]]:
    """Return compact metadata suitable for inclusion in an LLM prompt."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "required": list(spec.required),
            "optional": list(spec.optional),
            "implementation": spec.implementation,
        }
        for spec in TOOL_SPECS.values()
    ]
