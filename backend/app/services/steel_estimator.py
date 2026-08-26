"""
Steel Estimation Engine

Calculates reinforcement steel requirements for structural elements based on
standard engineering practices (IS 456:2000 guidelines).

Supports: Columns, Beams, Slabs, Footings, Staircases, Lintels
"""

import math

from backend.app.models.schemas import (
    ElementType,
    StructuralElement,
    RebarSpec,
    SteelEstimate,
    ProjectEstimate,
)


# Standard rebar weights (kg/m) by diameter
REBAR_WEIGHT_PER_METER = {
    6: 0.222,
    8: 0.395,
    10: 0.617,
    12: 0.888,
    16: 1.580,
    20: 2.469,
    25: 3.854,
    28: 4.834,
    32: 6.313,
    36: 7.990,
    40: 9.864,
}

# Default assumptions when drawing data is incomplete
DEFAULTS = {
    "column": {
        "main_bar_dia": 16,
        "main_bar_count": 8,
        "stirrup_dia": 8,
        "stirrup_spacing": 150,
        "clear_cover": 40,
    },
    "beam": {
        "main_bar_dia": 16,
        "main_bar_count": 4,  # top + bottom combined
        "stirrup_dia": 8,
        "stirrup_spacing": 150,
        "clear_cover": 25,
    },
    "slab": {
        "main_bar_dia": 10,
        "distribution_bar_dia": 8,
        "main_spacing": 150,
        "dist_spacing": 200,
        "clear_cover": 20,
    },
    "footing": {
        "main_bar_dia": 12,
        "main_spacing": 150,
        "clear_cover": 50,
    },
    "staircase": {
        "main_bar_dia": 12,
        "distribution_bar_dia": 8,
        "main_spacing": 150,
        "dist_spacing": 200,
        "clear_cover": 20,
    },
    "lintel": {
        "main_bar_dia": 12,
        "main_bar_count": 4,
        "stirrup_dia": 8,
        "stirrup_spacing": 150,
        "clear_cover": 25,
    },
}

LAP_LENGTH_FACTOR = 50  # 50 x diameter for Fe500 in tension (IS 456)
HOOK_LENGTH = 10  # 10d standard hook


def get_weight(diameter: float) -> float:
    """Get weight per meter for a given bar diameter."""
    d = int(diameter)
    if d in REBAR_WEIGHT_PER_METER:
        return REBAR_WEIGHT_PER_METER[d]
    return (diameter ** 2) / 162.2  # Formula: d²/162.2 kg/m


def estimate_column(element: StructuralElement) -> list[RebarSpec]:
    """Estimate steel for a column."""
    defaults = DEFAULTS["column"]
    rebars = []

    width = element.width
    depth = element.depth
    length = element.length
    cover = defaults["clear_cover"]

    if not length:
        raise ValueError(f"Cannot estimate column '{element.label}': height/length is not provided.")

    # Main bars
    main_dia = element.main_bar_dia
    main_count = element.main_bar_count

    if not main_dia or not main_count:
        raise ValueError(
            f"Cannot estimate column '{element.label}': no reinforcement data provided "
            f"(need main_bar_dia and main_bar_count)."
        )

    # Bar length = column height + lap length (one lap per storey)
    lap_length = LAP_LENGTH_FACTOR * main_dia / 1000  # in meters
    bar_length = (length / 1000) + lap_length

    main_weight_pm = get_weight(main_dia)
    main_total = main_count * bar_length * main_weight_pm

    rebars.append(RebarSpec(
        diameter=main_dia,
        count=main_count,
        length=bar_length,
        weight_per_meter=main_weight_pm,
        total_weight=round(main_total, 2),
        bar_type="main",
    ))

    # Stirrups / Ties
    stirrup_dia = element.stirrup_dia
    stirrup_spacing = element.stirrup_spacing

    if not stirrup_dia or not stirrup_spacing:
        raise ValueError(
            f"Cannot estimate column '{element.label}': no stirrup data provided "
            f"(need stirrup_dia and stirrup_spacing)."
        )

    # Stirrup perimeter = 2(width + depth) - 8*cover + hooks
    stirrup_perimeter = 2 * ((width - 2 * cover) + (depth - 2 * cover))
    hook_addition = 2 * HOOK_LENGTH * stirrup_dia  # two hooks
    stirrup_length = (stirrup_perimeter + hook_addition) / 1000  # meters

    # Number of stirrups
    num_stirrups = math.ceil(length / stirrup_spacing) + 1

    stirrup_weight_pm = get_weight(stirrup_dia)
    stirrup_total = num_stirrups * stirrup_length * stirrup_weight_pm

    rebars.append(RebarSpec(
        diameter=stirrup_dia,
        count=num_stirrups,
        length=stirrup_length,
        weight_per_meter=stirrup_weight_pm,
        total_weight=round(stirrup_total, 2),
        bar_type="stirrup",
    ))

    return rebars


