from __future__ import annotations

import copy
import math
import os
import random
from typing import Any


ORIENTATIONS = ("north", "east", "south", "west")
FACADE_KEYS = (
    "wwr",
    "window_type",
    "window_height",
    "window_sill",
    "shading_type",
    "depth",
    "angle_primary",
    "angle_secondary",
    "secondary_parameter",
    "story_pattern",
    "overhang_extension",
    "overhang_gap",
)
CATEGORICAL_FACADE_KEYS = ("window_type", "shading_type", "story_pattern")
CONTINUOUS_FACADE_KEYS = tuple(key for key in FACADE_KEYS if key not in CATEGORICAL_FACADE_KEYS)
WINDOW_PARAMETER_GENES = 3
OVERHANG_TYPES = ("horizontal", "combined", "louver")
STORY_PATTERNS = (
    (0, 18),
    (0, 6),
    (6, 12),
    (12, 18),
    (0, 12),
    (6, 18),
)
STORY_PATTERN_NAMES = ("all", "lower", "middle", "upper", "lower+middle", "middle+upper")
# Physical name of each shared window-parameter gene: (name, window type, parameter that defines its range).
SHARED_PHYSICAL = (
    ("edge_margin_m", "continuous_window", "edge_margin_m"),
    ("vertical_inset_m", "floor_to_ceiling", "vertical_inset_m"),
    ("head_clearance_m", "continuous_window", "head_clearance_m"),
)

# Two decoders share the same 51-gene genome. Each window type lists its WWR
# range (low, high, step) and its parameters; a parameter is a constant or
# (gene, low, high, step), where the gene is a facade gene or one of the three
# shared window-parameter genes "a", "b", "c".
#
# hierarchical_mixed_v2 spans every option the Step 2 stage accepts. Most of
# its combinations are rejected by the stage's constraints (realized WWR within
# 0.02 of the request, 30% shading projection, 3 m board spacing), because the
# window parameters and the WWR are sampled independently.
#
# valid_range_v3 keeps the combinations the stage can realize: per window type
# the WWR stops at what the window parameters can reach on a 4 m story, the
# margins stay small, options that cannot keep the 3 m board spacing or the
# wall outline on short wall segments (vertical and combined fins, punched
# modules) are left out, a perforated screen is not offered on floor-to-ceiling
# glazing (part of its frame falls outside the audited window area), and board
# tilt and screen openness stay inside the projection limit.
ALL_SHADING = ("none", "horizontal", "vertical", "combined", "louver", "perforated_panel", "ledge")
VALID_SHADING = ("none", "horizontal", "louver", "perforated_panel", "ledge")
SPACES: dict[str, dict[str, Any]] = {
    "hierarchical_mixed_v2": {
        "windows": {
            "offset_window": {"wwr": (0.50, 0.90, 0.01), "shading_types": ALL_SHADING, "parameters": {
                "window_height_m": ("window_height", 1.00, 3.00, 0.10),
                "sill_height_m": ("window_sill", 0.10, 1.20, 0.05)}},
            "floor_to_ceiling": {"wwr": (0.50, 0.90, 0.01), "shading_types": ALL_SHADING, "parameters": {
                "min_inset_m": ("a", 0.05, 0.20, 0.05),
                "vertical_inset_m": ("b", 0.05, 0.30, 0.05),
                "side_margin_m": ("c", 0.10, 0.50, 0.05)}},
            "continuous_window": {"wwr": (0.50, 0.90, 0.01), "shading_types": ALL_SHADING, "parameters": {
                "edge_margin_m": ("a", 0.10, 0.60, 0.05),
                "sill_height_m": ("window_sill", 0.10, 1.20, 0.05),
                "head_clearance_m": ("c", 0.15, 0.60, 0.05),
                "min_window_height_m": 1.00,
                "window_height_m": ("window_height", 1.00, 3.00, 0.10)}},
            "punched_window": {"wwr": (0.50, 0.90, 0.01), "shading_types": ALL_SHADING, "parameters": {
                "punched_edge_margin_m": ("a", 0.10, 0.60, 0.05),
                "sill_height_m": ("window_sill", 0.10, 1.20, 0.05),
                "head_clearance_m": ("c", 0.20, 0.60, 0.05),
                "module_width_m": 1.50,
                "module_gap_m": 0.30,
                "max_module_count": 8,
                "window_height_m": ("window_height", 1.00, 3.00, 0.10)}},
        },
        "ranges": {
            "shading_depth_m": (0.05, 0.95, 0.05),
            "shading_angle_deg": (-30.0, 30.0, 5.0),
            "louver_spacing_m": (3.0, 4.0, 0.1),
            "perforated_panel_open_area_ratio": (0.70, 0.95, 0.05),
            "overhang_side_extension_m": (0.0, 1.0, 0.1),
            "overhang_gap_m": (0.0, 0.6, 0.05),
        },
    },
    "valid_range_v3": {
        "windows": {
            "offset_window": {"wwr": (0.50, 0.80, 0.01), "shading_types": VALID_SHADING, "parameters": {
                "window_height_m": ("window_height", 1.00, 3.00, 0.10),
                "sill_height_m": ("window_sill", 0.10, 1.20, 0.05)}},
            "floor_to_ceiling": {"wwr": (0.50, 0.80, 0.01),
                                 "shading_types": ("none", "horizontal", "louver", "ledge"), "parameters": {
                "min_inset_m": ("a", 0.05, 0.15, 0.05),
                "vertical_inset_m": ("b", 0.05, 0.15, 0.05),
                "side_margin_m": ("c", 0.10, 0.15, 0.05)}},
            "continuous_window": {"wwr": (0.50, 0.70, 0.01), "shading_types": VALID_SHADING, "parameters": {
                "edge_margin_m": ("a", 0.10, 0.30, 0.05),
                "sill_height_m": ("window_sill", 0.10, 0.60, 0.05),
                "head_clearance_m": ("c", 0.15, 0.30, 0.05),
                "min_window_height_m": 1.00,
                "window_height_m": ("window_height", 1.00, 3.00, 0.10)}},
        },
        "ranges": {
            "shading_depth_m": (0.05, 0.95, 0.05),
            "shading_angle_deg": (-15.0, 15.0, 5.0),
            "louver_spacing_m": (3.0, 4.0, 0.1),
            "perforated_panel_open_area_ratio": (0.75, 0.95, 0.05),
            "overhang_side_extension_m": (0.0, 1.0, 0.1),
            "overhang_gap_m": (0.0, 0.6, 0.05),
        },
    },
}
SEARCH_SPACE_VERSION = os.environ.get("STEP2_BENCHMARK_SPACE", "valid_range_v3")
if SEARCH_SPACE_VERSION not in SPACES:
    raise ValueError(f"STEP2_BENCHMARK_SPACE must be one of {sorted(SPACES)}, got {SEARCH_SPACE_VERSION!r}")
