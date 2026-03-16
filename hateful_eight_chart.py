#!/usr/bin/env python3
"""
Build a Bloomberg-style scatter showing YTD return vs estimated S&P 500 point contribution,
with Mag 7 + Oracle highlighted as the "Hateful Eight".
"""

from __future__ import annotations

import argparse
from datetime import date
from io import StringIO

import matplotlib.pyplot as plt
import pandas as pd
import requests
import yfinance as yf
import certifi


MAG7 = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]
ORACLE = "ORCL"
HATEFUL_EIGHT = MAG7 + [ORACLE]


def get_sp500_tickers() -> list[str]:
    """Load current S&P 500 constituents from a stable CSV endpoint."""
    url = "https://datahub.io/core/s-and-p-500-companies/_r/-/data/constituents.csv"
    resp = requests.get(
        url,
        timeout=30,
        verify=certifi.where(),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    table = pd.read_csv(StringIO(resp.text))
    tickers = table["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
    return tickers


def first_trading_day_close(series: pd.Series) -> float:
    if isinstance(series, pd.DataFrame):
        if series.shape[1] == 0:
            return float("nan")
        series = series.iloc[:, 0]
    non_null = series.dropna()
    if non_null.empty:
        return float("nan")
    return float(non_null.iloc[0].item() if hasattr(non_null.iloc[0], "item") else non_null.iloc[0])


def last_close(series: pd.Series) -> float:
    if isinstance(series, pd.DataFrame):
        if series.shape[1] == 0:
            return float("nan")
        series = series.iloc[:, 0]
    non_null = series.dropna()
    if non_null.empty:
        return float("nan")
    return float(non_null.iloc[-1].item() if hasattr(non_null.iloc[-1], "item") else non_null.iloc[-1])


def fetch_prices(tickers: list[str], start: str) -> pd.DataFrame:
    """Fetch daily adjusted close prices for tickers."""
    prices = yf.download(
        tickers=tickers,
        start=start,
        auto_adjust=True,
        progress=False,
        threads=True,
        group_by="ticker",
    )
    return prices


def extract_adjusted_close(prices: pd.DataFrame, ticker: str) -> pd.Series:
    # MultiIndex columns when multiple tickers, single-level otherwise.
    if isinstance(prices.columns, pd.MultiIndex):
        if (ticker, "Close") in prices.columns:
            return prices[(ticker, "Close")]
        if (ticker, "Adj Close") in prices.columns:
            return prices[(ticker, "Adj Close")]
        return pd.Series(dtype=float)

    if "Close" in prices.columns:
        return prices["Close"]
    if "Adj Close" in prices.columns:
        return prices["Adj Close"]
    return pd.Series(dtype=float)


def fetch_shares_outstanding(tickers: list[str]) -> pd.Series:
    """Fetch shares outstanding for each ticker via yfinance."""
    shares = {}
    ticker_objs = yf.Tickers(" ".join(tickers)).tickers
    for symbol in tickers:
        value = None
        t = ticker_objs.get(symbol)
        if t is None:
            shares[symbol] = float("nan")
            continue
        try:
            value = t.fast_info.get("shares")
        except Exception:
            value = None
        if not value:
            try:
                info = t.info
                value = info.get("sharesOutstanding")
            except Exception:
                value = None
        shares[symbol] = float(value) if value else float("nan")
    return pd.Series(shares, name="shares_outstanding")


def build_dataset(year: int) -> pd.DataFrame:
    tickers = get_sp500_tickers()
    start = f"{year}-01-01"

    prices = fetch_prices(tickers=tickers, start=start)
    rows = []
    for t in tickers:
        s = extract_adjusted_close(prices, t)
        start_px = first_trading_day_close(s)
        end_px = last_close(s)
        if pd.isna(start_px) or pd.isna(end_px) or start_px == 0:
            continue
        ytd_pct = (end_px / start_px - 1.0) * 100.0
        rows.append({"ticker": t, "start_px": start_px, "end_px": end_px, "ytd_pct": ytd_pct})

    df = pd.DataFrame(rows).set_index("ticker")

    shares = fetch_shares_outstanding(df.index.tolist())
    df = df.join(shares)
    df["market_cap"] = df["shares_outstanding"] * df["end_px"]
    df = df.dropna(subset=["market_cap"])
    df["weight"] = df["market_cap"] / df["market_cap"].sum()

    spx = yf.download("^GSPC", start=start, auto_adjust=True, progress=False, threads=False)
    spx_start = first_trading_day_close(spx["Close"])

    # Estimated contribution in index points.
    df["spx_point_contrib"] = df["weight"] * (df["ytd_pct"] / 100.0) * spx_start
    df["group"] = "Other"
    df.loc[df.index.isin(MAG7), "group"] = "Mag 7"
    df.loc[df.index == ORACLE, "group"] = "Oracle"
    return df.reset_index()


def plot_chart(df: pd.DataFrame, output_png: str, year: int) -> None:
    fig, ax = plt.subplots(figsize=(14, 11), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    other = df[df["group"] == "Other"]
    mag7 = df[df["group"] == "Mag 7"]
    oracle = df[df["group"] == "Oracle"]

    ax.scatter(
        other["ytd_pct"],
        other["spx_point_contrib"],
        s=190,
        c="#f0089f",
        edgecolor="white",
        linewidth=1.2,
        alpha=0.9,
        label="Other",
        zorder=2,
    )
    ax.scatter(
        mag7["ytd_pct"],
        mag7["spx_point_contrib"],
        s=280,
        c="#f2c300",
        edgecolor="white",
        linewidth=1.4,
        alpha=0.98,
        label="Mag 7",
        zorder=3,
    )
    ax.scatter(
        oracle["ytd_pct"],
        oracle["spx_point_contrib"],
        s=360,
        c="#0d5d8f",
        edgecolor="#111111",
        linewidth=1.6,
        alpha=1.0,
        label="Oracle",
        zorder=4,
    )

    for _, r in mag7.iterrows():
        ax.annotate(
            r["ticker"],
            (r["ytd_pct"], r["spx_point_contrib"]),
            xytext=(6, 6),
            textcoords="offset points",
            fontsize=11,
            color="#181F29",
            zorder=4,
        )
    for _, r in oracle.iterrows():
        ax.annotate(
            "ORCL",
            (r["ytd_pct"], r["spx_point_contrib"]),
            xytext=(8, -14),
            textcoords="offset points",
            fontsize=12,
            fontweight="bold",
            color="#0d5d8f",
            zorder=5,
        )

    ax.axhline(0, color="#444444", linewidth=1.0, zorder=1)
    ax.axvline(0, color="#d0d0d0", linewidth=1.0, zorder=1)
    ax.grid(axis="y", color="#e8ebef", linewidth=1.0)
    ax.grid(axis="x", visible=False)

    ax.set_title(
        "Hateful Eight: Mag 7 + Oracle Drag on the S&P 500 YTD",
        fontsize=25,
        fontweight="bold",
        loc="left",
        pad=20,
    )
    ax.text(
        0.0,
        1.02,
        "YTD return vs estimated S&P 500 point contribution using market-cap weights",
        transform=ax.transAxes,
        fontsize=13,
        color="#5a6573",
    )
    ax.set_xlabel("Year-to-date change (%)", fontsize=14, labelpad=12)
    ax.set_ylabel("Estimated S&P 500 point contribution", fontsize=14, labelpad=12)

    handles, labels = ax.get_legend_handles_labels()
    order = [1, 2, 0]
    ax.legend([handles[i] for i in order], [labels[i] for i in order], frameon=False, loc="upper left")

    as_of = date.today().isoformat()
    fig.text(
        0.01,
        0.01,
        f"Source: yfinance (Yahoo Finance), S&P 500 constituents (Wikipedia), as of {as_of}",
        fontsize=10,
        color="#606a78",
    )

    plt.tight_layout(rect=(0, 0.03, 1, 0.96))
    plt.savefig(output_png, dpi=180, facecolor="white")
    plt.close(fig)


def main() -> None:
    today = date.today()
    parser = argparse.ArgumentParser(description="Create Hateful Eight YTD contribution chart.")
    parser.add_argument("--year", type=int, default=today.year, help="Calendar year for YTD calculation.")
    parser.add_argument("--png", default="hateful-eight-ytd.png", help="Output PNG filename.")
    parser.add_argument("--csv", default="hateful-eight-ytd.csv", help="Output CSV filename.")
    args = parser.parse_args()

    df = build_dataset(year=args.year)
    df.sort_values("spx_point_contrib", inplace=True)
    df.to_csv(args.csv, index=False)
    plot_chart(df=df, output_png=args.png, year=args.year)

    print(f"Wrote {args.csv}")
    print(f"Wrote {args.png}")


if __name__ == "__main__":
    main()
