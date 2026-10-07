from __future__ import annotations

from typing import Any


ORIENTATIONS = ("north", "east", "south", "west")
WINDOW_TYPES = ("offset_window", "floor_to_ceiling", "continuous_window", "punched_window")
SHADING_TYPES = ("none", "horizontal", "vertical", "combined", "louver", "perforated_panel", "ledge")
STORY_PATTERNS: dict[str, tuple[int, int]] = {
    "all": (0, 18),
    "lower": (0, 6),
    "middle": (6, 12),
    "upper": (12, 18),
    "lower+middle": (0, 12),
    "middle+upper": (6, 18),
}


def _float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip(value: Any, lo: float, hi: float, default: float) -> float:
    return max(lo, min(hi, _float(value, default)))


def _quantize(value: Any, lo: float, hi: float, step: float, default: float) -> float:
    clipped = _clip(value, lo, hi, default)
    return round(round(clipped / step) * step, 6)


def _choice(value: Any, choices: tuple[str, ...], default: str) -> str:
    text = str(value or default).strip().lower().replace("-", "_")
    aliases = {
        "perforated panel": "perforated_panel",
        "perforated-panel": "perforated_panel",
        "floor to ceiling": "floor_to_ceiling",
        "floor-to-ceiling": "floor_to_ceiling",
        "offset window": "offset_window",
        "continuous window": "continuous_window",
        "punched window": "punched_window",
    }
    text = aliases.get(text, text)
    return text if text in choices else default


def _story_range(value: Any) -> tuple[str, int, int]:
    if isinstance(value, dict):
        start = int(_clip(value.get("story_start"), 0, 18, 0))
        end = int(_clip(value.get("story_end"), 0, 18, 18))
        if end <= start:
            start, end = 0, 18
        return f"{start}-{end}", start, end
    key = str(value or "all").strip().lower().replace(" ", "")
    key = {"lower_middle": "lower+middle", "middle_upper": "middle+upper"}.get(key, key)
    start, end = STORY_PATTERNS.get(key, STORY_PATTERNS["all"])
    return key if key in STORY_PATTERNS else "all", start, end


def _window_parameters(window_type: str, proposal: dict[str, Any]) -> dict[str, Any]:
    source = proposal.get("window_parameters") or {}
    height = _quantize(source.get("window_height_m"), 1.00, 3.00, 0.10, 2.00)
    sill = source.get("sill_height_m")
    if window_type == "offset_window":
        return {"window_height_m": height, "sill_height_m": _quantize(sill, 0.10, 1.20, 0.05, 0.80)}
    if window_type == "floor_to_ceiling":
        return {
            "min_inset_m": _quantize(source.get("min_inset_m"), 0.05, 0.20, 0.05, 0.10),
            "vertical_inset_m": _quantize(source.get("vertical_inset_m"), 0.05, 0.30, 0.05, 0.15),
            "side_margin_m": _quantize(source.get("side_margin_m"), 0.10, 0.50, 0.05, 0.25),
        }
    if window_type == "continuous_window":
        return {
            "edge_margin_m": _quantize(source.get("edge_margin_m"), 0.10, 0.60, 0.05, 0.30),
            "sill_height_m": _quantize(sill, 0.10, 1.20, 0.05, 0.75),
            "head_clearance_m": _quantize(source.get("head_clearance_m"), 0.15, 0.60, 0.05, 0.30),
            "min_window_height_m": 1.00,
            "window_height_m": height,
        }
    return {
        "punched_edge_margin_m": _quantize(source.get("punched_edge_margin_m"), 0.10, 0.60, 0.05, 0.30),
        "sill_height_m": _quantize(sill, 0.10, 1.20, 0.05, 0.80),
        "head_clearance_m": _quantize(source.get("head_clearance_m"), 0.20, 0.60, 0.05, 0.35),
        "module_width_m": 1.50,
        "module_gap_m": 0.30,
        "max_module_count": 8,
        "window_height_m": height,
    }


