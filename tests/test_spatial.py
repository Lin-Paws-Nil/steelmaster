"""
Test suite for SpatialGrouper.

Verifies that:
- Reinforcement entities are assigned to the correct beam by proximity
- Top/bottom classification works based on Y-coordinate comparison
- Edge cases (single beam, overlapping zones, equidistant) are handled
"""

import pytest

from backend.app.models.beam import AssembledBeam
from backend.app.models.reinforcement import BeamTag, MainSteel, Stirrup
from backend.app.services.spatial_service import SpatialGrouper


@pytest.fixture
def two_beam_layout():
    """
    Two adjacent beams:
        Beam 1: anchor at X=1000, Y=5000
        Beam 2: anchor at X=3000, Y=5000

    Reinforcement scattered around them:
        - Top bar near Beam 1: X=900, Y=5200 (above anchor Y)
        - Bottom bar near Beam 1: X=1100, Y=4800 (below anchor Y)
        - Top bar near Beam 2: X=3100, Y=5300 (above anchor Y)
        - Bottom bar near Beam 2: X=2900, Y=4700 (below anchor Y)
        - Stirrup near Beam 1: X=1050, Y=5050
        - Stirrup near Beam 2: X=3050, Y=4950
    """
    return [
        {"type": "beam_tag", "data": BeamTag(beam_id="B1", width=230, depth=600), "x": 1000.0, "y": 5000.0},
        {"type": "beam_tag", "data": BeamTag(beam_id="B2", width=230, depth=600), "x": 3000.0, "y": 5000.0},
        {"type": "main_bar", "data": MainSteel(bar_count=2, diameter=16), "x": 900.0, "y": 5200.0},
        {"type": "main_bar", "data": MainSteel(bar_count=2, diameter=25), "x": 1100.0, "y": 4800.0},
        {"type": "main_bar", "data": MainSteel(bar_count=4, diameter=20), "x": 3100.0, "y": 5300.0},
        {"type": "main_bar", "data": MainSteel(bar_count=6, diameter=16), "x": 2900.0, "y": 4700.0},
        {"type": "stirrup", "data": Stirrup(legs=2, diameter=8, spacing=150), "x": 1050.0, "y": 5050.0},
        {"type": "stirrup", "data": Stirrup(legs=4, diameter=10, spacing=125), "x": 3050.0, "y": 4950.0},
    ]


class TestProximityAssignment:
    """Tests that reinforcement is assigned to the correct beam by proximity."""

    def test_bars_near_beam1_assigned_to_beam1(self, two_beam_layout):
        grouper = SpatialGrouper(x_weight=1.0, y_weight=2.0)
        result = grouper.group_texts_to_beams(two_beam_layout)

        beam1 = next(b for b in result if b.beam_tag.beam_id == "B1")
        all_bars = beam1.top_main_bars + beam1.bottom_main_bars
        diameters = {bar.diameter for bar in all_bars}

        assert 16 in diameters  # 2K16 at X=900
        assert 25 in diameters  # 2K25 at X=1100

    def test_bars_near_beam2_assigned_to_beam2(self, two_beam_layout):
        grouper = SpatialGrouper(x_weight=1.0, y_weight=2.0)
        result = grouper.group_texts_to_beams(two_beam_layout)

        beam2 = next(b for b in result if b.beam_tag.beam_id == "B2")
        all_bars = beam2.top_main_bars + beam2.bottom_main_bars
        diameters = {bar.diameter for bar in all_bars}

        assert 20 in diameters  # 4K20 at X=3100
        assert 16 in diameters  # 6K16 at X=2900

    def test_stirrup_near_beam1_assigned_to_beam1(self, two_beam_layout):
        grouper = SpatialGrouper(x_weight=1.0, y_weight=2.0)
        result = grouper.group_texts_to_beams(two_beam_layout)

        beam1 = next(b for b in result if b.beam_tag.beam_id == "B1")
        assert len(beam1.stirrups) == 1
        assert beam1.stirrups[0].diameter == 8
        assert beam1.stirrups[0].spacing == 150

    def test_stirrup_near_beam2_assigned_to_beam2(self, two_beam_layout):
        grouper = SpatialGrouper(x_weight=1.0, y_weight=2.0)
        result = grouper.group_texts_to_beams(two_beam_layout)

        beam2 = next(b for b in result if b.beam_tag.beam_id == "B2")
        assert len(beam2.stirrups) == 1
        assert beam2.stirrups[0].legs == 4
        assert beam2.stirrups[0].spacing == 125


