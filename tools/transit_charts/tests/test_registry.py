"""Guard against a transposed boolean in registry.build_registry()'s ChartSpec literal table."""
from __future__ import annotations

from transit_charts.registry import build_registry

NEEDS_SINGLE_ROUTE = {"C9", "A2", "B5", "B7", "B8", "D14", "D17"}
INTERACTIVE = {"C9", "C10", "B6"}
MULTI_DAY_BY_DESIGN = {"D15", "E20", "J39"}
EXCLUDE_ROUTE_AWARE = {"C10", "C11", "B6", "D15", "H28", "H29", "H30"}


def test_registry_flags_match_the_original_cli_tables():
    registry = build_registry()

    assert set(registry) == {
        "C9", "C10", "C11", "A2", "B5", "B6", "B7", "B8", "D14", "D17",
        "D15", "E20", "H28", "H29", "H30", "J39",
    }
    for key, spec in registry.items():
        assert (spec.route_mode == "single") == (key in NEEDS_SINGLE_ROUTE), key
        assert spec.interactive_capable == (key in INTERACTIVE), key
        assert spec.multi_day_by_design == (key in MULTI_DAY_BY_DESIGN), key
        assert spec.exclude_route_aware == (key in EXCLUDE_ROUTE_AWARE), key
