"""Route-name pattern matching - the exact/prefix wildcard shared by extract-time route
resolution and chart-time --exclude-route."""
from __future__ import annotations

from transit_charts import sources


def test_exact_match_is_case_insensitive():
    matched, selected, unmatched = sources.match_route_names(["11"], {"11", "10A", "10B"})

    assert matched == {"11": ["11"]}
    assert selected == {"11"}
    assert unmatched == []


def test_trailing_star_matches_by_prefix():
    matched, selected, unmatched = sources.match_route_names(
        ["10*"], {"10A", "10B", "100", "11"}
    )

    # The documented gotcha: '10*' also catches '100', which is a different line, not a
    # variant of '10'. The matcher's job is only to report what it actually matched.
    assert matched == {"10*": ["100", "10A", "10B"]}
    assert selected == {"10A", "10B", "100"}
    assert unmatched == []


def test_pattern_with_no_hit_is_reported_not_silently_dropped():
    matched, selected, unmatched = sources.match_route_names(["99"], {"11", "10A"})

    assert matched == {}
    assert selected == set()
    assert unmatched == ["99"]


def test_several_patterns_union_their_hits():
    matched, selected, unmatched = sources.match_route_names(
        ["11", "10*"], {"11", "10A", "10B", "69A"}
    )

    assert selected == {"11", "10A", "10B"}
    assert unmatched == []