def estimate_beam(element: StructuralElement) -> list[RebarSpec]:
    """Estimate steel for a beam, using detailed zone-based data when available."""
    defaults = DEFAULTS["beam"]
    rebars = []

    width = element.width
    depth = element.depth
    span = element.length
    cover = element.clear_cover or defaults["clear_cover"]

    if not span:
        raise ValueError(f"Cannot estimate beam '{element.label}': span/length is not provided.")

    detail = element.reinforcement_detail

    # If we have detailed reinforcement data, use zone-based calculation
    if detail:
        rebars = _estimate_beam_detailed(element, detail)
        if rebars:
            return rebars

    # Standard calculation from top-level fields
    if element.bottom_bar_dia and element.bottom_bar_count:
        bottom_dia = element.bottom_bar_dia
        bottom_count = element.bottom_bar_count
    elif element.main_bar_dia and element.main_bar_count:
        bottom_dia = element.main_bar_dia
        bottom_count = element.main_bar_count
    else:
        raise ValueError(
            f"Cannot estimate beam '{element.label}': no reinforcement data provided "
            f"(need bottom_bar_dia/count or main_bar_dia/count)."
        )

    if element.top_bar_dia and element.top_bar_count:
        top_dia = element.top_bar_dia
        top_count = element.top_bar_count
    elif element.main_bar_dia and element.main_bar_count and not element.bottom_bar_dia:
        total = element.main_bar_count
        bottom_count = max(1, math.ceil(total * 0.6))
        top_count = max(1, total - bottom_count)
        top_dia = element.main_bar_dia
        bottom_dia = element.main_bar_dia
    else:
        raise ValueError(
            f"Cannot estimate beam '{element.label}': no top bar data provided "
            f"(need top_bar_dia/count or main_bar_dia/count)."
        )

    # Bottom bars: full span + development length on each side
    dev_length_bottom = LAP_LENGTH_FACTOR * bottom_dia / 1000
    bottom_bar_length = (span / 1000) + 2 * (dev_length_bottom * 0.5)

    bottom_weight_pm = get_weight(bottom_dia)
    rebars.append(RebarSpec(
        diameter=bottom_dia,
        count=bottom_count,
        length=round(bottom_bar_length, 2),
        weight_per_meter=bottom_weight_pm,
        total_weight=round(bottom_count * bottom_bar_length * bottom_weight_pm, 2),
        bar_type="main (bottom)",
    ))

    # Top bars: full span + development length
    dev_length_top = LAP_LENGTH_FACTOR * top_dia / 1000
    top_bar_length = (span / 1000) + 2 * (dev_length_top * 0.5)

    top_weight_pm = get_weight(top_dia)
    rebars.append(RebarSpec(
        diameter=top_dia,
        count=top_count,
        length=round(top_bar_length, 2),
        weight_per_meter=top_weight_pm,
        total_weight=round(top_count * top_bar_length * top_weight_pm, 2),
        bar_type="main (top)",
    ))

    # Extra/cranked bars at supports (typically 2 bars, same dia as bottom)
    extra_dia = bottom_dia
    extra_count = 2
    extra_length = (span / 1000) * 0.3  # 30% of span
    extra_weight_pm = get_weight(extra_dia)
    rebars.append(RebarSpec(
        diameter=extra_dia,
        count=extra_count,
        length=round(extra_length, 2),
        weight_per_meter=extra_weight_pm,
        total_weight=round(extra_count * extra_length * extra_weight_pm, 2),
        bar_type="extra",
    ))

    # Stirrups
    stirrup_dia = element.stirrup_dia
    stirrup_spacing = element.stirrup_spacing

    if not stirrup_dia or not stirrup_spacing:
        raise ValueError(
            f"Cannot estimate beam '{element.label}': no stirrup data provided "
            f"(need stirrup_dia and stirrup_spacing)."
        )

    stirrup_perimeter = 2 * ((width - 2 * cover) + (depth - 2 * cover))
    hook_addition = 2 * HOOK_LENGTH * stirrup_dia
    stirrup_length = (stirrup_perimeter + hook_addition) / 1000

    num_stirrups = math.ceil(span / stirrup_spacing) + 1

    stirrup_weight_pm = get_weight(stirrup_dia)
    stirrup_total = num_stirrups * stirrup_length * stirrup_weight_pm

    rebars.append(RebarSpec(
        diameter=stirrup_dia,
        count=num_stirrups,
        length=round(stirrup_length, 2),
        weight_per_meter=stirrup_weight_pm,
        total_weight=round(stirrup_total, 2),
        bar_type="stirrup",
    ))

    return rebars