SPACE = SPACES[SEARCH_SPACE_VERSION]
WINDOW_TYPES = tuple(SPACE["windows"])
# Every shading type some window type offers, in the order of the full list.
SHADING_TYPES = tuple(name for name in ALL_SHADING
                      if any(name in window["shading_types"] for window in SPACE["windows"].values()))
RANGES = SPACE["ranges"]


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def quantize(value: float, lo: float, hi: float, step: float) -> float:
    raw = lo + clamp01(value) * (hi - lo)
    return round(round(raw / step) * step, 6)


def _range(value: float, name: str) -> float:
    lo, hi, step = RANGES[name]
    return quantize(value, lo, hi, step)


def category(value: float, choices: tuple[Any, ...]) -> Any:
    index = min(int(clamp01(value) * len(choices)), len(choices) - 1)
    return choices[index]


def random_genome(rng: random.Random) -> dict[str, Any]:
    return {
        "window_parameters": [rng.random() for _ in range(WINDOW_PARAMETER_GENES)],
        "facades": {orientation: {key: rng.random() for key in FACADE_KEYS} for orientation in ORIENTATIONS},
    }


def _radical_inverse(index: int, base: int) -> float:
    value = 0.0
    factor = 1.0 / base
    while index:
        index, digit = divmod(index, base)
        value += digit * factor
        factor /= base
    return value


def _primes(count: int) -> list[int]:
    primes: list[int] = []
    candidate = 2
    while len(primes) < count:
        if all(candidate % p for p in primes if p * p <= candidate):
            primes.append(candidate)
        candidate += 1
    return primes


def _continuous_values(index: int, count: int) -> list[float]:
    return [_radical_inverse(index + 1, prime) for prime in _primes(count)]


