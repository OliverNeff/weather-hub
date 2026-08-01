from datetime import datetime, timedelta, timezone

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation
from app.routers.weather_data import (
    _compute_status,
    _pick_first,
    _pick_max,
    _sorted_by_freshness,
)

_now = datetime.now(timezone.utc)
_yesterday = _now - timedelta(hours=30)
_old = _now - timedelta(hours=48)


# ---------------------------------------------------------------------------
# _compute_status() — all 14 HA status values
# ---------------------------------------------------------------------------


def test_status_clear_night():
    wd = WeatherData(weather_code=0, sun_elevation=-10)
    assert _compute_status(wd) == "clear-night"


def test_status_sunny_day():
    wd = WeatherData(weather_code=0, sun_elevation=45)
    assert _compute_status(wd) == "sunny"


def test_status_sunny_code_1():
    wd = WeatherData(weather_code=1, sun_elevation=20)
    assert _compute_status(wd) == "sunny"


def test_status_partlycloudy_code():
    wd = WeatherData(weather_code=2)
    assert _compute_status(wd) == "partlycloudy"


def test_status_partlycloudy_cloud():
    wd = WeatherData(cloud_cover=30)
    assert _compute_status(wd) == "partlycloudy"


def test_status_cloudy_code():
    wd = WeatherData(weather_code=3)
    assert _compute_status(wd) == "cloudy"


def test_status_cloudy_code_range():
    wd = WeatherData(weather_code=44)
    assert _compute_status(wd) == "cloudy"


def test_status_cloudy_cover():
    wd = WeatherData(cloud_cover=80)
    assert _compute_status(wd) == "cloudy"


def test_status_cloudy_low_code():
    wd = WeatherData(weather_code=4)
    assert _compute_status(wd) == "cloudy"


def test_status_fog():
    wd = WeatherData(weather_code=45)
    assert _compute_status(wd) == "fog"


def test_status_fog_rime_fog():
    wd = WeatherData(weather_code=48)
    assert _compute_status(wd) == "fog"


def test_status_hail():
    wd = WeatherData(weather_code=77, temperature=1.0)
    assert _compute_status(wd) == "hail"


def test_status_hail_temp_boundary():
    wd = WeatherData(weather_code=77, temperature=2.0)
    assert _compute_status(wd) == "hail"


def test_status_snowy_code():
    wd = WeatherData(weather_code=71)
    assert _compute_status(wd) == "snowy"


def test_status_snowy_heavy():
    wd = WeatherData(weather_code=75)
    assert _compute_status(wd) == "snowy"


def test_status_snowy_drizzle():
    wd = WeatherData(weather_code=85)
    assert _compute_status(wd) == "snowy"


def test_status_snowy_77_warm():
    wd = WeatherData(weather_code=77, temperature=10)
    assert _compute_status(wd) == "snowy"


def test_status_snowy_rainy():
    wd = WeatherData(weather_code=66)
    assert _compute_status(wd) == "snowy-rainy"


def test_status_snowy_rainy_codes():
    for code in (67, 86):
        wd = WeatherData(weather_code=code)
        assert _compute_status(wd) == "snowy-rainy", f"failed for code {code}"


def test_status_lightning():
    wd = WeatherData(weather_code=95)
    assert _compute_status(wd) == "lightning"


def test_status_lightning_rainy():
    wd = WeatherData(weather_code=96)
    assert _compute_status(wd) == "lightning-rainy"


def test_status_rainy_precip():
    wd = WeatherData(precipitation_intensity=2.0)
    assert _compute_status(wd) == "rainy"


def test_status_rainy_wmo_fallback():
    wd = WeatherData(weather_code=61, precipitation_intensity=0)
    assert _compute_status(wd) == "rainy"


def test_status_rainy_wmo_codes():
    for code in (51, 53, 55, 61, 63, 64, 80, 81, 82):
        wd = WeatherData(weather_code=code)
        assert _compute_status(wd) == "rainy", f"failed for code {code}"


def test_status_pouring():
    wd = WeatherData(precipitation_intensity=6.0)
    assert _compute_status(wd) == "pouring"


def test_status_windy():
    wd = WeatherData(wind_speed=12.0)
    assert _compute_status(wd) == "windy"


def test_status_windy_boundary():
    wd = WeatherData(wind_speed=10.0)
    assert _compute_status(wd) == "windy"


def test_status_windy_variant():
    wd = WeatherData(wind_speed=16.0)
    assert _compute_status(wd) == "windy-variant"


def test_status_windy_variant_boundary():
    wd = WeatherData(wind_speed=15.0)
    assert _compute_status(wd) == "windy-variant"


def test_status_none_all_empty():
    wd = WeatherData()
    assert _compute_status(wd) is None


def test_status_clear_night_cloud_fallback():
    wd = WeatherData(cloud_cover=5, sun_elevation=-5)
    assert _compute_status(wd) == "clear-night"


def test_status_sunny_cloud_fallback():
    wd = WeatherData(cloud_cover=10, sun_elevation=30)
    assert _compute_status(wd) == "sunny"


def test_status_snowy_thunder_priority():
    wd = WeatherData(weather_code=96, precipitation_intensity=0)
    assert _compute_status(wd) == "lightning-rainy"


def test_status_snowy_rainy_thunder_priority():
    wd = WeatherData(weather_code=66, precipitation_intensity=0)
    assert _compute_status(wd) == "snowy-rainy"


def test_status_snowy_over_measured_precip():
    wd = WeatherData(weather_code=71, precipitation_intensity=1.0)
    assert _compute_status(wd) == "snowy"


def test_status_pouring_over_wind():
    wd = WeatherData(precipitation_intensity=7.0, wind_speed=20.0)
    assert _compute_status(wd) == "pouring"


def test_status_windy_over_precip_zero():
    wd = WeatherData(precipitation_intensity=0, wind_speed=14.0)
    assert _compute_status(wd) == "windy"


def test_status_rainy_precip_over_wmo():
    wd = WeatherData(precipitation_intensity=0.5, weather_code=61)
    assert _compute_status(wd) == "rainy"


def test_status_windy_below_threshold():
    wd = WeatherData(wind_speed=9.9)
    assert _compute_status(wd) is None


def test_status_precip_zero_not_rainy():
    wd = WeatherData(precipitation_intensity=0)
    assert _compute_status(wd) is None


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
    a = WeatherData(
        stations=[WeatherStation(time=_old)]
    )
    b = WeatherData(
        stations=[WeatherStation(time=_now)]
    )
    c = WeatherData(
        stations=[WeatherStation(time=_yesterday)]
    )
    result = _sorted_by_freshness((a, b, c))
    assert result[0] is b
    assert result[1] is c
    assert result[2] is a


def test_sorted_none_time_at_end():
    a = WeatherData(
        stations=[WeatherStation(time=_now)]
    )
    b = WeatherData(
        stations=[WeatherStation(time=None)]
    )
    result = _sorted_by_freshness((a, b))
    assert result[0] is a
    assert result[1] is b


def test_sorted_no_stations_at_end():
    a = WeatherData(
        stations=[WeatherStation(time=_now)]
    )
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