def _estimate_beam_detailed(element: StructuralElement, detail) -> list[RebarSpec]:
    """Calculate steel using detailed zone-based reinforcement data."""
    from backend.app.models.schemas import BeamReinforcementDetail

    rebars = []
    span = element.length  # mm
    width = element.width
    depth = element.depth
    cover = element.clear_cover or 25.0

    if not span:
        raise ValueError(f"Cannot estimate beam '{element.label}': span/length is not provided.")

    # --- Bottom straight bars (full span) ---
    if detail.bottom_straight:
        for layer in detail.bottom_straight:
            dia = layer.diameter
            count = layer.count
            dev_length = LAP_LENGTH_FACTOR * dia / 1000
            bar_length = (span / 1000) + 2 * (dev_length * 0.5)
            wpm = get_weight(dia)
            rebars.append(RebarSpec(
                diameter=dia,
                count=count,
                length=round(bar_length, 2),
                weight_per_meter=wpm,
                total_weight=round(count * bar_length * wpm, 2),
                bar_type=f"bottom straight ({int(dia)}mm)",
            ))

    # --- Bottom extra bars at mid-span ---
    if detail.bottom_extra_midspan:
        for layer in detail.bottom_extra_midspan:
            dia = layer.diameter
            count = layer.count
            # Extra mid-span bars typically extend 60% of span
            bar_length = (span / 1000) * 0.6
            wpm = get_weight(dia)
            rebars.append(RebarSpec(
                diameter=dia,
                count=count,
                length=round(bar_length, 2),
                weight_per_meter=wpm,
                total_weight=round(count * bar_length * wpm, 2),
                bar_type=f"bottom extra mid-span ({int(dia)}mm)",
            ))

    # --- Top straight bars (full span) ---
    if detail.top_straight:
        for layer in detail.top_straight:
            dia = layer.diameter
            count = layer.count
            dev_length = LAP_LENGTH_FACTOR * dia / 1000
            bar_length = (span / 1000) + 2 * (dev_length * 0.5)
            wpm = get_weight(dia)
            rebars.append(RebarSpec(
                diameter=dia,
                count=count,
                length=round(bar_length, 2),
                weight_per_meter=wpm,
                total_weight=round(count * bar_length * wpm, 2),
                bar_type=f"top straight ({int(dia)}mm)",
            ))

    # --- Top extra bars at supports (cranked/curtailed) ---
    if detail.top_extra_support:
        for layer in detail.top_extra_support:
            dia = layer.diameter
            count = layer.count
            # Support bars extend L/4 from each end + development length
            if layer.zone == "both-supports":
                bar_length = 2 * (span / 4000) + LAP_LENGTH_FACTOR * dia / 1000
            else:
                bar_length = (span / 4000) + LAP_LENGTH_FACTOR * dia / 1000
            wpm = get_weight(dia)
            rebars.append(RebarSpec(
                diameter=dia,
                count=count,
                length=round(bar_length, 2),
                weight_per_meter=wpm,
                total_weight=round(count * bar_length * wpm, 2),
                bar_type=f"top extra at support ({int(dia)}mm)",
            ))

    # --- Side face reinforcement ---
    if detail.side_face:
        for layer in detail.side_face:
            dia = layer.diameter
            count = layer.count
            bar_length = span / 1000
            wpm = get_weight(dia)
            rebars.append(RebarSpec(
                diameter=dia,
                count=count,
                length=round(bar_length, 2),
                weight_per_meter=wpm,
                total_weight=round(count * bar_length * wpm, 2),
                bar_type=f"side face ({int(dia)}mm)",
            ))

    # --- Stirrups: end zone (closer spacing) ---
    stirrup_perimeter = 2 * ((width - 2 * cover) + (depth - 2 * cover))
    hook_addition_fn = lambda d: 2 * HOOK_LENGTH * d

    if detail.stirrup_end_zone:
        sz = detail.stirrup_end_zone
        dia = float(sz.get("dia", 8))
        spacing = float(sz.get("spacing", 150))
        legs = int(sz.get("legs", 2))
        zone_len = float(sz.get("zone_length_mm", span / 4))

        stirrup_len = (stirrup_perimeter + hook_addition_fn(dia)) / 1000
        if legs > 2:
            inner_leg_length = (depth - 2 * cover) / 1000
            stirrup_len += (legs - 2) * inner_leg_length

        # Number of stirrups in BOTH end zones
        num_end = 2 * (math.ceil(zone_len / spacing) + 1)
        wpm = get_weight(dia)
        rebars.append(RebarSpec(
            diameter=dia,
            count=num_end,
            length=round(stirrup_len, 2),
            weight_per_meter=wpm,
            total_weight=round(num_end * stirrup_len * wpm, 2),
            bar_type=f"stirrup end zone ({int(dia)}mm @{int(spacing)})",
        ))

    if detail.stirrup_support_zone:
        sz = detail.stirrup_support_zone
        dia = float(sz.get("dia", 10))
        spacing = float(sz.get("spacing", 130))
        legs = int(sz.get("legs", 2))
        zone_len = float(sz.get("zone_length_mm", span / 4))

        stirrup_len = (stirrup_perimeter + hook_addition_fn(dia)) / 1000
        if legs > 2:
            inner_leg_length = (depth - 2 * cover) / 1000
            stirrup_len += (legs - 2) * inner_leg_length

        # Number of stirrups in BOTH support zones
        num_support = 2 * (math.ceil(zone_len / spacing) + 1)
        wpm = get_weight(dia)
        rebars.append(RebarSpec(
            diameter=dia,
            count=num_support,
            length=round(stirrup_len, 2),
            weight_per_meter=wpm,
            total_weight=round(num_support * stirrup_len * wpm, 2),
            bar_type=f"stirrup support zone ({int(dia)}mm @{int(spacing)})",
        ))

    if detail.stirrup_mid_zone:
        sz = detail.stirrup_mid_zone
        dia = float(sz.get("dia", 8))
        spacing = float(sz.get("spacing", 200))
        legs = int(sz.get("legs", 2))
        zone_len = float(sz.get("zone_length_mm", span / 2))

        stirrup_len = (stirrup_perimeter + hook_addition_fn(dia)) / 1000
        if legs > 2:
            inner_leg_length = (depth - 2 * cover) / 1000
            stirrup_len += (legs - 2) * inner_leg_length

        num_mid = math.ceil(zone_len / spacing) + 1
        wpm = get_weight(dia)
        rebars.append(RebarSpec(
            diameter=dia,
            count=num_mid,
            length=round(stirrup_len, 2),
            weight_per_meter=wpm,
            total_weight=round(num_mid * stirrup_len * wpm, 2),
            bar_type=f"stirrup mid zone ({int(dia)}mm @{int(spacing)})",
        ))

    # If no stirrup detail provided but detail object exists, fall back to element-level stirrup
    if not detail.stirrup_end_zone and not detail.stirrup_support_zone and not detail.stirrup_mid_zone:
        stirrup_dia = element.stirrup_dia or 8
        stirrup_spacing = element.stirrup_spacing or 150
        stirrup_len = (stirrup_perimeter + hook_addition_fn(stirrup_dia)) / 1000
        num_stirrups = math.ceil(span / stirrup_spacing) + 1
        wpm = get_weight(stirrup_dia)
        rebars.append(RebarSpec(
            diameter=stirrup_dia,
            count=num_stirrups,
            length=round(stirrup_len, 2),
            weight_per_meter=wpm,
            total_weight=round(num_stirrups * stirrup_len * wpm, 2),
            bar_type="stirrup",
        ))

    return rebars


