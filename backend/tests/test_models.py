import pytest
from pydantic import ValidationError

from models import Settings

VALID = dict(
    source_mode="fixture",
    db_path="data/test.duckdb",
    symbols=("SPY", "QQQ", "AAPL"),
    default_symbol="SPY",
    refresh_min_interval_seconds=60,
    risk_free_rate=0.04,
    dividend_yields={"SPY": 0.0, "QQQ": 0.0, "AAPL": 0.0},
)


def test_valid_settings_parses():
    settings = Settings(**VALID)
    assert settings.default_symbol == "SPY"


@pytest.mark.parametrize(
    "override",
    [
        {"symbols": ()},
        {"symbols": ("SPY", "SPY")},
        {"symbols": ("SPY", "QQQ", "AAPL", "MSFT")},
        {"default_symbol": "MSFT"},
        {"dividend_yields": {"SPY": 0.0, "QQQ": 0.0}},
        {"dividend_yields": {"SPY": 0.0, "QQQ": 0.0, "AAPL": 0.0, "MSFT": 0.0}},
        {"refresh_min_interval_seconds": 59},
        {"risk_free_rate": 0.51},
        {"risk_free_rate": -0.11},
        {"dividend_yields": {"SPY": 0.51, "QQQ": 0.0, "AAPL": 0.0}},
    ],
)
def test_invalid_settings_rejected(override):
    # M1.8
    with pytest.raises(ValidationError):
        Settings(**{**VALID, **override})
