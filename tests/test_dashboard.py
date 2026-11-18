"""Dashboard tests on a tiny warehouse built by the fixture pipeline: filters, empty selection, rates."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
import app as dashboard  # noqa: E402

from tests.test_pipeline import data_dir  # noqa: E402,F401  (pytest fixture)
import run_pipeline  # noqa: E402


@pytest.fixture
def warehouse(data_dir, monkeypatch):
    run_pipeline.run(full=False, as_of=None, data_dir=data_dir)
    monkeypatch.setenv("WAREHOUSE_PATH", str(data_dir / "warehouse" / "warehouse.db"))
    return data_dir


def test_header_rate_is_late_orders_over_orders(warehouse):
    numbers = dashboard.header_numbers(None, None, False)
    assert numbers["orders"] > 0
    assert numbers["rate"] == pytest.approx(numbers["late"] / numbers["orders"])


def test_state_filter_changes_the_numbers(warehouse):
    everything = dashboard.header_numbers(None, None, False)["orders"]
    one_state = dashboard.header_numbers("SP", None, False)["orders"]
    assert 0 < one_state < everything


def test_empty_selection_does_not_crash(warehouse):
    # a minimum of 10,000 orders removes every bar, route and category
    for make in (dashboard.distance_freight_figure, dashboard.route_figure, dashboard.category_figure):
        figure = make(None, None, False, 10_000)
        assert figure.layout.annotations[0].text.startswith("No data")
    assert dashboard.header_numbers("ZZ", None, False)["rate"] is None


def test_where_clause_builds_parameters():
    where, params = dashboard.where_clause("SP", 2017, True)
    assert params == ["SP", 2017] and "carnival_in_window = 0" in where
    assert dashboard.where_clause(None, None, False) == ("", [])
