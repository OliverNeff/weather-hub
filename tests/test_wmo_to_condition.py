"""Test _wmo_to_condition_template Jinja rendering."""

from jinja2 import Environment

from app.mqtt_discovery import _wmo_to_condition_template


template = Environment().from_string(_wmo_to_condition_template())


def _render(weather_code: int | None) -> str:
    return template.render(value_json={"weather_code": weather_code})


class TestWmoToCondition:
    """Verify WMO codes map to correct HA condition strings."""

    # Clear / clear-night
    def test_clear_day(self):
        assert _render(0) == "clear-night"

    def test_mainly_clear(self):
        assert _render(1) == "clear-night"

    # Clouds
    def test_partly_cloudy(self):
        assert _render(2) == "partlycloudy"

    def test_overcast(self):
        assert _render(3) == "cloudy"

    def test_high_clouds(self):
        assert _render(4) == "unknown"

    # Fog
    def test_fog(self):
        assert _render(45) == "fog"

    def test_rime_fog(self):
        assert _render(48) == "fog"

    # Rain (light / moderate / heavy / showers)
    def test_drizzle(self):
        assert _render(51) == "rainy"

    def test_rain(self):
        for code in (53, 55, 56, 61, 63, 65, 80, 81, 82):
            assert _render(code) == "rainy", f"WMO {code} should map to rainy"

    # Snow
    def test_snow(self):
        for code in (71, 73, 75, 77, 85, 87):
            assert _render(code) == "snowy", f"WMO {code} should map to snowy"

    # Snowy-rainy (freezing rain, sleet)
    def test_snowy_rainy(self):
        for code in (66, 67, 86):
            assert _render(code) == "snowy-rainy", f"WMO {code} should map to snowy-rainy"

    # Thunderstorm
    def test_lightning(self):
        assert _render(95) == "lightning"

    def test_thunderstorm_with_hail(self):
        assert _render(96) == "lightning-rainy"

    def test_thunderstorm_heavy_hail(self):
        assert _render(99) == "lightning-rainy"

    # None / unknown
    def test_none(self):
        assert _render(None) == "unknown"

    def test_unmapped_code(self):
        assert _render(52) == "unknown"