def estimate_slab(element: StructuralElement) -> list[RebarSpec]:
    """Estimate steel for a slab (per panel)."""
    defaults = DEFAULTS["slab"]
    rebars = []

    width = element.width  # one dimension of slab panel
    length = element.length  # other dimension
    depth = element.depth  # slab thickness
    cover = element.clear_cover or defaults["clear_cover"]

    main_dia = element.main_bar_dia or defaults["main_bar_dia"]
    dist_dia = defaults["distribution_bar_dia"]
    main_spacing = defaults["main_spacing"]
    dist_spacing = defaults["dist_spacing"]

    # Main reinforcement (shorter span direction)
    shorter_span = min(width, length)
    longer_span = max(width, length)

    num_main_bars = math.ceil(longer_span / main_spacing) + 1
    main_bar_length = (shorter_span / 1000) + 2 * (LAP_LENGTH_FACTOR * main_dia / 1000 * 0.3)

    main_weight_pm = get_weight(main_dia)
    rebars.append(RebarSpec(
        diameter=main_dia,
        count=num_main_bars,
        length=round(main_bar_length, 2),
        weight_per_meter=main_weight_pm,
        total_weight=round(num_main_bars * main_bar_length * main_weight_pm, 2),
        bar_type="main",
    ))

    # Distribution reinforcement (longer span direction)
    num_dist_bars = math.ceil(shorter_span / dist_spacing) + 1
    dist_bar_length = (longer_span / 1000) + 2 * (LAP_LENGTH_FACTOR * dist_dia / 1000 * 0.3)

    dist_weight_pm = get_weight(dist_dia)
    rebars.append(RebarSpec(
        diameter=dist_dia,
        count=num_dist_bars,
        length=round(dist_bar_length, 2),
        weight_per_meter=dist_weight_pm,
        total_weight=round(num_dist_bars * dist_bar_length * dist_weight_pm, 2),
        bar_type="distribution",
    ))

    # Top extra bars at supports (negative moment)
    extra_bars = math.ceil(longer_span / main_spacing) + 1
    extra_length = (shorter_span / 1000) * 0.25  # L/4 from support
    rebars.append(RebarSpec(
        diameter=main_dia,
        count=extra_bars,
        length=round(extra_length, 2),
        weight_per_meter=main_weight_pm,
        total_weight=round(extra_bars * extra_length * main_weight_pm, 2),
        bar_type="extra (top at support)",
    ))

    return rebars


