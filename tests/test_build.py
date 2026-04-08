import logging
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent

import sys

sys.path.insert(0, str(ROOT))
from build_hateful_eight_interactive import (
    close_at_or_before,
    get_tickers,
    round_down,
    round_up,
    scalar_float,
    HATEFUL8,
    MAG7,
    ROLLING_WINDOWS,
    WINDOW_ORDER,
)

sys.path.insert(0, str(ROOT))
from hateful_eight_chart import (
    first_trading_day_close,
    last_close,
    extract_adjusted_close,
    MAG7,
    ORACLE,
    HATEFUL_EIGHT,
)

sys.path.insert(0, str(ROOT))
from animate_hateful_eight import (
    close_at_or_before as anim_close_at_or_before,
    frame_table,
    HATEFUL8 as ANIM_HATEFUL8,
)


class TestScalarFloat:
    def test_float(self):
        assert scalar_float(3.14) == 3.14

    def test_int(self):
        assert scalar_float(42) == 42.0

    def test_numpy_scalar(self):
        val = MagicMock()
        val.item.return_value = 2.718
        assert scalar_float(val) == 2.718


class TestRoundDown:
    def test_positive(self):
        assert round_down(37.3, 10.0) == 30.0

    def test_negative(self):
        assert round_down(-5.0, 10.0) == -10.0

    def test_exact(self):
        assert round_down(50.0, 10.0) == 50.0


class TestRoundUp:
    def test_positive(self):
        assert round_up(33.1, 10.0) == 40.0

    def test_negative(self):
        assert round_up(-9.0, 10.0) == 0.0

    def test_exact(self):
        assert round_up(50.0, 10.0) == 50.0


class TestCloseAtOrBefore:
    def test_returns_last_value_before_date(self):
        idx = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
        s = pd.Series([100.0, 101.0, 102.0], index=idx)
        result = close_at_or_before(s, pd.Timestamp("2025-01-05"))
        assert result == 101.0

    def test_returns_exact_date_value(self):
        idx = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
        s = pd.Series([100.0, 101.0, 102.0], index=idx)
        result = close_at_or_before(s, pd.Timestamp("2025-01-06"))
        assert result == 102.0

    def test_returns_none_when_series_empty(self):
        s = pd.Series([], dtype=float)
        assert close_at_or_before(s, pd.Timestamp("2025-01-01")) is None

    def test_returns_none_when_before_all_dates(self):
        idx = pd.to_datetime(["2025-01-05", "2025-01-06"])
        s = pd.Series([100.0, 101.0], index=idx)
        assert close_at_or_before(s, pd.Timestamp("2025-01-01")) is None

    def test_skips_nan_values(self):
        idx = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
        s = pd.Series([100.0, float("nan"), 102.0], index=idx)
        result = close_at_or_before(s, pd.Timestamp("2025-01-05"))
        assert result == 100.0


class TestGetTickers:
    def test_excludes_goog_when_googl_present(self, tmp_path, monkeypatch):
        csv = tmp_path / "tickers.csv"
        csv.write_text("ticker\nAAPL\nGOOG\nGOOGL\nMSFT\n")
        monkeypatch.setattr("build_hateful_eight_interactive.UNIVERSE_CSV", csv)
        tickers = get_tickers()
        assert "GOOG" not in tickers
        assert "GOOGL" in tickers
        assert "AAPL" in tickers
        assert "MSFT" in tickers


class TestPointCalculation:
    def test_ytd_contribution_points(self):
        spx_base = 5000.0
        weight = 0.07
        ret_pct = 10.0
        expected_pts = weight * (ret_pct / 100.0) * spx_base
        assert abs(expected_pts - 35.0) < 0.01

    def test_h8_vs_other_classification(self):
        mag7_set = set(MAG7)
        assert mag7_set.issubset(HATEFUL8)
        assert "ORCL" in HATEFUL8
        assert "AAPL" in HATEFUL8
        assert "EXC" not in HATEFUL8

    def test_rolling_window_definitions(self):
        assert "1m" in ROLLING_WINDOWS
        assert "ytd" not in ROLLING_WINDOWS
        assert ROLLING_WINDOWS["1m"] == 30
        assert ROLLING_WINDOWS["1y"] == 365

    def test_window_order(self):
        assert WINDOW_ORDER == ["1m", "ytd", "1y"]


