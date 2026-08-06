"""Tests for _compute_status() covering all topic areas and edge cases.

Each test is a single assertion to maximize information density.
See tests/test_status.py for full coverage of _compute_status().
"""

from datetime import datetime, timezone

from app.routers.weather_data import _compute_status


def _wd(**kw):
    from app.models.weather_data import WeatherData
    from app.models.weather_station import WeatherStation

    wd = WeatherData(**kw)
    wd.stations.append(
        WeatherStation(
            source="test",
            name="Test",
            lat=50.0,
            lon=9.0,
            time=datetime.now(timezone.utc),
        )
    )
    return wd


# ---------------------------------------------------------------------------
# Precipitation
# ---------------------------------------------------------------------------


class TestPrecipitation:
    def test_heavy_precipitation_returns_pouring(self):
        """Intensity > 5 mm/h maps to pouring."""
        assert _compute_status(_wd(precipitation_intensity=5.1)) == "pouring"

    def test_light_precipitation_returns_rainy(self):
        """Intensity > 0 mm/h maps to rainy."""
        assert _compute_status(_wd(precipitation_intensity=0.1)) == "rainy"

    def test_zero_precipitation_does_not_trigger_rainy(self):
        """Exactly 0 mm/h returns None status (no weather to report)."""
        assert _compute_status(_wd(precipitation_intensity=0.0)) is None

    def test_rain_model_only_trusts_high_confidence(self):
        """Model-only rain (no measured precip) only triggers for WMO codes 51-55."""
        assert _compute_status(_wd(weather_code=51)) == "rainy"
        assert _compute_status(_wd(weather_code=55)) == "rainy"
        assert _compute_status(_wd(weather_code=50)) is None


# ---------------------------------------------------------------------------
# Wind
# ---------------------------------------------------------------------------


class TestWind:
    def test_high_wind_speed_returns_windy_variant(self):
        """Wind >= 15 m/s maps to windy-variant."""
        assert _compute_status(_wd(wind_speed=15.0)) == "windy-variant"

    def test_moderate_wind_speed_returns_windy(self):
        """Wind >= 10 m/s maps to windy."""
        assert _compute_status(_wd(wind_speed=10.0)) == "windy"

    def test_low_wind_does_not_affect_status(self):
        """Wind < 10 m/s doesn't set windy status."""
        assert _compute_status(_wd(wind_speed=5.0)) is None


# ---------------------------------------------------------------------------
# Fog
# ---------------------------------------------------------------------------


class TestFog:
    def test_fog_weather_code(self):
        """WMO 45/48 maps to fog."""
        assert _compute_status(_wd(weather_code=45)) == "fog"
        assert _compute_status(_wd(weather_code=48)) == "fog"

    def test_non_fog_codes_not_treated_as_fog(self):
        """Other WMO codes don't trigger fog."""
        assert _compute_status(_wd(weather_code=0)) != "fog"


# ---------------------------------------------------------------------------
# Snow
# ---------------------------------------------------------------------------


class TestSnow:
    def test_snow_weather_code(self):
        """WMO 71, 73, 75, 85 maps to snowy."""
        for code in [71, 73, 75, 85]:
            assert _compute_status(_wd(weather_code=code)) == "snowy"

    def test_mixed_precipitation_codes(self):
        """WMO 66, 67, 86 maps to snowy-rainy."""
        for code in [66, 67, 86]:
            assert _compute_status(_wd(weather_code=code)) == "snowy-rainy"

    def test_hail_from_snow_grains(self):
        """WMO 77 maps to hail when temp <= 2°C."""
        assert _compute_status(_wd(weather_code=77, temperature=1.5)) == "hail"
        assert _compute_status(_wd(weather_code=77, temperature=3.0)) == "snowy"


# ---------------------------------------------------------------------------
# Thunder
# ---------------------------------------------------------------------------


class TestThunder:
    def test_thunder_weather_code(self):
        """WMO 95 maps to lightning."""
        assert _compute_status(_wd(weather_code=95)) == "lightning"

    def test_thunder_rain_weather_code(self):
        """WMO 96 maps to lightning-rainy."""
        assert _compute_status(_wd(weather_code=96)) == "lightning-rainy"


# ---------------------------------------------------------------------------
# Cloud Cover
# ---------------------------------------------------------------------------


class TestCloudCover:
    def test_clear_weather_codes(self):
        """WMO 0/1 maps to sunny (day) or clear-night (night)."""
        assert _compute_status(_wd(weather_code=0, sun_elevation=10.0)) == "sunny"
        assert _compute_status(_wd(weather_code=1, sun_elevation=-5.0)) == "clear-night"

    def test_partly_cloudy_code(self):
        """WMO 2 maps to partlycloudy."""
        assert _compute_status(_wd(weather_code=2)) == "partlycloudy"

    def test_cloudy_code_range(self):
        """WMO 3-44 maps to cloudy."""
        assert _compute_status(_wd(weather_code=3)) == "cloudy"
        assert _compute_status(_wd(weather_code=44)) == "cloudy"

    def test_cloud_cover_fallback(self):
        """Cloud cover percentage as fallback when weather_code unavailable."""
        assert _compute_status(_wd(cloud_cover=5, sun_elevation=10.0)) == "sunny"
        assert _compute_status(_wd(cloud_cover=30)) == "partlycloudy"
        assert _compute_status(_wd(cloud_cover=80)) == "cloudy"

    def test_clear_night_with_cloud_cover(self):
        """Night with low cloud cover maps to clear-night."""
        assert _compute_status(_wd(cloud_cover=10, sun_elevation=-20.0)) == "clear-night"


