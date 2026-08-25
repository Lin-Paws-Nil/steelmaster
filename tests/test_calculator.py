"""
Test suite for BBSCalculator.

Verifies:
- Unit weight formula: D² / 162
- Main bar cutting length with anchorage and bend deductions
- Stirrup cutting length with cover deduction, hooks, and bend deductions
- Stirrup count: (span / spacing) + 1
- Full BBS generation for a complete assembled beam
"""

import math

import pytest

from backend.app.models.beam import AssembledBeam
from backend.app.models.bbs import BBSRow
from backend.app.models.reinforcement import BeamTag, MainSteel, Stirrup
from backend.app.services.calculation_service import BBSCalculator


@pytest.fixture
def calculator():
    """BBSCalculator with default 25mm cover."""
    return BBSCalculator(clear_cover=25.0)


@pytest.fixture
def sample_beam():
    """
    Mock beam B1:
        - Dimensions: 230mm wide × 600mm deep
        - Top bars: 2K16
        - Stirrups: K8@150C/C
    Span: 3000mm
    """
    return AssembledBeam(
        beam_tag=BeamTag(beam_id="B1", width=230, depth=600),
        top_main_bars=[MainSteel(bar_count=2, diameter=16)],
        bottom_main_bars=[],
        stirrups=[Stirrup(legs=2, diameter=8, spacing=150)],
    )


class TestUnitWeight:
    """Tests for the unit weight formula: D² / 162."""

    def test_16mm_bar(self, calculator):
        expected = (16 ** 2) / 162.0  # 1.5802 kg/m
        assert calculator.unit_weight(16) == pytest.approx(expected)

    def test_8mm_bar(self, calculator):
        expected = (8 ** 2) / 162.0  # 0.3951 kg/m
        assert calculator.unit_weight(8) == pytest.approx(expected)

    def test_25mm_bar(self, calculator):
        expected = (25 ** 2) / 162.0  # 3.8580 kg/m
        assert calculator.unit_weight(25) == pytest.approx(expected)

    def test_12mm_bar(self, calculator):
        expected = (12 ** 2) / 162.0  # 0.8889 kg/m
        assert calculator.unit_weight(12) == pytest.approx(expected)


class TestMainBarCuttingLength:
    """Tests for main bar cutting length calculation."""

    def test_formula_16mm_3000mm_span(self, calculator):
        """
        span=3000, D=16
        cutting = 3000 + 2*50*16 - 2*2*16
                = 3000 + 1600 - 64
                = 4536 mm
        """
        result = calculator.calc_main_bar_cutting_length(3000, 16)
        assert result == pytest.approx(4536.0)

    def test_formula_25mm_5000mm_span(self, calculator):
        """
        span=5000, D=25
        cutting = 5000 + 2*50*25 - 2*2*25
                = 5000 + 2500 - 100
                = 7400 mm
        """
        result = calculator.calc_main_bar_cutting_length(5000, 25)
        assert result == pytest.approx(7400.0)

    def test_formula_20mm_4000mm_span(self, calculator):
        """
        span=4000, D=20
        cutting = 4000 + 2*50*20 - 2*2*20
                = 4000 + 2000 - 80
                = 5920 mm
        """
        result = calculator.calc_main_bar_cutting_length(4000, 20)
        assert result == pytest.approx(5920.0)


