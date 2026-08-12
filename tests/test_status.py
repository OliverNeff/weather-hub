"""Tests for _wmo_to_condition and merge helpers (_pick_first, _pick_max, _sorted_by_freshness)."""

from datetime import datetime, timedelta, timezone

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation
from app.routers.weather_data import (
    _pick_first,
    _pick_max,
    _sorted_by_freshness,
    _wmo_to_condition,
)

_now = datetime.now(timezone.utc)
_yesterday = _now - timedelta(hours=30)
_old = _now - timedelta(hours=48)


# ---------------------------------------------------------------------------
# _wmo_to_condition() — WMO code → HA condition string
# ---------------------------------------------------------------------------


def test_clear_night():
    assert _wmo_to_condition(0) == "clear-night"
    assert _wmo_to_condition(1) == "clear-night"


def test_partlycloudy():
    assert _wmo_to_condition(2) == "partlycloudy"


def test_cloudy():
    assert _wmo_to_condition(3) == "cloudy"
    assert _wmo_to_condition(4) == "cloudy"
    assert _wmo_to_condition(44) == "cloudy"


def test_fog():
    assert _wmo_to_condition(45) == "fog"
    assert _wmo_to_condition(48) == "fog"


def test_rainy():
    for code in (51, 53, 55, 56, 61, 63, 65, 80, 81, 82):
        assert _wmo_to_condition(code) == "rainy", f"failed for code {code}"


def test_pouring():
    assert _wmo_to_condition(52) == "pouring"
    assert _wmo_to_condition(54) == "pouring"
    assert _wmo_to_condition(64) == "pouring"


def test_snowy_rainy():
    for code in (66, 67, 86):
        assert _wmo_to_condition(code) == "snowy-rainy", f"failed for code {code}"


def test_snowy():
    for code in (71, 73, 75, 77, 85, 87):
        assert _wmo_to_condition(code) == "snowy", f"failed for code {code}"


def test_lightning():
    assert _wmo_to_condition(95) == "lightning"


def test_lightning_rainy():
    assert _wmo_to_condition(96) == "lightning-rainy"
    assert _wmo_to_condition(99) == "lightning-rainy"


def test_none():
    assert _wmo_to_condition(None) is None


# ---------------------------------------------------------------------------
# _pick_first()
# ---------------------------------------------------------------------------


def test_pick_first_returns_first():
    a = WeatherData(temperature=20.0)
    b = WeatherData(temperature=25.0)
    val, adapter = _pick_first((a, b), "temperature")
    assert val == 20.0
    assert adapter is a


def test_pick_first_skips_none():
    a = WeatherData(temperature=None)
    b = WeatherData(temperature=25.0)
    val, adapter = _pick_first((a, b), "temperature")
    assert val == 25.0
    assert adapter is b


def test_pick_first_all_none():
    a = WeatherData(temperature=None)
    b = WeatherData(temperature=None)
    val, adapter = _pick_first((a, b), "temperature")
    assert val is None
    assert adapter is None


def test_pick_first_empty_tuple():
    val, adapter = _pick_first((), "temperature")
    assert val is None
    assert adapter is None


def test_pick_first_single_match():
    a = WeatherData(wind_speed=5.0)
    b = WeatherData(wind_speed=None)
    c = WeatherData(wind_speed=10.0)
    val, adapter = _pick_first((a, b, c), "wind_speed")
    assert val == 5.0
    assert adapter is a


# ---------------------------------------------------------------------------
# _pick_max()
# ---------------------------------------------------------------------------


def test_pick_max_returns_max():
    a = WeatherData(wind_speed=5.0)
    b = WeatherData(wind_speed=12.0)
    c = WeatherData(wind_speed=3.0)
    assert _pick_max((a, b, c), "wind_speed") == 12.0


def test_pick_max_ignores_none():
    a = WeatherData(wind_speed=None)
    b = WeatherData(wind_speed=8.0)
    assert _pick_max((a, b), "wind_speed") == 8.0


def test_pick_max_all_none():
    a = WeatherData(wind_speed=None)
    b = WeatherData(wind_speed=None)
    assert _pick_max((a, b), "wind_speed") is None


def test_pick_max_single_value():
    a = WeatherData(wind_speed=7.0)
    assert _pick_max((a,), "wind_speed") == 7.0


def test_pick_max_negative_values():
    a = WeatherData(temperature=-5.0)
    b = WeatherData(temperature=-1.0)
    assert _pick_max((a, b), "temperature") == -1.0


def test_pick_max_zero_is_valid():
    a = WeatherData(precipitation_intensity=0.0)
    b = WeatherData(precipitation_intensity=None)
    assert _pick_max((a, b), "precipitation_intensity") == 0.0


# ---------------------------------------------------------------------------
# _sorted_by_freshness()
# ---------------------------------------------------------------------------


def test_sorted_newest_first():
    a = WeatherData(stations=[WeatherStation(time=_old)])
    b = WeatherData(stations=[WeatherStation(time=_now)])
    c = WeatherData(stations=[WeatherStation(time=_yesterday)])
    result = _sorted_by_freshness((a, b, c))
    assert result[0] is b
    assert result[1] is c
    assert result[2] is a


def test_sorted_none_time_at_end():
    a = WeatherData(stations=[WeatherStation(time=_now)])
    b = WeatherData(stations=[WeatherStation(time=None)])
    result = _sorted_by_freshness((a, b))
    assert result[0] is a
    assert result[1] is b


def test_sorted_no_stations_at_end():
    a = WeatherData(stations=[WeatherStation(time=_now)])
    b = WeatherData()
    result = _sorted_by_freshness((a, b))
    assert result[0] is a
    assert result[1] is b


def test_sorted_all_none_times():
    a = WeatherData()
    b = WeatherData()
    result = _sorted_by_freshness((a, b))
    assert len(result) == 2


def test_sorted_preserves_count():
    a = WeatherData()
    b = WeatherData()
    c = WeatherData()
    result = _sorted_by_freshness((a, b, c))
    assert len(result) == 3