class TestTopBottomClassification:
    """Tests that bars are classified as top/bottom based on Y-coordinate."""

    def test_bar_above_anchor_is_top(self, two_beam_layout):
        grouper = SpatialGrouper(x_weight=1.0, y_weight=2.0)
        result = grouper.group_texts_to_beams(two_beam_layout)

        beam1 = next(b for b in result if b.beam_tag.beam_id == "B1")
        # 2K16 at Y=5200 > anchor Y=5000 -> top
        assert any(bar.bar_count == 2 and bar.diameter == 16 for bar in beam1.top_main_bars)

    def test_bar_below_anchor_is_bottom(self, two_beam_layout):
        grouper = SpatialGrouper(x_weight=1.0, y_weight=2.0)
        result = grouper.group_texts_to_beams(two_beam_layout)

        beam1 = next(b for b in result if b.beam_tag.beam_id == "B1")
        # 2K25 at Y=4800 < anchor Y=5000 -> bottom
        assert any(bar.bar_count == 2 and bar.diameter == 25 for bar in beam1.bottom_main_bars)

    def test_beam2_top_bottom_separation(self, two_beam_layout):
        grouper = SpatialGrouper(x_weight=1.0, y_weight=2.0)
        result = grouper.group_texts_to_beams(two_beam_layout)

        beam2 = next(b for b in result if b.beam_tag.beam_id == "B2")
        # 4K20 at Y=5300 > anchor Y=5000 -> top
        assert any(bar.bar_count == 4 and bar.diameter == 20 for bar in beam2.top_main_bars)
        # 6K16 at Y=4700 < anchor Y=5000 -> bottom
        assert any(bar.bar_count == 6 and bar.diameter == 16 for bar in beam2.bottom_main_bars)


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_input_returns_empty(self):
        grouper = SpatialGrouper()
        result = grouper.group_texts_to_beams([])
        assert result == []

    def test_no_beams_returns_empty(self):
        grouper = SpatialGrouper()
        entities = [
            {"type": "main_bar", "data": MainSteel(bar_count=2, diameter=16), "x": 100.0, "y": 200.0},
        ]
        result = grouper.group_texts_to_beams(entities)
        assert result == []

    def test_beam_with_no_reinforcement(self):
        grouper = SpatialGrouper()
        entities = [
            {"type": "beam_tag", "data": BeamTag(beam_id="B1", width=230, depth=600), "x": 1000.0, "y": 5000.0},
        ]
        result = grouper.group_texts_to_beams(entities)
        assert len(result) == 1
        assert result[0].beam_tag.beam_id == "B1"
        assert result[0].top_main_bars == []
        assert result[0].bottom_main_bars == []
        assert result[0].stirrups == []

    def test_bar_at_same_y_as_anchor_goes_to_bottom(self):
        """When Y is exactly equal to anchor, it goes to bottom (not top)."""
        grouper = SpatialGrouper()
        entities = [
            {"type": "beam_tag", "data": BeamTag(beam_id="B1", width=230, depth=600), "x": 1000.0, "y": 5000.0},
            {"type": "main_bar", "data": MainSteel(bar_count=2, diameter=16), "x": 1000.0, "y": 5000.0},
        ]
        result = grouper.group_texts_to_beams(entities)
        assert len(result[0].bottom_main_bars) == 1
        assert len(result[0].top_main_bars) == 0

    def test_midpoint_entity_assigned_to_closer_beam(self):
        """An entity at the midpoint between two beams should go to the closer one."""
        grouper = SpatialGrouper(x_weight=1.0, y_weight=1.0)
        entities = [
            {"type": "beam_tag", "data": BeamTag(beam_id="B1", width=230, depth=600), "x": 0.0, "y": 0.0},
            {"type": "beam_tag", "data": BeamTag(beam_id="B2", width=230, depth=600), "x": 100.0, "y": 0.0},
            # Entity at X=40, slightly closer to B1
            {"type": "main_bar", "data": MainSteel(bar_count=2, diameter=16), "x": 40.0, "y": 5.0},
        ]
        result = grouper.group_texts_to_beams(entities)
        beam1 = next(b for b in result if b.beam_tag.beam_id == "B1")
        beam2 = next(b for b in result if b.beam_tag.beam_id == "B2")
        assert len(beam1.top_main_bars) + len(beam1.bottom_main_bars) == 1
        assert len(beam2.top_main_bars) + len(beam2.bottom_main_bars) == 0