class TestStirrupCuttingLength:
    """Tests for stirrup cutting length with cover deduction."""

    def test_230x600_beam_8mm_stirrup(self, calculator):
        """
        beam 230×600, cover=25, D=8
        core_width = 230 - 2*25 = 180
        core_depth = 600 - 2*25 = 550
        perimeter = 2*(180 + 550) = 1460
        hooks = 2*10*8 = 160 (> 2*75=150, so use 160)
        bend_deductions = 5*2*8 = 80
        cutting = 1460 + 160 - 80 = 1540 mm
        """
        result = calculator.calc_stirrup_cutting_length(230, 600, 8)
        assert result == pytest.approx(1540.0)

    def test_cover_subtracted_from_all_sides(self, calculator):
        """Verify that cover is subtracted from width and depth (both sides)."""
        # For a 300x300 beam with 25mm cover:
        # core = 300-50 = 250 on each dimension
        result = calculator.calc_stirrup_cutting_length(300, 300, 8)
        core_width = 300 - 50  # 250
        core_depth = 300 - 50  # 250
        perimeter = 2 * (core_width + core_depth)  # 1000
        hooks = 2 * 10 * 8  # 160
        bends = 5 * 2 * 8   # 80
        expected = perimeter + hooks - bends  # 1080
        assert result == pytest.approx(expected)

    def test_min_hook_length_for_small_diameter(self, calculator):
        """For 6mm bars, 10D=60mm < 75mm minimum, so 75mm per hook is used."""
        result = calculator.calc_stirrup_cutting_length(230, 600, 6)
        core_width = 230 - 50   # 180
        core_depth = 600 - 50   # 550
        perimeter = 2 * (180 + 550)  # 1460
        hooks = 2 * 75  # minimum 75mm per hook = 150 (since 10*6=60 < 75)
        bends = 5 * 2 * 6  # 60
        expected = perimeter + hooks - bends  # 1460 + 150 - 60 = 1550
        assert result == pytest.approx(expected)


class TestStirrupCount:
    """Tests for stirrup count calculation: (span / spacing) + 1."""

    def test_3000mm_span_150mm_spacing(self, calculator):
        """(3000 / 150) + 1 = 21"""
        result = calculator.calc_stirrup_count(3000, 150)
        assert result == 21

    def test_5000mm_span_200mm_spacing(self, calculator):
        """(5000 / 200) + 1 = 26"""
        result = calculator.calc_stirrup_count(5000, 200)
        assert result == 26

    def test_4000mm_span_125mm_spacing(self, calculator):
        """(4000 / 125) + 1 = 33"""
        result = calculator.calc_stirrup_count(4000, 125)
        assert result == 33


class TestGenerateBeamBBS:
    """Integration tests for full BBS generation."""

    def test_returns_correct_number_of_rows(self, calculator, sample_beam):
        rows = calculator.generate_beam_bbs(sample_beam, span_length=3000)
        # 1 top bar group + 0 bottom bar groups + 1 stirrup = 2 rows
        assert len(rows) == 2

    def test_beam_id_propagated(self, calculator, sample_beam):
        rows = calculator.generate_beam_bbs(sample_beam, span_length=3000)
        for row in rows:
            assert row.beam_id == "B1"

    def test_top_bar_weight_calculation(self, calculator, sample_beam):
        """
        Top bar: 2K16, span=3000mm
        cutting = 3000 + 100*16 - 4*16 = 4536mm = 4.536m
        weight = 2 * 4.536 * (16²/162) = 2 * 4.536 * 1.5802 = 14.334 kg
        """
        rows = calculator.generate_beam_bbs(sample_beam, span_length=3000)
        top_row = next(r for r in rows if r.bar_type == "Top Main")

        cutting_m = 4536 / 1000.0
        expected_weight = 2 * cutting_m * ((16 ** 2) / 162.0)

        assert top_row.diameter == 16
        assert top_row.count == 2
        assert top_row.cutting_length_m == pytest.approx(cutting_m, rel=1e-3)
        assert top_row.total_weight_kg == pytest.approx(expected_weight, rel=1e-2)

    def test_stirrup_row_values(self, calculator, sample_beam):
        """
        Stirrup: K8@150C/C, beam 230×600, span=3000mm
        count = (3000/150) + 1 = 21
        cutting = 1540mm = 1.540m
        weight = 21 * 1.540 * (8²/162) = 21 * 1.540 * 0.3951 = 12.773 kg
        """
        rows = calculator.generate_beam_bbs(sample_beam, span_length=3000)
        stirrup_row = next(r for r in rows if r.bar_type == "Stirrup")

        assert stirrup_row.diameter == 8
        assert stirrup_row.count == 21
        assert stirrup_row.shape_code == "Closed Rectangular Link"
        assert stirrup_row.cutting_length_m == pytest.approx(1.540, rel=1e-3)

        expected_weight = 21 * 1.540 * ((8 ** 2) / 162.0)
        assert stirrup_row.total_weight_kg == pytest.approx(expected_weight, rel=1e-2)

    def test_beam_with_multiple_bar_groups(self, calculator):
        """Test beam with both top and bottom bars."""
        beam = AssembledBeam(
            beam_tag=BeamTag(beam_id="B5", width=300, depth=450),
            top_main_bars=[MainSteel(bar_count=2, diameter=16)],
            bottom_main_bars=[
                MainSteel(bar_count=2, diameter=20),
                MainSteel(bar_count=2, diameter=16),
            ],
            stirrups=[Stirrup(legs=2, diameter=8, spacing=200)],
        )
        rows = calculator.generate_beam_bbs(beam, span_length=4500)
        assert len(rows) == 4  # 1 top + 2 bottom + 1 stirrup

        top_rows = [r for r in rows if r.bar_type == "Top Main"]
        bot_rows = [r for r in rows if r.bar_type == "Bottom Main"]
        stir_rows = [r for r in rows if r.bar_type == "Stirrup"]

        assert len(top_rows) == 1
        assert len(bot_rows) == 2
        assert len(stir_rows) == 1

    def test_empty_beam_returns_empty(self, calculator):
        """Beam with no reinforcement returns no rows."""
        beam = AssembledBeam(
            beam_tag=BeamTag(beam_id="B99", width=230, depth=450),
        )
        rows = calculator.generate_beam_bbs(beam, span_length=3000)
        assert rows == []