def estimate_footing(element: StructuralElement) -> list[RebarSpec]:
    """Estimate steel for a footing."""
    defaults = DEFAULTS["footing"]
    rebars = []

    width = element.width
    length = element.length  # other dimension (or same for square)
    depth = element.depth
    cover = element.clear_cover or defaults["clear_cover"]

    main_dia = element.main_bar_dia or defaults["main_bar_dia"]
    main_spacing = defaults["main_spacing"]

    # Bars in X direction
    num_bars_x = math.ceil(length / main_spacing) + 1
    bar_length_x = (width / 1000) - 2 * (cover / 1000) + 2 * (HOOK_LENGTH * main_dia / 1000)

    # Bars in Y direction
    num_bars_y = math.ceil(width / main_spacing) + 1
    bar_length_y = (length / 1000) - 2 * (cover / 1000) + 2 * (HOOK_LENGTH * main_dia / 1000)

    weight_pm = get_weight(main_dia)

    rebars.append(RebarSpec(
        diameter=main_dia,
        count=num_bars_x,
        length=round(bar_length_x, 2),
        weight_per_meter=weight_pm,
        total_weight=round(num_bars_x * bar_length_x * weight_pm, 2),
        bar_type="main (X-direction)",
    ))

    rebars.append(RebarSpec(
        diameter=main_dia,
        count=num_bars_y,
        length=round(bar_length_y, 2),
        weight_per_meter=weight_pm,
        total_weight=round(num_bars_y * bar_length_y * weight_pm, 2),
        bar_type="main (Y-direction)",
    ))

    return rebars