def space_filling_genome(index: int, seed: int = 0) -> dict[str, Any]:
    count = WINDOW_PARAMETER_GENES + len(ORIENTATIONS) * len(CONTINUOUS_FACADE_KEYS)
    values = iter(_continuous_values(index + seed * 97, count))
    genome: dict[str, Any] = {
        "window_parameters": [next(values) for _ in range(WINDOW_PARAMETER_GENES)],
        "facades": {},
    }
    for offset, orientation in enumerate(ORIENTATIONS):
        facade = {key: next(values) for key in CONTINUOUS_FACADE_KEYS}
        facade["window_type"] = ((index + offset + seed) % len(WINDOW_TYPES) + 0.5) / len(WINDOW_TYPES)
        facade["shading_type"] = ((index + offset * 2 + seed) % len(SHADING_TYPES) + 0.5) / len(SHADING_TYPES)
        facade["story_pattern"] = ((index * 5 + offset + seed) % len(STORY_PATTERNS) + 0.5) / len(STORY_PATTERNS)
        genome["facades"][orientation] = facade
    return genome


def normalize_genome(genome: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(genome)
    values = list(out.get("window_parameters", []))[:WINDOW_PARAMETER_GENES]
    out["window_parameters"] = [clamp01(v) for v in values + [0.5] * (WINDOW_PARAMETER_GENES - len(values))]
    # A v1 genome carried one building-level window type; apply it to every facade.
    legacy_window_type = out.pop("window_type", None)
    facades = out.setdefault("facades", {})
    for orientation in ORIENTATIONS:
        source = facades.setdefault(orientation, {})
        if legacy_window_type is not None and "window_type" not in source:
            source["window_type"] = legacy_window_type
        for key in FACADE_KEYS:
            source[key] = clamp01(source.get(key, 0.5))
        for key in list(source):
            if key not in FACADE_KEYS:
                del source[key]
    return out


def genome_dimension() -> int:
    """Length of the flattened normalized genome vector."""
    return WINDOW_PARAMETER_GENES + len(ORIENTATIONS) * len(FACADE_KEYS)


def genome_to_vector(genome: dict[str, Any]) -> list[float]:
    """Flatten a genome into a fixed-order vector in [0, 1] for surrogate models."""
    genes = normalize_genome(genome)
    vector = list(genes["window_parameters"])
    for orientation in ORIENTATIONS:
        facade = genes["facades"][orientation]
        vector.extend(facade[key] for key in FACADE_KEYS)
    return vector


def vector_to_genome(vector: list[float]) -> dict[str, Any]:
    """Inverse of :func:`genome_to_vector`; rebuilds a normalized genome."""
    if len(vector) != genome_dimension():
        raise ValueError(
            f"Expected genome vector of length {genome_dimension()}, got {len(vector)}."
        )
    cursor = iter(vector)
    genome = {"window_parameters": [next(cursor) for _ in range(WINDOW_PARAMETER_GENES)], "facades": {}}
    for orientation in ORIENTATIONS:
        genome["facades"][orientation] = {key: next(cursor) for key in FACADE_KEYS}
    return normalize_genome(genome)


def mutate_genome(
    genome: dict[str, Any],
    rng: random.Random,
    continuous_rate: float = 0.18,
    categorical_rate: float = 0.12,
    sigma: float = 0.12,
) -> dict[str, Any]:
    child = normalize_genome(genome)
    for i in range(WINDOW_PARAMETER_GENES):
        if rng.random() < continuous_rate:
            child["window_parameters"][i] = clamp01(child["window_parameters"][i] + rng.gauss(0.0, sigma))
    for orientation in ORIENTATIONS:
        facade = child["facades"][orientation]
        for key in CATEGORICAL_FACADE_KEYS:
            if rng.random() < categorical_rate:
                facade[key] = rng.random()
        for key in CONTINUOUS_FACADE_KEYS:
            if rng.random() < continuous_rate:
                facade[key] = clamp01(facade[key] + rng.gauss(0.0, sigma))
    return child


def crossover_genomes(a: dict[str, Any], b: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    left, right = normalize_genome(a), normalize_genome(b)
    child: dict[str, Any] = {"window_parameters": [], "facades": {}}
    for x, y in zip(left["window_parameters"], right["window_parameters"]):
        child["window_parameters"].append(rng.choice([x, y]) if rng.random() < 0.5 else (x + y) / 2.0)
    for orientation in ORIENTATIONS:
        la, rb = left["facades"][orientation], right["facades"][orientation]
        facade = {key: rng.choice([la[key], rb[key]]) for key in CATEGORICAL_FACADE_KEYS}
        for key in CONTINUOUS_FACADE_KEYS:
            facade[key] = rng.choice([la[key], rb[key]]) if rng.random() < 0.5 else (la[key] + rb[key]) / 2.0
        child["facades"][orientation] = facade
    return normalize_genome(child)


def _decode_window(window_type: str, shared: list[float], facade: dict[str, float]) -> dict[str, float]:
    genes = {"a": shared[0], "b": shared[1], "c": shared[2], **facade}
    return {
        name: quantize(genes[item[0]], *item[1:]) if isinstance(item, tuple) else item
        for name, item in SPACE["windows"][window_type]["parameters"].items()
    }


def gene_value(value: float, low: float, high: float) -> float:
    """Gene that decodes to `value` on a (low, high) range, clipped to the range."""
    return clamp01((value - low) / (high - low)) if high > low else 0.5


def category_gene(choice: Any, choices: tuple[Any, ...]) -> float:
    return (choices.index(choice) + 0.5) / len(choices)


def facade_genes(*, window_type: str, wwr: float, window_height_m: float, sill_height_m: float,
                 shading_type: str, depth_m: float, louver_spacing_m: float = 3.0,
                 overhang_gap_m: float = 0.0, overhang_side_extension_m: float = 0.0) -> dict[str, float]:
    """Facade genes of the active space for a design given in physical units (inverse of the decoder).

    Values outside the active ranges are clipped to them; angles are 0 degrees
    and the shading covers every story.
    """
    window = SPACE["windows"][window_type]

    def parameter(name: str, value: float) -> float:
        item = window["parameters"].get(name)
        return gene_value(value, item[1], item[2]) if isinstance(item, tuple) else 0.5

    low, high, _ = RANGES["shading_angle_deg"]
    return {
        "wwr": gene_value(wwr, *window["wwr"][:2]),
        "window_type": category_gene(window_type, WINDOW_TYPES),
        "window_height": parameter("window_height_m", window_height_m),
        "window_sill": parameter("sill_height_m", sill_height_m),
        "shading_type": category_gene(shading_type, window["shading_types"]),
        "depth": gene_value(depth_m, *RANGES["shading_depth_m"][:2]),
        "angle_primary": gene_value(0.0, low, high),
        "angle_secondary": gene_value(0.0, low, high),
        "secondary_parameter": (gene_value(louver_spacing_m, *RANGES["louver_spacing_m"][:2])
                                if shading_type == "louver" else 0.5),
        "story_pattern": category_gene(STORY_PATTERNS[0], STORY_PATTERNS),
        "overhang_extension": gene_value(overhang_side_extension_m, *RANGES["overhang_side_extension_m"][:2]),
        "overhang_gap": gene_value(overhang_gap_m, *RANGES["overhang_gap_m"][:2]),
    }


def decode_genome(
    genome: dict[str, Any],
    *,
    method: str,
    candidate_index: int,
    case_id: str,
) -> dict[str, Any]:
    genes = normalize_genome(genome)
    orientation_wwr: dict[str, float] = {}
    orientation_window_type: dict[str, str] = {}
    orientation_window_parameters: dict[str, dict[str, float]] = {}
    shading_depths: dict[str, float] = {}
    shading_systems: list[dict[str, Any]] = []
    decoded_facades: dict[str, Any] = {}

    for orientation in ORIENTATIONS:
        facade = genes["facades"][orientation]
        window_type = category(facade["window_type"], WINDOW_TYPES)
        shade_type = category(facade["shading_type"], SPACE["windows"][window_type]["shading_types"])
        wwr = quantize(facade["wwr"], *SPACE["windows"][window_type]["wwr"])
        depth = 0.0 if shade_type == "none" else _range(facade["depth"], "shading_depth_m")
        angle_primary = _range(facade["angle_primary"], "shading_angle_deg")
        angle_secondary = _range(facade["angle_secondary"], "shading_angle_deg")
        story_start, story_end = category(facade["story_pattern"], STORY_PATTERNS)
        orientation_wwr[orientation] = wwr
        orientation_window_type[orientation] = window_type
        orientation_window_parameters[orientation] = _decode_window(window_type, genes["window_parameters"], facade)
        shading_depths[orientation] = depth
        decoded: dict[str, Any] = {
            "window_type": window_type,
            "window_parameters": orientation_window_parameters[orientation],
            "type": shade_type,
            "depth_m": depth,
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
                system["overhang_gap_m"] = _range(facade["overhang_gap"], "overhang_gap_m")
            if shade_type in OVERHANG_TYPES:
                system["overhang_side_extension_m"] = _range(facade["overhang_extension"], "overhang_side_extension_m")
            if shade_type == "vertical":
                system["vertical_fin_angle_deg"] = angle_primary
            if shade_type == "combined":
                system["vertical_fin_angle_deg"] = angle_secondary
            if shade_type == "louver":
                system["louver_tilt_angle_deg"] = angle_primary
                system["louver_spacing_m"] = _range(facade["secondary_parameter"], "louver_spacing_m")
            if shade_type == "perforated_panel":
                # Only a facade-parallel perforated screen is implemented.
                system["perforated_panel_angle_deg"] = 0.0
                system["perforated_panel_open_area_ratio"] = _range(
                    facade["secondary_parameter"], "perforated_panel_open_area_ratio"
                )
            shading_systems.append(system)
            decoded.update({key: value for key, value in system.items() if key not in ("orientations",)})
        decoded_facades[orientation] = decoded

    active_types = [item["type"] for item in shading_systems]
    summary_shading_type = active_types[0] if active_types else "none"
    mean_depth = sum(shading_depths.values()) / len(shading_depths)
    building_window_type = orientation_window_type["north"]
    return {
        "description": f"{method} mixed-space candidate {candidate_index:03d}",
        "design_intent": (
            "Shared hierarchical mixed-variable benchmark candidate decoded by the common deterministic "
            "envelope decoder. Facade-specific window and shading choices use conditional parameter activation."
        ),
        "case_id": case_id,
        "wwr": round(sum(orientation_wwr.values()) / len(orientation_wwr), 3),
        "orientation_wwr": orientation_wwr,
        "window_type": building_window_type,
        "window_position_strategy": "orientation_by_facade",
        "window_parameters": orientation_window_parameters["north"],
        "orientation_window_type": orientation_window_type,
        "orientation_window_parameters": orientation_window_parameters,
        "shading_type": summary_shading_type,
        "horizontal_shade_depth_m": round(mean_depth, 3),
        "shading_depths_m": shading_depths,
        "shading_systems": shading_systems,
        "benchmark_method": method,
        "benchmark_candidate_index": candidate_index,
        "benchmark_search_space_version": SEARCH_SPACE_VERSION,
        "decoded_facade_design": decoded_facades,
        "genome": genes,
    }


def search_space_metadata() -> dict[str, Any]:
    return {
        "name": SEARCH_SPACE_VERSION,
        "representation": "fixed-length normalized genome with conditional type-dependent activation",
        "window_types_per_orientation": list(WINDOW_TYPES),
        "shading_types_per_orientation": list(SHADING_TYPES),
        "shading_types_by_window_type": {name: list(window["shading_types"]) for name, window in SPACE["windows"].items()},
        "story_patterns": [
            {"story_start": start, "story_end": end} for start, end in STORY_PATTERNS
        ],
        "facade_genes": list(FACADE_KEYS),
        "shared_window_parameter_genes": WINDOW_PARAMETER_GENES,
        "genome_dimension": genome_dimension(),
        "ranges": {name: [lo, hi] for name, (lo, hi, _) in RANGES.items()},
        "windows": {
            window_type: {
                "wwr": list(window["wwr"][:2]),
                "parameters": {name: (list(item[1:3]) if isinstance(item, tuple) else item)
                               for name, item in window["parameters"].items()},
            } for window_type, window in SPACE["windows"].items()
        },
        "base_categorical_combinations": int(math.pow(len(WINDOW_TYPES) * len(SHADING_TYPES) * len(STORY_PATTERNS), 4)),
        "fairness_rule": "All methods use this decoder, the same constraints, and the same proposal budget.",
    }


def genome_to_physical(genome: dict[str, Any]) -> dict[str, Any]:
    """The design a genome decodes to, in physical units, with only the active parameters."""
    genes = normalize_genome(genome)
    shared = {
        name: quantize(gene, *SPACE["windows"][window_type]["parameters"][parameter][1:])
        for gene, (name, window_type, parameter) in zip(genes["window_parameters"], SHARED_PHYSICAL)
    }
    facades: dict[str, Any] = {}
    for orientation in ORIENTATIONS:
        facade = genes["facades"][orientation]
        window_type = category(facade["window_type"], WINDOW_TYPES)
        window = SPACE["windows"][window_type]
        parameters = _decode_window(window_type, genes["window_parameters"], facade)
        entry: dict[str, Any] = {"window_type": window_type, "wwr": quantize(facade["wwr"], *window["wwr"])}
        entry.update({key: parameters[key] for key in ("window_height_m", "sill_height_m") if key in parameters})
        shading = category(facade["shading_type"], window["shading_types"])
        entry["shading_type"] = shading
        if shading != "none":
            entry["depth_m"] = _range(facade["depth"], "shading_depth_m")
            entry["story_range"] = category(facade["story_pattern"], STORY_PATTERN_NAMES)
            if shading in ("horizontal", "combined", "vertical", "louver"):
                entry["angle_deg"] = _range(facade["angle_primary"], "shading_angle_deg")
            if shading == "combined":
                entry["vertical_fin_angle_deg"] = _range(facade["angle_secondary"], "shading_angle_deg")
            if shading in ("horizontal", "combined"):
                entry["overhang_gap_m"] = _range(facade["overhang_gap"], "overhang_gap_m")
            if shading in OVERHANG_TYPES:
                entry["overhang_side_extension_m"] = _range(facade["overhang_extension"], "overhang_side_extension_m")
            if shading == "louver":
                entry["louver_spacing_m"] = _range(facade["secondary_parameter"], "louver_spacing_m")
            if shading == "perforated_panel":
                entry["perforated_panel_open_area_ratio"] = _range(
                    facade["secondary_parameter"], "perforated_panel_open_area_ratio")
        facades[orientation] = entry
    return {"shared": shared, "facades": facades}


def physical_to_genome(design: dict[str, Any]) -> dict[str, Any]:
    """Encode a design given in physical units into the shared genome (inverse of genome_to_physical).

    Values are clipped to the active ranges, so the encoded design is one that
    every genome-based method can also reach. An unknown category, or a shading
    type not offered on the facade's window type, raises ValueError.
    """
    if not isinstance(design, dict) or not isinstance(design.get("facades"), dict):
        raise ValueError("design.facades is missing")
    shared_input = design.get("shared") if isinstance(design.get("shared"), dict) else {}
    window_parameters = []
    for name, window_type, parameter in SHARED_PHYSICAL:
        item = SPACE["windows"][window_type]["parameters"][parameter]
        value = shared_input.get(name)
        window_parameters.append(0.5 if value is None else gene_value(float(value), item[1], item[2]))
    angle_low, angle_high, _ = RANGES["shading_angle_deg"]
    facades: dict[str, Any] = {}
    for orientation in ORIENTATIONS:
        facade = design["facades"].get(orientation)
        if not isinstance(facade, dict):
            raise ValueError(f"design.facades.{orientation} is missing")
        window_type = facade.get("window_type")
        if window_type not in WINDOW_TYPES:
            raise ValueError(f"{orientation}: window_type {window_type!r} is not one of {list(WINDOW_TYPES)}")
        window = SPACE["windows"][window_type]
        shading = facade.get("shading_type") or "none"
        if shading not in window["shading_types"]:
            raise ValueError(f"{orientation}: shading_type {shading!r} is not offered on a {window_type} facade")
        story = facade.get("story_range") or "all"
        if story not in STORY_PATTERN_NAMES:
            raise ValueError(f"{orientation}: story_range {story!r} is not one of {list(STORY_PATTERN_NAMES)}")

        def gene(key: str, low: float, high: float) -> float:
            return 0.5 if facade.get(key) is None else gene_value(float(facade[key]), low, high)

        def window_gene(key: str) -> float:
            item = window["parameters"].get(key)
            return gene(key, item[1], item[2]) if isinstance(item, tuple) else 0.5

        secondary = {"louver": "louver_spacing_m", "perforated_panel": "perforated_panel_open_area_ratio"}.get(shading)
        facades[orientation] = {
            "wwr": gene("wwr", *window["wwr"][:2]),
            "window_type": category_gene(window_type, WINDOW_TYPES),
            "window_height": window_gene("window_height_m"),
            "window_sill": window_gene("sill_height_m"),
            "shading_type": category_gene(shading, window["shading_types"]),
            "depth": gene("depth_m", *RANGES["shading_depth_m"][:2]),
            "angle_primary": gene("angle_deg", angle_low, angle_high),
            "angle_secondary": gene("vertical_fin_angle_deg", angle_low, angle_high),
            "secondary_parameter": gene(secondary, *RANGES[secondary][:2]) if secondary else 0.5,
            "story_pattern": category_gene(story, STORY_PATTERN_NAMES),
            "overhang_extension": gene("overhang_side_extension_m", *RANGES["overhang_side_extension_m"][:2]),
            "overhang_gap": gene("overhang_gap_m", *RANGES["overhang_gap_m"][:2]),
        }
    return normalize_genome({"window_parameters": window_parameters, "facades": facades})


PHYSICAL_FACADE_NUMBERS = (
    "wwr", "window_height_m", "sill_height_m", "depth_m", "angle_deg", "vertical_fin_angle_deg",
    "louver_spacing_m", "perforated_panel_open_area_ratio", "overhang_side_extension_m", "overhang_gap_m",
)


def physical_output_schema() -> dict[str, Any]:
    """JSON schema of a proposal in physical units for the active space."""
    facade = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "window_type": {"type": "string", "enum": list(WINDOW_TYPES)},
            "shading_type": {"type": "string", "enum": list(SHADING_TYPES)},
            "story_range": {"type": "string", "enum": list(STORY_PATTERN_NAMES)},
            **{name: {"type": "number"} for name in PHYSICAL_FACADE_NUMBERS},
        },
        "required": ["window_type", "wwr", "shading_type"],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "hypothesis": {"type": "string"},
            "design": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "shared": {"type": "object", "additionalProperties": False,
                               "properties": {name: {"type": "number"} for name, _, _ in SHARED_PHYSICAL},
                               "required": [name for name, _, _ in SHARED_PHYSICAL]},
                    "facades": {"type": "object", "additionalProperties": False,
                                "properties": {orientation: facade for orientation in ORIENTATIONS},
                                "required": list(ORIENTATIONS)},
                },
                "required": ["shared", "facades"],
            },
        },
        "required": ["hypothesis", "design"],
    }


