"""
BBS Calculation Engine

Computes cutting lengths, bar counts, and steel weights for reinforcement
bars and stirrups to generate a Bar Bending Schedule.

Engineering Constants (IS 456:2000 / SP-16):
    - Unit weight: D² / 162 (kg/m), where D is diameter in mm
    - Clear cover: 25mm for beams (configurable)
    - Stirrup hooks: 10D per hook (min 75mm), 2 hooks per closed stirrup
    - Main bar anchorage: 50D L-bend into column at each end
    - Bend deduction: 2D per 90° bend
"""

import math
from typing import List

import pandas as pd

from backend.app.models.beam import AssembledBeam
from backend.app.models.bbs import BBSRow
from backend.app.models.reinforcement import MainSteel, Stirrup


# Configurable engineering constants
CLEAR_COVER_MM = 25.0
ANCHORAGE_FACTOR = 50  # 50D L-bend into column
HOOK_FACTOR = 10       # 10D per hook (stirrup)
MIN_HOOK_LENGTH_MM = 75.0
BEND_DEDUCTION_FACTOR = 2  # 2D per 90° bend
STIRRUP_BENDS = 5      # number of 90° bends in a closed rectangular stirrup
STIRRUP_HOOKS = 2      # number of hooks in a closed stirrup


class BBSCalculator:
    """
    Computes Bar Bending Schedule rows for an assembled beam.

    All internal calculations use mm. Final cutting lengths are converted to meters.
    """

    def __init__(self, clear_cover: float = CLEAR_COVER_MM):
        """
        Args:
            clear_cover: Clear cover in mm. Default 25mm for beams.
        """
        self.clear_cover = clear_cover

    @staticmethod
    def unit_weight(diameter: int) -> float:
        """
        Calculate unit weight of a bar (kg per meter).

        Formula: D² / 162
        """
        return (diameter ** 2) / 162.0

    def calc_main_bar_cutting_length(self, span_mm: float, diameter: int) -> float:
        """
        Calculate cutting length for a main bar (top or bottom).

        Formula:
            cutting_length = span + 2 * (anchorage) - 2 * (bend_deduction)
                           = span + 2 * 50D - 2 * 2D
                           = span + 100D - 4D
                           = span + 96D

        Args:
            span_mm: Beam span/clear length in mm.
            diameter: Bar diameter in mm.

        Returns:
            Cutting length in mm.
        """
        anchorage = 2 * ANCHORAGE_FACTOR * diameter  # 2 ends × 50D
        bend_deduction = 2 * BEND_DEDUCTION_FACTOR * diameter  # 2 bends × 2D
        return span_mm + anchorage - bend_deduction

    def calc_stirrup_cutting_length(self, beam_width: int, beam_depth: int, diameter: int) -> float:
        """
        Calculate cutting length for a closed rectangular stirrup.

        Formula:
            core_width = beam_width - 2 * cover
            core_depth = beam_depth - 2 * cover
            perimeter = 2 * (core_width + core_depth)
            hooks = 2 * 10D (one hook at each end of the stirrup bar)
            bend_deductions = 5 bends × 2D per bend
            cutting_length = perimeter + hooks - bend_deductions
                           = 2(cw + cd) + 20D - 10D
                           = 2(cw + cd) + 24D - 10D  (using 24D for hooks per spec)

        Spec formula: 2*(cw + cd) + 24D - 5*2D = 2*(cw+cd) + 14D

        Args:
            beam_width: Beam width in mm.
            beam_depth: Beam depth in mm.
            diameter: Stirrup bar diameter in mm.

        Returns:
            Cutting length in mm.
        """
        core_width = beam_width - (2 * self.clear_cover)
        core_depth = beam_depth - (2 * self.clear_cover)
        perimeter = 2 * (core_width + core_depth)
        hook_length = STIRRUP_HOOKS * HOOK_FACTOR * diameter  # 2 hooks × 10D = 20D
        # Ensure minimum hook length
        hook_length = max(hook_length, STIRRUP_HOOKS * MIN_HOOK_LENGTH_MM)
        bend_deductions = STIRRUP_BENDS * BEND_DEDUCTION_FACTOR * diameter  # 5 × 2D = 10D
        return perimeter + hook_length - bend_deductions

    def calc_stirrup_count(self, span_mm: float, spacing: int) -> int:
        """
        Calculate number of stirrups along the beam span.

        Formula: ceil(span / spacing) + 1

        Uses ceiling to ensure full span coverage for non-exact divisors.

        Args:
            span_mm: Beam span in mm.
            spacing: Stirrup c/c spacing in mm.

        Returns:
            Number of stirrups (integer).

        Raises:
            ValueError: If spacing is zero or negative.
        """
        if spacing <= 0:
            raise ValueError(f"Stirrup spacing must be positive, got {spacing}")
        return math.ceil(span_mm / spacing) + 1

    def generate_beam_bbs(
        self,
        beam: AssembledBeam,
        span_length: float,
    ) -> List[BBSRow]:
        """
        Generate all BBS rows for a single beam.

        Args:
            beam: An AssembledBeam with tag, top bars, bottom bars, and stirrups.
            span_length: Clear span length of the beam in mm. Must be positive.

        Returns:
            List of BBSRow objects (one per bar group).

        Raises:
            ValueError: If span_length is zero or negative.
        """
        if span_length <= 0:
            raise ValueError(f"span_length must be positive, got {span_length}")

        rows: List[BBSRow] = []
        beam_id = beam.beam_tag.beam_id
        width = beam.beam_tag.width
        depth = beam.beam_tag.depth

        # Top main bars
        for bar in beam.top_main_bars:
            cutting_mm = self.calc_main_bar_cutting_length(span_length, bar.diameter)
            cutting_m = cutting_mm / 1000.0
            weight = bar.bar_count * cutting_m * self.unit_weight(bar.diameter)

            rows.append(BBSRow(
                beam_id=beam_id,
                bar_type="Top Main",
                diameter=bar.diameter,
                count=bar.bar_count,
                shape_code="Straight with L-bend",
                cutting_length_m=round(cutting_m, 4),
                total_weight_kg=round(weight, 3),
            ))

        # Bottom main bars
        for bar in beam.bottom_main_bars:
            cutting_mm = self.calc_main_bar_cutting_length(span_length, bar.diameter)
            cutting_m = cutting_mm / 1000.0
            weight = bar.bar_count * cutting_m * self.unit_weight(bar.diameter)

            rows.append(BBSRow(
                beam_id=beam_id,
                bar_type="Bottom Main",
                diameter=bar.diameter,
                count=bar.bar_count,
                shape_code="Straight with L-bend",
                cutting_length_m=round(cutting_m, 4),
                total_weight_kg=round(weight, 3),
            ))

        # Stirrups
        for stirrup in beam.stirrups:
            dia = stirrup.diameter or 8  # default 8mm if shorthand
            cutting_mm = self.calc_stirrup_cutting_length(width, depth, dia)
            cutting_m = cutting_mm / 1000.0
            count = self.calc_stirrup_count(span_length, stirrup.spacing)
            weight = count * cutting_m * self.unit_weight(dia)

            rows.append(BBSRow(
                beam_id=beam_id,
                bar_type="Stirrup",
                diameter=dia,
                count=count,
                shape_code="Closed Rectangular Link",
                cutting_length_m=round(cutting_m, 4),
                total_weight_kg=round(weight, 3),
            ))

        return rows

    def generate_project_bbs(
        self,
        beams: List[AssembledBeam],
        span_lengths: dict[str, float],
    ) -> pd.DataFrame:
        """
        Generate a full project BBS as a pandas DataFrame.

        Args:
            beams: List of AssembledBeam objects.
            span_lengths: Dict mapping beam_id -> span in mm.
                          Missing beams default to 3000mm.

        Returns:
            DataFrame with columns matching BBSRow fields.
        """
        all_rows: List[BBSRow] = []

        for beam in beams:
            span = span_lengths.get(beam.beam_tag.beam_id, 3000.0)
            rows = self.generate_beam_bbs(beam, span)
            all_rows.extend(rows)

        if not all_rows:
            return pd.DataFrame(columns=[
                "beam_id", "bar_type", "diameter", "count",
                "shape_code", "cutting_length_m", "total_weight_kg",
            ])

        df = pd.DataFrame([row.model_dump() for row in all_rows])
        return df