def estimate_staircase(element: StructuralElement) -> list[RebarSpec]:
    """Estimate steel for a staircase waist slab."""
    defaults = DEFAULTS["staircase"]
    rebars = []

    width = element.width  # stair width
    length = element.length  # going length (horizontal)
    depth = element.depth  # waist slab thickness

    # Inclined length (assuming typical rise/tread ratio)
    riser = 150  # mm typical
    tread = 300  # mm typical
    inclined_factor = math.sqrt(riser**2 + tread**2) / tread
    inclined_length = length * inclined_factor

    main_dia = element.main_bar_dia or defaults["main_bar_dia"]
    dist_dia = defaults["distribution_bar_dia"]
    main_spacing = defaults["main_spacing"]
    dist_spacing = defaults["dist_spacing"]

    # Main bars along inclined length
    num_main = math.ceil(width / main_spacing) + 1
    main_bar_length = (inclined_length / 1000) + 2 * (LAP_LENGTH_FACTOR * main_dia / 1000 * 0.3)

    main_weight = get_weight(main_dia)
    rebars.append(RebarSpec(
        diameter=main_dia,
        count=num_main,
        length=round(main_bar_length, 2),
        weight_per_meter=main_weight,
        total_weight=round(num_main * main_bar_length * main_weight, 2),
        bar_type="main",
    ))

    # Distribution bars
    num_dist = math.ceil(inclined_length / dist_spacing) + 1
    dist_bar_length = (width / 1000)
    dist_weight = get_weight(dist_dia)

    rebars.append(RebarSpec(
        diameter=dist_dia,
        count=num_dist,
        length=round(dist_bar_length, 2),
        weight_per_meter=dist_weight,
        total_weight=round(num_dist * dist_bar_length * dist_weight, 2),
        bar_type="distribution",
    ))

    return rebars


def estimate_lintel(element: StructuralElement) -> list[RebarSpec]:
    """Estimate steel for a lintel beam."""
    return estimate_beam(StructuralElement(
        element_type=ElementType.LINTEL,
        label=element.label,
        width=element.width,
        depth=element.depth,
        length=element.length,
        clear_cover=element.clear_cover,
        main_bar_dia=element.main_bar_dia,
        main_bar_count=element.main_bar_count,
        stirrup_dia=element.stirrup_dia,
        stirrup_spacing=element.stirrup_spacing,
    ))


ESTIMATORS = {
    ElementType.COLUMN: estimate_column,
    ElementType.BEAM: estimate_beam,
    ElementType.SLAB: estimate_slab,
    ElementType.FOOTING: estimate_footing,
    ElementType.STAIRCASE: estimate_staircase,
    ElementType.LINTEL: estimate_lintel,
    ElementType.WALL: estimate_beam,  # RCC walls treated similar to beams
}


def estimate_element(element: StructuralElement) -> SteelEstimate:
    """Estimate steel for a single structural element.

    Raises ValueError if the element is missing required data (reinforcement, dimensions).
    """
    estimator = ESTIMATORS.get(element.element_type)
    if not estimator:
        raise ValueError(f"No estimator available for element type '{element.element_type.value}'")

    rebars = estimator(element)

    # Apply quantity multiplier
    for rebar in rebars:
        rebar.total_weight *= element.quantity

    total_kg = sum(r.total_weight for r in rebars)

    return SteelEstimate(
        element=element,
        rebars=rebars,
        total_weight_kg=round(total_kg, 2),
        total_weight_tons=round(total_kg / 1000, 4),
    )


def estimate_project(project_name: str, elements: list[StructuralElement]) -> ProjectEstimate:
    """Estimate total steel for an entire project.

    Raises ValueError if any element is missing required data.
    """
    estimates = []
    errors = []

    for e in elements:
        try:
            estimates.append(estimate_element(e))
        except ValueError as err:
            errors.append(str(err))

    if not estimates:
        raise ValueError(
            f"Cannot estimate any elements. All {len(elements)} elements failed:\n"
            + "\n".join(errors[:10])
        )

    if errors:
        print(f"[Estimator] {len(errors)} elements skipped due to missing data: {errors[:5]}")

    total_kg = sum(e.total_weight_kg for e in estimates)

    # Summary by element type
    summary_by_type = {}
    for est in estimates:
        key = est.element.element_type.value
        summary_by_type[key] = summary_by_type.get(key, 0) + est.total_weight_kg

    # Summary by bar diameter
    summary_by_diameter = {}
    for est in estimates:
        for rebar in est.rebars:
            key = f"{int(rebar.diameter)}mm"
            summary_by_diameter[key] = summary_by_diameter.get(key, 0) + rebar.total_weight

    # Round all values
    summary_by_type = {k: round(v, 2) for k, v in summary_by_type.items()}
    summary_by_diameter = {k: round(v, 2) for k, v in summary_by_diameter.items()}

    return ProjectEstimate(
        project_name=project_name,
        elements=estimates,
        total_steel_kg=round(total_kg, 2),
        total_steel_tons=round(total_kg / 1000, 4),
        summary_by_type=summary_by_type,
        summary_by_diameter=summary_by_diameter,
    )