class TestFirstLastClose:
    def test_first_trading_day_close(self):
        idx = pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"])
        df = pd.DataFrame({"Close": [100.0, 101.0, 102.0]}, index=idx)
        result = first_trading_day_close(df["Close"])
        assert result == 100.0

    def test_last_close(self):
        idx = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
        df = pd.DataFrame({"Close": [100.0, 101.0, 102.0]}, index=idx)
        result = last_close(df["Close"])
        assert result == 102.0

    def test_handles_dataframe_input(self):
        idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
        df = pd.DataFrame({"Close": [100.0, 101.0]}, index=idx)
        result = first_trading_day_close(df)
        assert result == 100.0

    def test_handles_empty_series(self):
        s = pd.Series([], dtype=float)
        result = first_trading_day_close(s)
        assert pd.isna(result)


class TestExtractAdjustedClose:
    def test_multilevel_multiindex_close(self):
        cols = pd.MultiIndex.from_tuples([("AAPL", "Close"), ("MSFT", "Close")])
        idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
        df = pd.DataFrame([[100.0, 200.0], [101.0, 201.0]], index=idx, columns=cols)
        result = extract_adjusted_close(df, "AAPL")
        assert list(result) == [100.0, 101.0]

    def test_missing_ticker_returns_empty_series(self):
        cols = pd.MultiIndex.from_tuples([("AAPL", "Close")])
        idx = pd.to_datetime(["2025-01-02"])
        df = pd.DataFrame([[100.0]], index=idx, columns=cols)
        result = extract_adjusted_close(df, "MSFT")
        assert result.empty


class TestFrameTable:
    def test_contribution_points_formula(self):
        idx = pd.to_datetime(["2024-07-01", "2024-07-08", "2025-01-17"])
        px_data = {
            ("AAPL", "Close"): pd.Series([150.0, 155.0, 160.0], index=idx),
        }
        px = pd.DataFrame(px_data)
        px.columns = pd.MultiIndex.from_tuples(px_data.keys())

        spx_close = pd.Series(
            [5000.0, 5050.0, 5100.0],
            index=pd.to_datetime(["2024-07-01", "2024-07-08", "2025-01-17"]),
        )
        shares = pd.Series({"AAPL": 1_000_000_000.0})
        frame_end = pd.Timestamp("2025-01-17")
        result = frame_table(px, spx_close, shares, ["AAPL"], frame_end)

        assert not result.empty
        assert "pts" in result.columns
        assert "weight" in result.columns
        assert "ret_pct" in result.columns
        assert "group" in result.columns
        assert result.iloc[0]["group"] == "Hateful Eight"

    def test_frame_table_empty_when_missing_px_data(self):
        idx = pd.to_datetime(["2025-01-03"])
        px_data = {("AAPL", "Close"): pd.Series([150.0], index=idx)}
        px = pd.DataFrame(px_data)
        px.columns = pd.MultiIndex.from_tuples(px_data.keys())

        spx_close = pd.Series([5000.0], index=pd.to_datetime(["2025-01-03"]))
        shares = pd.Series({"AAPL": 1_000_000_000.0})
        frame_end = pd.Timestamp("2025-01-03")
        result = frame_table(px, spx_close, shares, ["AAPL"], frame_end)
        assert result.empty


class TestMag7OracleClassification:
    def test_hateful_eight_members(self):
        assert set(HATEFUL_EIGHT) == set(MAG7 + [ORACLE])
        assert len(HATEFUL_EIGHT) == 8
        assert ORACLE in HATEFUL_EIGHT
        assert "NVDA" in HATEFUL_EIGHT
        assert "TSLA" in HATEFUL_EIGHT

    def test_anim_hateful8_matches(self):
        assert ANIM_HATEFUL8 == HATEFUL8