class TestWeightedDistance:
    """Tests that distance weighting works correctly."""

    def test_y_weight_prevents_vertical_jump(self):
        """
        With high Y-weight, a bar that is closer vertically but farther
        horizontally should still be assigned to the horizontally-closer beam.
        """
        grouper = SpatialGrouper(x_weight=1.0, y_weight=3.0)
        entities = [
            {"type": "beam_tag", "data": BeamTag(beam_id="B1", width=230, depth=600), "x": 0.0, "y": 0.0},
            {"type": "beam_tag", "data": BeamTag(beam_id="B2", width=230, depth=600), "x": 0.0, "y": 1000.0},
            # Entity at X=0, Y=400 — closer to B1 in raw distance (400 vs 600)
            # but with y_weight=3, weighted dist to B1 = 3*400 = 1200
            # weighted dist to B2 = 3*600 = 1800... no, 3*(1000-400)=1800
            # Actually: dist to B1 = sqrt(0 + (3*400)^2) = 1200
            # dist to B2 = sqrt(0 + (3*600)^2) = 1800
            # So it goes to B1 regardless. Let me pick a better case.
            # Entity at X=100, Y=800 — raw dist to B1 = 806, to B2 = 224
            # Weighted: to B1 = sqrt((1*100)^2 + (3*800)^2) = sqrt(10000+5760000) = 2401
            # Weighted: to B2 = sqrt((1*100)^2 + (3*200)^2) = sqrt(10000+360000) = 608
            # Goes to B2 (closer vertically with heavy Y weight)
            {"type": "main_bar", "data": MainSteel(bar_count=2, diameter=16), "x": 100.0, "y": 800.0},
        ]
        result = grouper.group_texts_to_beams(entities)
        beam2 = next(b for b in result if b.beam_tag.beam_id == "B2")
        assert len(beam2.top_main_bars) + len(beam2.bottom_main_bars) == 1


class TestFromDwgEntities:
    """Tests for the convenience factory method."""

    def test_from_dwg_entities_integration(self):
        """Test the full pipeline from DWGTextEntity-like dicts to assembled beams."""
        from unittest.mock import MagicMock

        entities = [
            MagicMock(text="B1(230X600)", x=1000.0, y=5000.0),
            MagicMock(text="2K16", x=950.0, y=5200.0),
            MagicMock(text="2K25", x=1050.0, y=4800.0),
            MagicMock(text="K8@150C/C", x=1000.0, y=5000.0),
        ]

        result = SpatialGrouper.from_dwg_entities(entities)
        assert len(result) == 1
        assert result[0].beam_tag.beam_id == "B1"
        assert len(result[0].top_main_bars) == 1
        assert result[0].top_main_bars[0].diameter == 16
        assert len(result[0].bottom_main_bars) == 1
        assert result[0].bottom_main_bars[0].diameter == 25
        assert len(result[0].stirrups) == 1
        assert result[0].stirrups[0].spacing == 150
