"""Unit tests for decode_polyline (no QGIS, plain Python)."""
import pytest

from easy_otp.core.plan_client import decode_polyline

# Canonical Google Encoded Polyline example from the algorithm documentation.
# Encodes [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)] at precision 5.
# Note: the string contains a backtick character before the final '@' — it was
# dropped in some Markdown renderings of the spec (backtick = inline code fence).
_CANONICAL_ENCODED = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
_CANONICAL_DECODED = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_empty_string_returns_empty_list():
    assert decode_polyline("") == []


def test_canonical_length():
    result = decode_polyline(_CANONICAL_ENCODED)
    assert len(result) == 3


def test_canonical_first_point():
    result = decode_polyline(_CANONICAL_ENCODED)
    assert result[0] == pytest.approx((38.5, -120.2), abs=1e-3)


def test_canonical_second_point():
    result = decode_polyline(_CANONICAL_ENCODED)
    assert result[1] == pytest.approx((40.7, -120.95), abs=1e-3)


def test_canonical_third_point():
    result = decode_polyline(_CANONICAL_ENCODED)
    assert result[2] == pytest.approx((43.252, -126.453), abs=1e-3)


def test_negative_coordinates_decoded_correctly():
    # All decoded coordinates in the canonical example have negative longitudes.
    result = decode_polyline(_CANONICAL_ENCODED)
    for lat, lon in result:
        assert lon < 0


def test_precision_parameter_changes_scale():
    # Same encoded string decoded at precision=6 gives values 10× smaller.
    result5 = decode_polyline(_CANONICAL_ENCODED, precision=5)
    result6 = decode_polyline(_CANONICAL_ENCODED, precision=6)
    # At precision=6 each value should be roughly 1/10 of the precision=5 value.
    assert abs(result6[0][0] - result5[0][0] / 10) < 1.0


def test_single_point_encoded():
    # Encode (0.0, 0.0) manually: delta=0 for both, encodes as "??" (two ?? chars).
    # ord('?') = 63, 63 - 63 = 0, chunk = 0 < 0x20, stop. Repeated for lon.
    result = decode_polyline("??")
    assert len(result) == 1
    assert result[0] == pytest.approx((0.0, 0.0), abs=1e-5)
