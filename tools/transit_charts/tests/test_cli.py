"""--route / --exclude-route composition, resolved against what a tidy table actually carries."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from transit_charts import cli, sources


def _args(route=None, exclude_route=None):
    return SimpleNamespace(route=route or [], exclude_route=exclude_route or [])


def _table(names):
    return pd.DataFrame({"route_short_name": names, "route_group": names})


def test_no_flags_means_every_route_unfiltered():
    assert cli._resolve_route_filter(_args(), _table(["11", "10A"])) is None


def test_exclude_only_subtracts_from_every_route_in_the_table():
    result = cli._resolve_route_filter(_args(exclude_route=["10A"]), _table(["11", "10A", "10B"]))

    assert result == ["10B", "11"]


def test_route_and_exclude_route_compose():
    """'10*' minus '10B' leaves '10A' - include first, then subtract."""
    result = cli._resolve_route_filter(
        _args(route=["10*"], exclude_route=["10B"]), _table(["11", "10A", "10B"])
    )

    assert result == ["10A"]


def test_exclude_route_pattern_with_no_hit_is_an_error_not_a_silent_no_op():
    with pytest.raises(sources.InputError):
        cli._resolve_route_filter(_args(exclude_route=["99"]), _table(["11", "10A"]))


def test_excluding_every_route_present_is_an_error():
    with pytest.raises(ValueError):
        cli._resolve_route_filter(_args(exclude_route=["*"]), _table(["11", "10A"]))


def test_include_pattern_with_no_hit_still_errors_like_resolve_routes():
    with pytest.raises(sources.InputError):
        cli._resolve_route_filter(
            _args(route=["99"], exclude_route=["11"]), _table(["11", "10A"])
        )