def physical_design_to_spec(
    proposal: dict[str, Any],
    *,
    method: str,
    candidate_index: int,
    case_id: str,
) -> dict[str, Any]:
    design = proposal.get("design") if isinstance(proposal.get("design"), dict) else proposal
    window_type = _choice(design.get("window_type"), WINDOW_TYPES, "floor_to_ceiling")
    orientation_wwr_input = design.get("orientation_wwr") or design.get("wwr") or {}
    facade_input = design.get("facades") or {}

    orientation_wwr: dict[str, float] = {}
    orientation_window_type: dict[str, str] = {}
    orientation_window_parameters: dict[str, dict[str, Any]] = {}
    shading_depths: dict[str, float] = {}
    shading_systems: list[dict[str, Any]] = []
    decoded_facades: dict[str, Any] = {}

    for orientation in ORIENTATIONS:
        facade = facade_input.get(orientation) or {}
        shade_type = _choice(facade.get("shading_type") or facade.get("type"), SHADING_TYPES, "none")
        wwr = _quantize(orientation_wwr_input.get(orientation, facade.get("wwr")), 0.50, 0.90, 0.01, 0.60)
        depth = 0.0 if shade_type == "none" else _quantize(facade.get("depth_m"), 0.05, 0.95, 0.05, 0.40)
        story_pattern, story_start, story_end = _story_range(facade.get("story_pattern", facade.get("story_range", "all")))
        angle_primary = _quantize(
            facade.get("angle_deg", facade.get("horizontal_shade_angle_deg", facade.get("louver_tilt_angle_deg", facade.get("perforated_panel_angle_deg")))),
            -30.0,
            30.0,
            5.0,
            0.0,
        )
        angle_secondary = _quantize(facade.get("vertical_fin_angle_deg"), -30.0, 30.0, 5.0, 0.0)
        facade_window_type = _choice(facade.get("window_type"), WINDOW_TYPES, window_type)
        orientation_window_type[orientation] = facade_window_type
        orientation_window_parameters[orientation] = _window_parameters(
            facade_window_type, facade if facade.get("window_parameters") else design
        )

        orientation_wwr[orientation] = wwr
        shading_depths[orientation] = depth
        decoded = {
            "type": shade_type,
            "depth_m": depth,
            "story_pattern": story_pattern,
            "story_start": story_start,
            "story_end": story_end,
        }
        if shade_type != "none":
            system: dict[str, Any] = {
                "type": shade_type,
                "orientations": [orientation],
                "story_start": story_start,
                "story_end": story_end,
                "depth_m": depth,
            }
            if shade_type in ("horizontal", "combined"):
                system["horizontal_shade_angle_deg"] = angle_primary
                system["overhang_gap_m"] = _quantize(facade.get("overhang_gap_m"), 0.0, 0.6, 0.05, 0.10)
            if shade_type in ("horizontal", "combined", "louver"):
                system["overhang_side_extension_m"] = _quantize(
                    facade.get("overhang_side_extension_m"), 0.0, 1.0, 0.1, 0.0
                )
            if shade_type == "vertical":
                system["vertical_fin_angle_deg"] = angle_primary
            if shade_type == "combined":
                system["vertical_fin_angle_deg"] = angle_secondary
            if shade_type == "louver":
                system["louver_tilt_angle_deg"] = angle_primary
                system["louver_spacing_m"] = _quantize(facade.get("louver_spacing_m"), 3.0, 4.0, 0.1, 3.5)
            if shade_type == "perforated_panel":
                system["perforated_panel_angle_deg"] = 0.0
                system["perforated_panel_open_area_ratio"] = _quantize(
                    facade.get("perforated_panel_open_area_ratio"),
                    0.70,
                    0.95,
                    0.05,
                    0.80,
                )
            shading_systems.append(system)
            decoded.update({key: value for key, value in system.items() if key != "orientations"})
        decoded_facades[orientation] = decoded

    active_types = [item["type"] for item in shading_systems]
    mean_depth = sum(shading_depths.values()) / len(shading_depths)
    return {
        "description": f"{method} physical-space candidate {candidate_index:03d}",
        "design_intent": (
            "LLM-authored physical-value proposal clipped to the shared hierarchical mixed-variable "
            "Step 2 design space. Genome-based methods remain unchanged."
        ),
        "case_id": case_id,
        "wwr": round(sum(orientation_wwr.values()) / len(orientation_wwr), 3),
        "orientation_wwr": orientation_wwr,
        "window_type": orientation_window_type["north"],
        "window_position_strategy": "orientation_by_facade",
        "window_parameters": orientation_window_parameters["north"],
        "orientation_window_type": orientation_window_type,
        "orientation_window_parameters": orientation_window_parameters,
        "shading_type": active_types[0] if active_types else "none",
        "horizontal_shade_depth_m": round(mean_depth, 3),
        "shading_depths_m": shading_depths,
        "shading_systems": shading_systems,
        "benchmark_method": method,
        "benchmark_candidate_index": candidate_index,
        "benchmark_search_space_version": "hierarchical_mixed_v2_physical_llm",
        "decoded_facade_design": decoded_facades,
        "llm_physical_design": design,
    }
