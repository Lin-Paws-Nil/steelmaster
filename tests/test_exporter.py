"""
Test suite for BBSExporter.

Verifies:
- Excel file generation without errors
- Correct sheet names ("BBS Details", "Weight Summary")
- Column headers match engineering nomenclature
- Weight summary correctly aggregates by diameter
- File is valid and readable by pandas
"""

import os
import tempfile

import pandas as pd
import pytest

from backend.app.models.bbs import BBSRow
from backend.app.services.export_service import BBSExporter


@pytest.fixture
def sample_bbs_data() -> list[BBSRow]:
    """
    5 BBSRow objects with mixed diameters:
        - 2 rows with 8mm (stirrups): weights 12.5 + 8.3 = 20.8 kg
        - 2 rows with 16mm (main bars): weights 25.0 + 18.5 = 43.5 kg
        - 1 row with 20mm (main bar): weight 35.0 kg
    """
    return [
        BBSRow(
            beam_id="B1", bar_type="Stirrup", diameter=8,
            count=21, shape_code="Closed Rectangular Link",
            cutting_length_m=1.540, total_weight_kg=12.5,
        ),
        BBSRow(
            beam_id="B2", bar_type="Stirrup", diameter=8,
            count=16, shape_code="Closed Rectangular Link",
            cutting_length_m=1.320, total_weight_kg=8.3,
        ),
        BBSRow(
            beam_id="B1", bar_type="Top Main", diameter=16,
            count=2, shape_code="Straight with L-bend",
            cutting_length_m=4.536, total_weight_kg=25.0,
        ),
        BBSRow(
            beam_id="B2", bar_type="Bottom Main", diameter=16,
            count=4, shape_code="Straight with L-bend",
            cutting_length_m=3.920, total_weight_kg=18.5,
        ),
        BBSRow(
            beam_id="B1", bar_type="Bottom Main", diameter=20,
            count=2, shape_code="Straight with L-bend",
            cutting_length_m=5.920, total_weight_kg=35.0,
        ),
    ]


@pytest.fixture
def exporter():
    return BBSExporter()


class TestExcelGeneration:
    """Tests that a valid Excel file is generated."""

    def test_generates_file_without_error(self, exporter, sample_bbs_data):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            result = exporter.generate_excel(sample_bbs_data, output_path=output_path)
            assert result == output_path
            assert os.path.exists(output_path)
            assert os.path.getsize(output_path) > 0
        finally:
            os.unlink(output_path)

    def test_generates_bytes_without_error(self, exporter, sample_bbs_data):
        result = exporter.generate_excel_bytes(sample_bbs_data)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_file_is_valid_xlsx(self, exporter, sample_bbs_data):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            exporter.generate_excel(sample_bbs_data, output_path=output_path)
            # Should be readable by pandas without error
            df = pd.read_excel(output_path, sheet_name="BBS Details")
            assert not df.empty
        finally:
            os.unlink(output_path)

    def test_empty_data_raises_error(self, exporter):
        with pytest.raises(ValueError, match="empty data"):
            exporter.generate_excel([], output_path="/tmp/test.xlsx")


class TestSheetStructure:
    """Tests for correct sheet names and column headers."""

    def test_has_bbs_details_sheet(self, exporter, sample_bbs_data):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            exporter.generate_excel(sample_bbs_data, output_path=output_path)
            xl = pd.ExcelFile(output_path)
            assert "BBS Details" in xl.sheet_names
        finally:
            os.unlink(output_path)

    def test_has_weight_summary_sheet(self, exporter, sample_bbs_data):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            exporter.generate_excel(sample_bbs_data, output_path=output_path)
            xl = pd.ExcelFile(output_path)
            assert "Weight Summary" in xl.sheet_names
        finally:
            os.unlink(output_path)

    def test_bbs_details_columns(self, exporter, sample_bbs_data):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            exporter.generate_excel(sample_bbs_data, output_path=output_path)
            df = pd.read_excel(output_path, sheet_name="BBS Details")
            expected_cols = {
                "Beam ID", "Bar Type", "Dia (mm)", "No. of Bars",
                "Shape", "Cut Length (m)", "Total Weight (kg)",
            }
            assert set(df.columns) == expected_cols
        finally:
            os.unlink(output_path)

    def test_bbs_details_row_count(self, exporter, sample_bbs_data):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            exporter.generate_excel(sample_bbs_data, output_path=output_path)
            df = pd.read_excel(output_path, sheet_name="BBS Details")
            assert len(df) == 5  # 5 input rows
        finally:
            os.unlink(output_path)


class TestWeightSummaryAggregation:
    """Tests that the Weight Summary sheet correctly aggregates by diameter."""

    def test_8mm_total_weight(self, exporter, sample_bbs_data):
        """8mm bars: 12.5 + 8.3 = 20.8 kg"""
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            exporter.generate_excel(sample_bbs_data, output_path=output_path)
            df = pd.read_excel(output_path, sheet_name="Weight Summary")

            row_8mm = df[df["Dia (mm)"] == 8]
            assert len(row_8mm) == 1
            assert row_8mm.iloc[0]["Total Weight (kg)"] == pytest.approx(20.8, rel=1e-2)
        finally:
            os.unlink(output_path)

    def test_16mm_total_weight(self, exporter, sample_bbs_data):
        """16mm bars: 25.0 + 18.5 = 43.5 kg"""
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            exporter.generate_excel(sample_bbs_data, output_path=output_path)
            df = pd.read_excel(output_path, sheet_name="Weight Summary")

            row_16mm = df[df["Dia (mm)"] == 16]
            assert len(row_16mm) == 1
            assert row_16mm.iloc[0]["Total Weight (kg)"] == pytest.approx(43.5, rel=1e-2)
        finally:
            os.unlink(output_path)

    def test_20mm_total_weight(self, exporter, sample_bbs_data):
        """20mm bars: 35.0 kg (single entry)"""
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            exporter.generate_excel(sample_bbs_data, output_path=output_path)
            df = pd.read_excel(output_path, sheet_name="Weight Summary")

            row_20mm = df[df["Dia (mm)"] == 20]
            assert len(row_20mm) == 1
            assert row_20mm.iloc[0]["Total Weight (kg)"] == pytest.approx(35.0, rel=1e-2)
        finally:
            os.unlink(output_path)

    def test_grand_total_row(self, exporter, sample_bbs_data):
        """Last row should be TOTAL: 20.8 + 43.5 + 35.0 = 99.3 kg"""
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            exporter.generate_excel(sample_bbs_data, output_path=output_path)
            df = pd.read_excel(output_path, sheet_name="Weight Summary")

            last_row = df.iloc[-1]
            assert str(last_row["Dia (mm)"]) == "TOTAL"
            assert last_row["Total Weight (kg)"] == pytest.approx(99.3, rel=1e-2)
        finally:
            os.unlink(output_path)

    def test_bar_count_aggregation(self, exporter, sample_bbs_data):
        """8mm bars count: 21 + 16 = 37"""
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = f.name

        try:
            exporter.generate_excel(sample_bbs_data, output_path=output_path)
            df = pd.read_excel(output_path, sheet_name="Weight Summary")

            row_8mm = df[df["Dia (mm)"] == 8]
            assert row_8mm.iloc[0]["Total No. of Bars"] == 37
        finally:
            os.unlink(output_path)
