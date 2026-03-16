#!/usr/bin/env python3
from __future__ import annotations

from datetime import date
from pathlib import Path
import shutil

from PIL import Image
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf


MAG7 = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"}
ORCL = "ORCL"
HATEFUL8 = MAG7 | {ORCL}

ROOT = Path("/Users/pk/dev/hateful-eight")
UNIVERSE_CSV = ROOT / "hateful-eight-ytd-latest.csv"
OUT_GIF = ROOT / "hateful-eight-rolling-6m-weekly.gif"
OUT_SUMMARY = ROOT / "hateful-eight-rolling-6m-weekly-summary.csv"
TMP_DIR = ROOT / "tmp_h8_frames"


def get_tickers() -> list[str]:
    if not UNIVERSE_CSV.exists():
        raise FileNotFoundError(f"Missing universe file: {UNIVERSE_CSV}")
    df = pd.read_csv(UNIVERSE_CSV)
    return sorted(df["ticker"].dropna().astype(str).str.replace(".", "-", regex=False).unique().tolist())


def close_at_or_before(series: pd.Series, asof: pd.Timestamp) -> float | None:
    s = series.dropna()
    if s.empty:
        return None
    s = s.loc[:asof]
    if s.empty:
        return None
    return float(s.iloc[-1])


def load_prices(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.Series]:
    prices = yf.download(
        tickers=tickers,
        start=start.strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        auto_adjust=True,
        progress=False,
        threads=True,
        group_by="ticker",
    )
    spx = yf.download(
        "^GSPC",
        start=start.strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    return prices, spx["Close"].dropna()


def load_shares(tickers: list[str]) -> pd.Series:
    objs = yf.Tickers(" ".join(tickers)).tickers
    shares = {}
    for t in tickers:
        v = None
        obj = objs.get(t)
        if obj is not None:
            try:
                v = obj.fast_info.get("shares")
            except Exception:
                v = None
            if not v:
                try:
                    v = obj.info.get("sharesOutstanding")
                except Exception:
                    v = None
        shares[t] = float(v) if v else float("nan")
    return pd.Series(shares, name="shares")


def frame_table(
    prices: pd.DataFrame,
    spx_close: pd.Series,
    shares: pd.Series,
    tickers: list[str],
    frame_end: pd.Timestamp,
) -> pd.DataFrame:
    window_start = frame_end - pd.Timedelta(days=182)
    spx_start = close_at_or_before(spx_close, window_start)
    if spx_start is None:
        return pd.DataFrame()

    rows = []
    for t in tickers:
        if not isinstance(prices.columns, pd.MultiIndex) or (t, "Close") not in prices.columns:
            continue
        s = prices[(t, "Close")]
        px0 = close_at_or_before(s, window_start)
        px1 = close_at_or_before(s, frame_end)
        if px0 is None or px1 is None or px0 == 0:
            continue
        ret = (px1 / px0 - 1.0) * 100.0
        sh = shares.get(t)
        if pd.isna(sh):
            continue
        mcap = sh * px1
        rows.append(
            {
                "ticker": t,
                "ret_pct": ret,
                "end_px": px1,
                "market_cap": mcap,
                "group": "Hateful Eight" if t in HATEFUL8 else "Other",
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["weight"] = df["market_cap"] / df["market_cap"].sum()
    df["pts"] = df["weight"] * (df["ret_pct"] / 100.0) * spx_start
    df["frame_end"] = frame_end
    df["window_start"] = window_start
    return df


def render_frame(
    df: pd.DataFrame,
    out_png: Path,
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    frame_end: pd.Timestamp,
    window_start: pd.Timestamp,
) -> None:
    fig, ax = plt.subplots(figsize=(12, 10), dpi=130)
    fig.patch.set_facecolor("#f4f1eb")
    ax.set_facecolor("#FFFCF7")

    other = df[df["group"] == "Other"]
    h8 = df[df["group"] == "Hateful Eight"]

    ax.scatter(
        other["ret_pct"],
        other["pts"],
        s=60,
        c="#2E5B88",
        alpha=0.33,
        edgecolors="#2E5B88",
        linewidths=0.7,
        zorder=2,
        label="S&P 500",
    )
    ax.scatter(
        h8["ret_pct"],
        h8["pts"],
        s=68,
        c="#C4533A",
        alpha=0.9,
        edgecolors="#C4533A",
        linewidths=1.0,
        zorder=3,
        label="Hateful Eight",
    )

    for _, r in h8.iterrows():
        ax.annotate(
            r["ticker"],
            (r["ret_pct"], r["pts"]),
            xytext=(5, -1),
            textcoords="offset points",
            fontsize=10,
            color="#C4533A",
            weight="semibold",
            zorder=4,
        )

    ax.axhline(0, color="#bdbdbd", linewidth=1)
    ax.axvline(0, color="#d0d0d0", linewidth=1)
    ax.grid(axis="y", color="#dddddd", linestyle=(0, (3, 4)))
    ax.grid(axis="x", visible=False)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_xlabel("6-month return (%)", fontsize=12, color="#777777", labelpad=10)
    ax.set_ylabel("Estimated S&P 500 point contribution", fontsize=12, color="#777777", labelpad=10)
    ax.tick_params(colors="#888888")

    ax.set_title(
        "Hateful Eight vs S&P 500: Rolling 6-Month Contribution",
        loc="left",
        pad=14,
        fontsize=24,
        fontweight="bold",
        color="#1f1f1f",
    )
    ax.text(
        0.0,
        1.01,
        f"Weekly snapshots over the last year • Window: {window_start.strftime('%b %d, %Y')} to {frame_end.strftime('%b %d, %Y')}",
        transform=ax.transAxes,
        fontsize=12,
        color="#555555",
    )

    ax.legend(loc="upper right", frameon=False, fontsize=11)
    fig.text(0.02, 0.02, "Sources: yfinance, paulkedrosky.com", fontsize=10, color="#9a9a9a")

    plt.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(out_png, facecolor=fig.get_facecolor())
    plt.close(fig)


def build_animation() -> None:
    end = pd.Timestamp(date.today())
    start_frames = end - pd.Timedelta(days=365)
    frame_ends = pd.date_range(start=start_frames, end=end, freq="W-FRI")
    if frame_ends.empty:
        raise RuntimeError("No weekly frame dates generated.")

    hist_start = frame_ends.min() - pd.Timedelta(days=190)
    tickers = get_tickers()
    prices, spx_close = load_prices(tickers, hist_start, end)
    shares = load_shares(tickers)

    tables = []
    for d in frame_ends:
        t = frame_table(prices, spx_close, shares, tickers, d)
        if not t.empty:
            tables.append(t)
    if not tables:
        raise RuntimeError("No frame data could be constructed.")

    all_df = pd.concat(tables, ignore_index=True)
    all_df.to_csv(OUT_SUMMARY, index=False)

    x_min = min(-35.0, float(all_df["ret_pct"].quantile(0.02)))
    x_max = max(80.0, float(all_df["ret_pct"].quantile(0.98)))
    y_min = min(-65.0, float(all_df["pts"].quantile(0.01)))
    y_max = max(60.0, float(all_df["pts"].quantile(0.99)))
    xlim = (x_min, x_max)
    ylim = (y_min, y_max)

    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    frame_files = []
    for i, d in enumerate(sorted(all_df["frame_end"].drop_duplicates())):
        frame_end = pd.Timestamp(d)
        sub = all_df[all_df["frame_end"] == frame_end].copy()
        window_start = pd.Timestamp(sub["window_start"].iloc[0])
        out_png = TMP_DIR / f"frame_{i:03d}.png"
        render_frame(sub, out_png, xlim, ylim, frame_end, window_start)
        frame_files.append(out_png)

    images = [Image.open(p).convert("P", palette=Image.ADAPTIVE) for p in frame_files]
    images[0].save(
        OUT_GIF,
        save_all=True,
        append_images=images[1:],
        duration=220,
        loop=0,
        optimize=False,
    )

    print(f"Wrote {OUT_GIF}")
    print(f"Wrote {OUT_SUMMARY}")
    print(f"Frames: {len(frame_files)} | Weekly period: {frame_ends.min().date()} to {frame_ends.max().date()}")


if __name__ == "__main__":
    build_animation()