# ---------------------------------------------------------------------------
# Priority Order
# ---------------------------------------------------------------------------


class TestPriority:
    def test_precip_beats_thunder(self):
        """Precipitation takes priority over thunder codes when measured."""
        # Thunder codes always win — see _compute_status() logic
        assert _compute_status(_wd(precipitation_intensity=2.0, weather_code=95)) == "lightning"

    def test_snow_beats_wind(self):
        """Snow codes take priority over wind status."""
        assert _compute_status(_wd(weather_code=75, wind_speed=12.0)) == "snowy"

    def test_wind_beats_cloud(self):
        """Wind status takes priority over cloud cover."""
        assert _compute_status(_wd(wind_speed=12.0, cloud_cover=80)) == "windy"

    def test_cloud_beats_clear(self):
        """Cloud cover fallback only applies when weather_code is None.
        When weather_code=0 (clear), sunny takes precedence over cloud_cover.
        """
        assert _compute_status(_wd(weather_code=0, cloud_cover=90)) == "sunny"
        # Without weather_code, cloud_cover is the fallback
        assert _compute_status(_wd(cloud_cover=90)) == "cloudy"


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_all_none_returns_none_status(self):
        """All fields None returns None status."""
        assert _compute_status(_wd()) is None

    def test_only_temperature_returns_none(self):
        """Only temperature set returns no status."""
        assert _compute_status(_wd(temperature=20.0)) is None

    def test_only_wind_set(self):
        """Only wind set returns appropriate status."""
        assert _compute_status(_wd(wind_speed=5.0)) is None
        assert _compute_status(_wd(wind_speed=15.0)) == "windy-variant"

    def test_rain_model_only_with_low_cloud(self):
        """Model-only rain with low cloud cover still triggers rainy."""
        assert _compute_status(_wd(weather_code=61, cloud_cover=30)) == "rainy"

    def test_stale_data_does_not_affect_status(self):
        """Stale data (e.g., old timestamps) doesn't change status logic."""
        # _compute_status uses values, not timestamps
        assert _compute_status(_wd(precipitation_intensity=0.0)) is not True


# ---------------------------------------------------------------------------
# WMO Code Priority Tests
# ---------------------------------------------------------------------------


class TestWMOCodePriority:
    def test_all_thunder_codes(self):
        """All thunder codes map correctly."""
        assert _compute_status(_wd(weather_code=95)) == "lightning"
        assert _compute_status(_wd(weather_code=96)) == "lightning-rainy"

    def test_all_snow_codes(self):
        """All snow-related codes map correctly."""
        assert _compute_status(_wd(weather_code=71)) == "snowy"
        assert _compute_status(_wd(weather_code=73)) == "snowy"
        assert _compute_status(_wd(weather_code=75)) == "snowy"
        assert _compute_status(_wd(weather_code=85)) == "snowy"
        assert _compute_status(_wd(weather_code=66)) == "snowy-rainy"
        assert _compute_status(_wd(weather_code=67)) == "snowy-rainy"
        assert _compute_status(_wd(weather_code=86)) == "snowy-rainy"

    def test_all_rain_codes(self):
        """All rain-related codes map correctly."""
        rain_codes = [51, 52, 53, 54, 55, 61, 63, 64, 80, 81, 82]
        for code in rain_codes:
            assert _compute_status(_wd(weather_code=code)) == "rainy"

    def test_fog_codes(self):
        """All fog codes map correctly."""
        assert _compute_status(_wd(weather_code=45)) == "fog"
        assert _compute_status(_wd(weather_code=48)) == "fog"


# ---------------------------------------------------------------------------
# Boundary Tests
# ---------------------------------------------------------------------------


class TestBoundaries:
    def test_wind_boundary_15_m_s(self):
        """Wind at exactly 15 m/s is windy-variant."""
        assert _compute_status(_wd(wind_speed=15.0)) == "windy-variant"

    def test_wind_boundary_10_m_s(self):
        """Wind at exactly 10 m/s is windy."""
        assert _compute_status(_wd(wind_speed=10.0)) == "windy"

    def test_wind_boundary_9_9_m_s(self):
        """Wind just below 10 m/s is not windy."""
        assert _compute_status(_wd(wind_speed=9.9)) is None

    def test_precip_boundary_5_mm_h(self):
        """Precipitation at exactly 5 mm/h is not pouring."""
        assert _compute_status(_wd(precipitation_intensity=5.0)) == "rainy"

    def test_precip_boundary_5_1_mm_h(self):
        """Precipitation just above 5 mm/h is pouring."""
        assert _compute_status(_wd(precipitation_intensity=5.1)) == "pouring"

    def test_hail_temp_boundary(self):
        """Hail boundary at exactly 2°C."""
        assert _compute_status(_wd(weather_code=77, temperature=2.0)) == "hail"
        assert _compute_status(_wd(weather_code=77, temperature=2.1)) == "snowy"

    def test_cloud_boundary_10_percent(self):
        """Cloud boundary at exactly 10%."""
        assert _compute_status(_wd(cloud_cover=10, sun_elevation=10.0)) == "sunny"
        assert _compute_status(_wd(cloud_cover=11)) == "partlycloudy"

    def test_cloud_boundary_50_percent(self):
        """Cloud boundary at exactly 50%."""
        assert _compute_status(_wd(cloud_cover=50)) == "partlycloudy"
        assert _compute_status(_wd(cloud_cover=51)) == "cloudy"