class TestProjectBBS:
    """Tests for project-level DataFrame generation."""

    def test_dataframe_columns(self, calculator, sample_beam):
        df = calculator.generate_project_bbs(
            [sample_beam],
            span_lengths={"B1": 3000},
        )
        expected_cols = {"beam_id", "bar_type", "diameter", "count",
                         "shape_code", "cutting_length_m", "total_weight_kg"}
        assert set(df.columns) == expected_cols

    def test_empty_beams_returns_empty_df(self, calculator):
        df = calculator.generate_project_bbs([], span_lengths={})
        assert df.empty


class TestEdgeCases:
    """Tests for boundary conditions and error handling."""

    def test_zero_spacing_raises_error(self, calculator):
        with pytest.raises(ValueError, match="spacing must be positive"):
            calculator.calc_stirrup_count(3000, 0)

    def test_negative_spacing_raises_error(self, calculator):
        with pytest.raises(ValueError, match="spacing must be positive"):
            calculator.calc_stirrup_count(3000, -100)

    def test_negative_span_raises_error(self, calculator, sample_beam):
        with pytest.raises(ValueError, match="span_length must be positive"):
            calculator.generate_beam_bbs(sample_beam, span_length=-1000)

    def test_zero_span_raises_error(self, calculator, sample_beam):
        with pytest.raises(ValueError, match="span_length must be positive"):
            calculator.generate_beam_bbs(sample_beam, span_length=0)

    def test_shorthand_stirrup_defaults_to_8mm(self, calculator):
        """Stirrup with diameter=None (shorthand @150C/C) uses 8mm default."""
        beam = AssembledBeam(
            beam_tag=BeamTag(beam_id="B1", width=230, depth=600),
            stirrups=[Stirrup(legs=2, diameter=None, spacing=150)],
        )
        rows = calculator.generate_beam_bbs(beam, span_length=3000)
        assert len(rows) == 1
        assert rows[0].diameter == 8

    def test_non_exact_span_spacing_uses_ceil(self, calculator):
        """
        span=3100, spacing=150 -> ceil(3100/150) + 1 = ceil(20.67) + 1 = 22
        (not int(20.67) + 1 = 21)
        """
        result = calculator.calc_stirrup_count(3100, 150)
        assert result == 22  # ceil(20.67) + 1

    def test_exact_divisor_same_as_before(self, calculator):
        """span=3000, spacing=150 -> ceil(20.0) + 1 = 21 (unchanged)."""
        result = calculator.calc_stirrup_count(3000, 150)
        assert result == 21