def _bins(labels: list[str]) -> str:
    count = len(labels)
    return ", ".join(
        f"[{index / count:.3f}, {(index + 1) / count:.3f}{']' if index == count - 1 else ')'} {label}"
        for index, label in enumerate(labels))


def prompt_sections(representation: str = "genome") -> dict[str, str]:
    """Design-space and response-format text of the model prompt, written from the active space.

    `genome`: the model returns the normalized genes. `physical`: the model
    returns physical values, which the harness encodes into the same genome.
    """
    import json

    physical = representation == "physical"
    shared_names = {gene: name for gene, (name, _, _) in zip("abc", SHARED_PHYSICAL)}
    source = ({"a": f"shared setting `{shared_names['a']}`", "b": f"shared setting `{shared_names['b']}`",
               "c": f"shared setting `{shared_names['c']}`", "window_height": "facade value", "window_sill": "facade value"}
              if physical else
              {"a": "shared gene 1", "b": "shared gene 2", "c": "shared gene 3",
               "window_height": "gene `window_height`", "window_sill": "gene `window_sill`"})
    lines = ["The accessible variables are, for each of the four facades (north, east, south, west):", "",
             "1. Window type: " + ", ".join(f"`{name}`" for name in WINDOW_TYPES) + ".",
             "2. WWR and window parameters, by window type (the window width follows from the WWR):"]
    for window_type, window in SPACE["windows"].items():
        parts = [f"WWR `{window['wwr'][0]:.2f}-{window['wwr'][1]:.2f}`"]
        parts += [f"`{name}` `{item[1]:g}-{item[2]:g}` ({source[item[0]]})"
                  for name, item in window["parameters"].items() if isinstance(item, tuple)]
        lines.append(f"   - `{window_type}`: " + "; ".join(parts) + ".")
    if physical:
        lines.append("   The three shared settings apply to the whole building; a shared setting takes the same relative")
        lines.append("   position in the range of every parameter it controls.")
    ranges = RANGES
    shading_lists = {name: window["shading_types"] for name, window in SPACE["windows"].items()}
    uniform = len(set(shading_lists.values())) == 1
    lines.append("3. Shading type (`ledge`: one continuous board along each windowed wall at the slab above the story)"
                 + (": " + ", ".join(f"`{name}`" for name in SHADING_TYPES) + "." if uniform else ", by window type:"))
    if not uniform:
        lines += [f"   - `{window_type}`: " + ", ".join(f"`{name}`" for name in names) + "."
                  for window_type, names in shading_lists.items()]
    lines += [
        "4. Conditional shading parameters:",
        f"   - depth: `{ranges['shading_depth_m'][0]:g}-{ranges['shading_depth_m'][1]:g} m`",
        f"   - angles: `{ranges['shading_angle_deg'][0]:g} to {ranges['shading_angle_deg'][1]:+g} degrees`",
        f"   - louver spacing: `{ranges['louver_spacing_m'][0]:g}-{ranges['louver_spacing_m'][1]:g} m`",
        "   - perforated-panel open-area ratio: "
        f"`{ranges['perforated_panel_open_area_ratio'][0]:g}-{ranges['perforated_panel_open_area_ratio'][1]:g}`",
        "   - overhang side extension beyond each window side: "
        f"`{ranges['overhang_side_extension_m'][0]:g}-{ranges['overhang_side_extension_m'][1]:g} m` (horizontal, louver)",
        "   - overhang gap above the window head: "
        f"`{ranges['overhang_gap_m'][0]:g}-{ranges['overhang_gap_m'][1]:g} m` (horizontal)",
        "   - story range: " + ", ".join(STORY_PATTERN_NAMES),
    ]

    middle = normalize_genome({})  # every gene 0.5
    if physical:
        response = [
            "Return JSON only, with no Markdown and no prose. Give physical values. The harness clips each value",
            "to its range, rounds it to the decoder's step and encodes it into the shared genome, so the model",
            "it builds is one that every other method can also reach. Parameters that do not apply to the chosen",
            "window or shading type may be omitted and are ignored. An unknown window type, shading type or story",
            "range, or a shading type that the facade's window type does not offer, is an invalid proposal.",
            "",
            "`hypothesis` states in one or two sentences what the proposal changes and why.",
            "",
            "The example shows the format with the mid-range value of every variable:",
            "",
            "```json",
            json.dumps({"hypothesis": "Concise rationale for this proposal.", "design": genome_to_physical(middle)}, indent=2),
            "```",
        ]
    else:
        response = [
            "Return JSON only, with no Markdown and no prose. Genome values are numbers in",
            "`[0,1]`; the shared decoder maps them to the physical categories and ranges.",
            "",
            "The categorical mapping is exact and left-closed/right-open (except 1.0 is in the final bin):",
            "",
            "- `window_type`: " + _bins(list(WINDOW_TYPES)) + ".",
            *(["- `shading_type`: " + _bins(list(SHADING_TYPES)) + "."] if uniform else
              [f"- `shading_type` on a `{window_type}` facade: " + _bins(list(names)) + "."
               for window_type, names in shading_lists.items()]),
            "- `story_pattern`: " + _bins(list(STORY_PATTERN_NAMES)) + ".",
            "- Continuous genes use `physical = low + gene * (high-low)` with the ranges above, followed by the",
            "  shared decoder's quantization. The `wwr` range is the one of the facade's window type, and",
            "  `window_parameters` holds the three shared genes. Inactive conditional genes do not affect the",
            "  phenotype.",
            "",
            "`hypothesis` states in one or two sentences what the proposal changes and why.",
            "",
            "```json",
            "{",
            '  "hypothesis": "Concise rationale for this proposal.",',
            '  "genome": {',
            '    "window_parameters": ' + json.dumps(middle["window_parameters"]) + ",",
            '    "facades": {',
            ",\n".join(f'      "{orientation}": ' + json.dumps(middle["facades"][orientation])
                       for orientation in ORIENTATIONS),
            "    }",
            "  }",
            "}",
            "```",
        ]
    return {"design_space": "\n".join(lines), "response_format": "\n".join(response)}
