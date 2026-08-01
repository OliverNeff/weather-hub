import pytest

from app.adapter.buinradar import _parse_raindata


class TestParseRaindata:
    def test_code_zero_returns_zero(self):
        result = _parse_raindata("0|123")
        assert result == [0.0]

    def test_code_109_returns_1_mmh(self):
        result = _parse_raindata("109|456")
        assert abs(result[0] - 1.0) < 1e-9

    def test_empty_string(self):
        result = _parse_raindata("")
        assert result == []

    def test_none_returns_empty(self):
        result = _parse_raindata(None)
        assert result == []

    def test_mixed_values(self):
        raw = "0|a\n109|b\n141|c\n0|d"
        result = _parse_raindata(raw)
        assert len(result) == 4
        assert result[0] == 0.0
        assert abs(result[1] - 1.0) < 1e-9
        assert result[2] > 1.0
        assert result[3] == 0.0

    def test_code_141_returns_10_mmh(self):
        result = _parse_raindata("141|xyz")
        assert abs(result[0] - 10.0) < 1e-9

    def test_code_77_returns_0_1_mmh(self):
        result = _parse_raindata("77|xyz")
        assert abs(result[0] - 0.1) < 1e-9

    def test_lines_without_pipe_skipped(self):
        raw = "0|1\nbadline\n109|2"
        result = _parse_raindata(raw)
        assert len(result) == 2

    def test_many_intervals(self):
        codes = [str(i) for i in range(200)]
        raw = "\n".join(f"{c}|x" for c in codes)
        result = _parse_raindata(raw)
        assert len(result) == 200

    def test_values_non_negative_for_any_code(self):
        raw = "50|a\n100|b\n200|c"
        result = _parse_raindata(raw)
        for v in result:
            assert v >= 0