#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf


ROOT = Path(__file__).resolve().parent
UNIVERSE_CSV = ROOT / "hateful-eight-ytd-latest.csv"
OUT_CSV = ROOT / "hateful-eight-weekly-interactive.csv"
OUT_HTML = ROOT / "hateful-eight-interactive.html"

MAG7 = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"}
HATEFUL8 = MAG7 | {"ORCL"}
WINDOWS = {"3m": 91, "6m": 182, "1y": 365}


def get_tickers() -> list[str]:
    if not UNIVERSE_CSV.exists():
        raise FileNotFoundError(f"Missing ticker universe file: {UNIVERSE_CSV}")
    df = pd.read_csv(UNIVERSE_CSV)
    return sorted(df["ticker"].dropna().astype(str).str.replace(".", "-", regex=False).unique().tolist())


def scalar_float(v) -> float:
    if hasattr(v, "item"):
        return float(v.item())
    return float(v)


def close_at_or_before(series: pd.Series, asof: pd.Timestamp) -> float | None:
    s = series.dropna()
    if s.empty:
        return None
    idx = s.index.searchsorted(asof, side="right") - 1
    if idx < 0:
        return None
    return scalar_float(s.iloc[idx])


def round_down(x: float, step: float) -> float:
    return float(step * (x // step))


def round_up(x: float, step: float) -> float:
    return float(step * -(-x // step))


def build_dataset() -> tuple[pd.DataFrame, pd.Timestamp, float]:
    tickers = get_tickers()

    end = pd.Timestamp(date.today())
    start_frames = end - pd.Timedelta(days=365)
    frame_ends = pd.date_range(start=start_frames, end=end, freq="W-FRI")
    if frame_ends.empty:
        raise RuntimeError("No weekly frame dates generated.")

    hist_start = frame_ends.min() - pd.Timedelta(days=max(WINDOWS.values()) + 20)

    px = yf.download(
        tickers=tickers,
        start=hist_start.strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        auto_adjust=True,
        progress=False,
        threads=True,
        group_by="ticker",
    )
    spx = yf.download(
        "^GSPC",
        start=hist_start.strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        auto_adjust=True,
        progress=False,
        threads=False,
    )

    spx_close = spx["Close"].dropna()
    if spx_close.empty:
        raise RuntimeError("No S&P 500 price history downloaded.")

    asof = pd.Timestamp(spx_close.index[-1]).normalize()
    if asof > frame_ends.max():
        frame_ends = frame_ends.append(pd.DatetimeIndex([asof]))
    start_of_year = pd.Timestamp(f"{asof.year}-01-01")
    spx_base = close_at_or_before(spx_close, start_of_year)
    if spx_base is None:
        raise RuntimeError("Could not determine S&P base at start of year.")

    shares = {}
    objs = yf.Tickers(" ".join(tickers)).tickers
    for t in tickers:
        val = None
        obj = objs.get(t)
        if obj is not None:
            try:
                val = obj.fast_info.get("shares")
            except Exception:
                val = None
            if not val:
                try:
                    val = obj.info.get("sharesOutstanding")
                except Exception:
                    val = None
        shares[t] = float(val) if val else float("nan")
    shares_s = pd.Series(shares, name="shares")

    if not isinstance(px.columns, pd.MultiIndex):
        raise RuntimeError("Expected multi-ticker download structure from yfinance.")

    close_series = {t: px[(t, "Close")] for t in tickers if (t, "Close") in px.columns}

    rows = []
    for frame_end in frame_ends:
        for lookback, days in WINDOWS.items():
            window_start = frame_end - pd.Timedelta(days=days)
            spx_window_base = close_at_or_before(spx_close, window_start)
            if spx_window_base is None:
                continue

            frame_rows = []
            for t, s in close_series.items():
                px0 = close_at_or_before(s, window_start)
                px1 = close_at_or_before(s, frame_end)
                if px0 is None or px1 is None or px0 == 0:
                    continue
                ret = (px1 / px0 - 1.0) * 100.0
                sh = shares_s.get(t)
                if pd.isna(sh):
                    continue
                mcap = sh * px1
                frame_rows.append(
                    {
                        "lookback": lookback,
                        "frame_end": frame_end,
                        "window_start": window_start,
                        "ticker": t,
                        "group": "h8" if t in HATEFUL8 else "other",
                        "ret_pct": ret,
                        "market_cap": mcap,
                        "spx_window_base": spx_window_base,
                    }
                )

            if not frame_rows:
                continue
            fdf = pd.DataFrame(frame_rows)
            fdf["weight"] = fdf["market_cap"] / fdf["market_cap"].sum()
            fdf["pts"] = fdf["weight"] * (fdf["ret_pct"] / 100.0) * fdf["spx_window_base"]
            rows.append(fdf)

    if not rows:
        raise RuntimeError("No frame rows generated.")

    out = pd.concat(rows, ignore_index=True)
    out = out.sort_values(["lookback", "frame_end", "group", "ticker"]).reset_index(drop=True)
    return out, asof, spx_base


def build_html(df: pd.DataFrame, asof: pd.Timestamp, spx_base: float) -> str:
    h8 = df[df["group"] == "h8"]

    x_min = -30.0
    x_max = 80.0
    pts_low = min(float(df["pts"].min()), float(h8["pts"].min()))
    pts_high = max(float(df["pts"].max()), float(h8["pts"].max()))
    y_pad = max(5.0, (pts_high - pts_low) * 0.1)
    y_min = round_down(pts_low - y_pad, 10.0)
    y_max = round_up(pts_high + y_pad, 10.0)
    # Keep vertical framing from becoming top-heavy; clip extreme positive outliers if needed.
    y_max = min(y_max, 300.0)

    frames_by_window: dict[str, list[dict]] = {}
    for lookback, ldf in df.groupby("lookback", sort=False):
        frames = []
        for frame_end, fdf in ldf.groupby("frame_end", sort=True):
            window_start = pd.Timestamp(fdf["window_start"].iloc[0])
            spx_window_base = float(fdf["spx_window_base"].iloc[0])
            points = [
                [r.ticker, round(float(r.ret_pct), 4), round(float(r.pts), 4), r.group]
                for r in fdf.itertuples(index=False)
            ]
            frames.append(
                {
                    "end": pd.Timestamp(frame_end).strftime("%Y-%m-%d"),
                    "start": window_start.strftime("%Y-%m-%d"),
                    "spxBase": round(spx_window_base, 4),
                    "points": points,
                }
            )
        frames_by_window[lookback] = frames

    payload = {
        "title": "Hateful Eight vs S&P 500: Weekly Rolling Contribution",
        "subtitle": "Choose 3M / 6M / 1Y window and play weekly snapshots over the last year (plus latest close)",
        "xMin": x_min,
        "xMax": x_max,
        "yMin": y_min,
        "yMax": y_max,
        "asOf": asof.strftime("%b. %d, %Y"),
        "spxBase": round(float(spx_base), 2),
        "windowLabels": {"3m": "3-month", "6m": "6-month", "1y": "1-year"},
        "windowOrder": ["3m", "6m", "1y"],
        "defaultWindow": "6m",
        "framesByWindow": frames_by_window,
    }
    data_js = json.dumps(payload, separators=(",", ":"))

    template = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Hateful Eight Interactive</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&display=swap');
  :root {
    --bg: #f6f8fb;
    --surface: #ffffff;
    --panel-border: #d9e1ea;
    --text: #0f172a;
    --muted: #64748b;
    --accent: #1f6fdb;
    --accent-soft: #eff6ff;
    --good: #15803d;
    --bad: #b91c1c;
    --neutral: #64748b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 20px;
  }
  .card {
    max-width: 1100px;
    margin: 0 auto;
    background: var(--surface);
    border: 1px solid var(--panel-border);
    border-radius: 12px;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
    overflow: hidden;
  }
  .header { padding: 18px 22px 14px; }
  h1 {
    font-size: clamp(30px, 5vw, 46px);
    line-height: 1.08;
    letter-spacing: -0.025em;
    font-weight: 800;
    margin-bottom: 6px;
  }
  .sub {
    color: var(--muted);
    font-size: clamp(14px, 2vw, 20px);
    font-weight: 500;
    margin-bottom: 14px;
  }
  .controls {
    display: grid;
    grid-template-columns: auto auto 1fr auto auto;
    gap: 10px;
    align-items: center;
    margin-top: 8px;
  }
  .metrics-row { margin-top: 10px; }
  .metrics-title {
    color: var(--muted);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    margin: 0 0 6px;
  }
  .impact-box {
    border: 1px solid var(--panel-border);
    background: #fbfdff;
    border-radius: 10px;
    padding: 10px 12px;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
  }
  .impact-item { min-width: 0; }
  .impact-label {
    color: var(--muted);
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 2px;
  }
  .impact-metric {
    color: #64748b;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.01em;
    margin-bottom: 3px;
  }
  .impact-value {
    color: var(--text);
    font-size: 26px;
    font-weight: 700;
    line-height: 1.1;
  }
  .impact-sub {
    color: var(--muted);
    font-size: 12px;
    font-weight: 600;
    margin-top: 2px;
  }
  .impact-value.pos, .impact-sub.pos { color: var(--good); }
  .impact-value.neg, .impact-sub.neg { color: var(--bad); }
  .impact-value.neu, .impact-sub.neu { color: var(--neutral); }
  @media (max-width: 920px) {
    .controls {
      grid-template-columns: 1fr 1fr;
      grid-template-areas:
        "play toggle"
        "slider slider"
        "date date"
        "range range";
    }
    #playBtn { grid-area: play; }
    .window-toggle { grid-area: toggle; justify-self: end; }
    #frameSlider { grid-area: slider; }
    #frameDate { grid-area: date; text-align: left; }
    #windowRange { grid-area: range; text-align: left; min-width: 0; }
    .impact-box { grid-template-columns: 1fr; }
  }
  .btn {
    border: 1px solid var(--panel-border);
    background: #fff;
    color: #334155;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
    padding: 7px 12px;
    cursor: pointer;
  }
  .btn:hover {
    background: #f8fafc;
    border-color: #cbd5e1;
  }
  .window-toggle {
    display: inline-flex;
    gap: 4px;
    background: #f8fafc;
    border: 1px solid var(--panel-border);
    border-radius: 999px;
    padding: 3px;
  }
  .window-btn {
    border: 1px solid transparent;
    background: transparent;
    color: #475569;
    border-radius: 999px;
    padding: 5px 10px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .window-btn.active {
    border-color: #93c5fd;
    background: var(--accent-soft);
    color: #1d4ed8;
    box-shadow: 0 1px 1px rgba(30, 64, 175, 0.08);
  }
  .slider {
    width: 100%;
    accent-color: var(--accent);
  }
  .frame-date {
    font-weight: 600;
    color: #334155;
    min-width: 126px;
    text-align: right;
    font-size: 14px;
  }
  .window {
    color: var(--muted);
    font-size: 12px;
    min-width: 260px;
    text-align: right;
  }
  .chart-wrap {
    position: relative;
    padding: 0 14px 4px;
  }
  .hover-tip {
    position: absolute;
    top: 0;
    left: 0;
    transform: translate(-9999px, -9999px);
    background: rgba(15, 23, 42, 0.96);
    color: #f8fafc;
    border-radius: 8px;
    padding: 6px 8px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.01em;
    pointer-events: none;
    white-space: nowrap;
    z-index: 20;
    opacity: 0;
    transition: opacity 120ms ease;
  }
  .hover-tip.show { opacity: 1; }
  svg { width: 100%; height: auto; display: block; }
  .footer {
    border-top: 1px solid var(--panel-border);
    padding: 8px 22px;
    display: flex;
    justify-content: space-between;
    gap: 10px;
    color: var(--muted);
    font-size: 12px;
    flex-wrap: wrap;
  }
  @media (max-width: 640px) {
    body { padding: 12px; }
    .header { padding: 14px 14px 12px; }
    .chart-wrap { padding: 0 8px 4px; }
    .footer { padding: 8px 14px; font-size: 11px; }
  }
</style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1 id="title"></h1>
      <div id="subtitle" class="sub"></div>
      <div class="controls">
        <button id="playBtn" class="btn">Play</button>
        <div class="window-toggle">
          <button class="window-btn" data-window="3m">3M</button>
          <button class="window-btn active" data-window="6m">6M</button>
          <button class="window-btn" data-window="1y">1Y</button>
        </div>
        <input id="frameSlider" class="slider" type="range" min="0" max="0" value="0" step="1" />
        <div id="frameDate" class="frame-date"></div>
        <div id="windowRange" class="window"></div>
      </div>
      <div class="metrics-row">
        <div class="metrics-title">Contribution Over Period</div>
        <div id="impactBox" class="impact-box"></div>
      </div>
    </div>
    <div class="chart-wrap">
      <svg id="chart" viewBox="0 0 1040 720" preserveAspectRatio="xMidYMid meet"></svg>
    </div>
    <div class="footer">
      <div>Sources: yfinance, paulkedrosky.com</div>
      <div id="footnote"></div>
    </div>
  </div>

<script>
const DATA = __DATA__;

const svg = document.getElementById('chart');
const titleEl = document.getElementById('title');
const subtitleEl = document.getElementById('subtitle');
const playBtn = document.getElementById('playBtn');
const slider = document.getElementById('frameSlider');
const frameDateEl = document.getElementById('frameDate');
const windowEl = document.getElementById('windowRange');
const footnoteEl = document.getElementById('footnote');
const impactBoxEl = document.getElementById('impactBox');
const windowBtns = Array.from(document.querySelectorAll('.window-btn'));
const chartWrapEl = document.querySelector('.chart-wrap');
const hoverTipEl = document.createElement('div');
hoverTipEl.className = 'hover-tip';
chartWrapEl.appendChild(hoverTipEl);

titleEl.textContent = DATA.title;
subtitleEl.textContent = DATA.subtitle;
footnoteEl.textContent = 'First two % values are contribution shares of gross move; Aggregate % is the S&P 500 return for the selected window. Data as of ' + DATA.asOf + '.';

const W = 1040, H = 720;
const M = { left: 90, right: 58, top: 56, bottom: 86 };
const CW = W - M.left - M.right;
const CH = H - M.top - M.bottom;

const X_MIN = DATA.xMin, X_MAX = DATA.xMax;
const Y_MIN = DATA.yMin, Y_MAX = DATA.yMax;

function xScale(v) {
  const vv = Math.max(X_MIN, Math.min(X_MAX, v));
  return M.left + ((vv - X_MIN) / (X_MAX - X_MIN)) * CW;
}
function yScale(v) {
  const vv = Math.max(Y_MIN, Math.min(Y_MAX, v));
  return M.top + CH - ((vv - Y_MIN) / (Y_MAX - Y_MIN)) * CH;
}
function el(tag, attrs, parent = svg) {
  const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  parent.appendChild(e);
  return e;
}
function txt(content, attrs, parent = svg) {
  const t = el('text', attrs, parent);
  t.textContent = content;
  return t;
}
function fmtDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
function sign(v) {
  return v > 0 ? ('+' + v) : String(v);
}
function fmtSigned(v, digits = 1) {
  return (v >= 0 ? '+' : '-') + Math.abs(v).toFixed(digits);
}
function toneClass(v) {
  if (v > 0) return 'pos';
  if (v < 0) return 'neg';
  return 'neu';
}
function hideHoverTip() {
  hoverTipEl.classList.remove('show');
  hoverTipEl.style.transform = 'translate(-9999px, -9999px)';
}
function moveHoverTip(event) {
  if (!hoverTipEl.classList.contains('show')) return;
  const rect = chartWrapEl.getBoundingClientRect();
  const pad = 10;
  const tipW = hoverTipEl.offsetWidth || 90;
  const tipH = hoverTipEl.offsetHeight || 26;
  let x = event.clientX - rect.left + 12;
  let y = event.clientY - rect.top - tipH - 10;

  if (x + tipW + pad > rect.width) x = rect.width - tipW - pad;
  if (x < pad) x = pad;
  if (y < pad) y = event.clientY - rect.top + 14;
  if (y + tipH + pad > rect.height) y = rect.height - tipH - pad;

  hoverTipEl.style.transform = 'translate(' + x + 'px,' + y + 'px)';
}
function showHoverTip(event, ticker, ret) {
  hoverTipEl.textContent = ticker + '  ' + fmtSigned(ret, 1) + '%';
  hoverTipEl.classList.add('show');
  moveHoverTip(event);
}
function renderImpact(frame) {
  let h8Pts = 0;
  let otherPts = 0;
  for (const p of frame.points) {
    if (p[3] === 'h8') h8Pts += p[2];
    else otherPts += p[2];
  }
  const totalPts = h8Pts + otherPts;
  const baseForFrame = frame.spxBase || DATA.spxBase;
  const grossPts = Math.abs(h8Pts) + Math.abs(otherPts);
  const h8Pct = grossPts ? (h8Pts / grossPts) * 100 : 0;
  const otherPct = grossPts ? (otherPts / grossPts) * 100 : 0;
  const totalPct = baseForFrame ? (totalPts / baseForFrame) * 100 : 0;
  const h8Tone = toneClass(h8Pts);
  const otherTone = toneClass(otherPts);
  const netTone = toneClass(totalPct);
  const h8PtsTone = toneClass(h8Pts);
  const otherPtsTone = toneClass(otherPts);
  const netPtsTone = toneClass(totalPts);
  impactBoxEl.innerHTML = `
    <div class="impact-item">
      <div class="impact-label">Hateful Eight</div>
      <div class="impact-metric">Share of Gross Move</div>
      <div class="impact-value ${h8Tone}">${fmtSigned(h8Pct, 1)}%</div>
      <div class="impact-sub ${h8PtsTone}">${fmtSigned(h8Pts, 1)} contribution pts</div>
    </div>
    <div class="impact-item">
      <div class="impact-label">Rest of S&P 500</div>
      <div class="impact-metric">Share of Gross Move</div>
      <div class="impact-value ${otherTone}">${fmtSigned(otherPct, 1)}%</div>
      <div class="impact-sub ${otherPtsTone}">${fmtSigned(otherPts, 1)} contribution pts</div>
    </div>
    <div class="impact-item">
      <div class="impact-label">Aggregate S&P Move</div>
      <div class="impact-metric">S&P 500 Return</div>
      <div class="impact-value ${netTone}">${fmtSigned(totalPct, 1)}%</div>
      <div class="impact-sub ${netPtsTone}">${fmtSigned(totalPts, 1)} index pts</div>
    </div>
  `;
}
function postHeight() {
  if (window.parent === window) return;
  const h = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
  window.parent.postMessage({ type: 'hateful-eight:height', height: h }, window.location.origin);
}

el('rect', { x: M.left, y: M.top, width: CW, height: CH, fill: '#ffffff' });

const yStep = 20;
for (let yv = Math.ceil(Y_MIN / yStep) * yStep; yv <= Y_MAX; yv += yStep) {
  const y = yScale(yv);
  el('line', {
    x1: M.left, x2: M.left + CW, y1: y, y2: y,
    stroke: yv === 0 ? '#94a3b8' : '#d1dae6',
    'stroke-width': yv === 0 ? 1.1 : 0.8,
    'stroke-dasharray': yv === 0 ? 'none' : '3,4'
  });
  txt(sign(yv), {
    x: M.left - 8, y, dy: '0.35em', 'text-anchor': 'end',
    'font-family': "'Geist', sans-serif", 'font-size': 15, fill: '#64748b'
  });
}

const xTicks = [-20, 0, 20, 40, 60, 80];
for (const xv of xTicks) {
  const x = xScale(xv);
  el('line', { x1: x, x2: x, y1: M.top + CH, y2: M.top + CH + 5, stroke: '#94a3b8', 'stroke-width': 1 });
  txt(xv === 0 ? '0%' : ((xv > 0 ? '+' : '') + xv + '%'), {
    x, y: M.top + CH + 22, 'text-anchor': 'middle',
    'font-family': "'Geist', sans-serif", 'font-size': 15, fill: '#64748b'
  });
}

el('line', { x1: M.left, x2: M.left + CW, y1: yScale(0), y2: yScale(0), stroke: '#94a3b8', 'stroke-width': 1.1 });
el('line', { x1: xScale(0), x2: xScale(0), y1: M.top, y2: M.top + CH, stroke: '#cbd5e1', 'stroke-width': 1 });
el('line', { x1: M.left, x2: M.left + CW, y1: M.top + CH, y2: M.top + CH, stroke: '#94a3b8', 'stroke-width': 1 });
el('line', { x1: M.left, x2: M.left, y1: M.top, y2: M.top + CH, stroke: '#94a3b8', 'stroke-width': 1 });

const xAxisLabel = txt('', {
  x: M.left + CW / 2, y: H - 28, 'text-anchor': 'middle',
  'font-family': "'Geist', sans-serif", 'font-size': 17, fill: '#475569'
});

const yAxisLabel = txt('S&P 500 point contribution', {
  x: 0, y: 0, 'text-anchor': 'middle',
  'font-family': "'Geist', sans-serif", 'font-size': 17, fill: '#475569'
});
yAxisLabel.setAttribute('transform', 'translate(30,' + (M.top + CH / 2) + ') rotate(-90)');

txt('† extreme x-axis outliers are clipped', {
  x: M.left + CW, y: M.top - 9, 'text-anchor': 'end',
  'font-family': "'Geist', sans-serif", 'font-size': 13, fill: '#94a3b8', 'font-style': 'italic'
});

const lg = el('g', {});
const legendItems = [
  { label: 'S&P 500', fill: '#1f6fdb', op: 0.24, stroke: '#1f6fdb' },
  { label: 'Hateful Eight', fill: '#d4553b', op: 0.9, stroke: '#d4553b' },
];
let ly = M.top + 20;
for (const it of legendItems) {
  el('circle', { cx: M.left + CW - 180, cy: ly, r: 5, fill: it.fill, 'fill-opacity': it.op, stroke: it.stroke, 'stroke-width': 1 }, lg);
  txt(it.label, {
    x: M.left + CW - 166, y: ly + 5,
    'font-family': "'Geist', sans-serif", 'font-size': 16, fill: '#334155'
  }, lg);
  ly += 24;
}

const pointsLayer = el('g', {});
const labelsLayer = el('g', {});

function placeLabels(h8Points) {
  while (labelsLayer.firstChild) labelsLayer.removeChild(labelsLayer.firstChild);
  const labelH = 12;
  const gap = 3;
  const minY = M.top + 10;
  const maxY = M.top + CH - 4;
  const minX = M.left + 4;
  const maxX = M.left + CW - 4;
  function approxLabelWidth(ticker) {
    return Math.max(24, ticker.length * 7.5 + 4);
  }
  const left = [];
  const right = [];

  for (const p of h8Points) {
    const w = approxLabelWidth(p.ticker);
    const rightRoom = maxX - (p.x + 7);
    const leftRoom = (p.x - 7) - minX;
    const sideRight = rightRoom >= w || rightRoom >= leftRoom;
    const lxRaw = sideRight ? (p.x + 7) : (p.x - 7);
    const lx = sideRight
      ? Math.min(lxRaw, maxX - w)
      : Math.max(lxRaw, minX + w);
    const item = {
      ticker: p.ticker,
      x: p.x,
      y: p.y,
      sideRight,
      lx,
      ly: p.y + 3,
      anchor: sideRight ? 'start' : 'end'
    };
    (sideRight ? right : left).push(item);
  }

  function pack(arr) {
    arr.sort((a,b) => a.ly - b.ly);
    for (let i = 1; i < arr.length; i++) {
      arr[i].ly = Math.max(arr[i].ly, arr[i-1].ly + labelH + gap);
    }
    if (!arr.length) return;
    arr[arr.length - 1].ly = Math.min(arr[arr.length - 1].ly, maxY);
    for (let i = arr.length - 2; i >= 0; i--) {
      arr[i].ly = Math.min(arr[i].ly, arr[i+1].ly - (labelH + gap));
      arr[i].ly = Math.max(arr[i].ly, minY);
    }
  }
  pack(left);
  pack(right);

  for (const p of left.concat(right)) {
    const tx = p.sideRight ? p.lx + 1 : p.lx - 1;
    el('line', {
      x1: p.x + (p.sideRight ? 4.5 : -4.5),
      y1: p.y,
      x2: tx,
      y2: p.ly - 3,
      stroke: '#bf4b34',
      'stroke-width': 0.9,
      'stroke-opacity': 0.75
    }, labelsLayer);
    txt(p.ticker, {
      x: p.lx, y: p.ly,
      'text-anchor': p.anchor,
      'font-family': "'Geist', sans-serif",
      'font-size': 14,
      'font-weight': 600,
      fill: '#d4553b'
    }, labelsLayer);
  }
}

let currentWindow = DATA.defaultWindow;
let frames = DATA.framesByWindow[currentWindow] || [];
let current = Math.max(0, frames.length - 1);
let timer = null;
let playing = false;

function setWindowButtons() {
  for (const btn of windowBtns) {
    btn.classList.toggle('active', btn.dataset.window === currentWindow);
  }
}

function setXAxisLabel() {
  xAxisLabel.textContent = DATA.windowLabels[currentWindow] + ' return (%)';
}

function setSliderBounds() {
  slider.max = String(Math.max(0, frames.length - 1));
  current = Math.min(current, Math.max(0, frames.length - 1));
  slider.value = String(current);
}

function renderFrame(idx) {
  if (!frames.length) return;
  const frame = frames[idx];
  hideHoverTip();
  while (pointsLayer.firstChild) pointsLayer.removeChild(pointsLayer.firstChild);
  const h8 = [];
  for (const p of frame.points) {
    const ticker = p[0], ret = p[1], pts = p[2], grp = p[3];
    const x = xScale(ret), y = yScale(pts);
    const isH8 = grp === 'h8';
    const pointEl = el('circle', {
      cx: x, cy: y, r: 4.5,
      fill: isH8 ? '#d4553b' : '#1f6fdb',
      'fill-opacity': isH8 ? 0.9 : 0.24,
      stroke: isH8 ? '#d4553b' : '#1f6fdb',
      'stroke-width': isH8 ? 1.0 : 0.8
    }, pointsLayer);
    pointEl.addEventListener('mouseenter', (event) => showHoverTip(event, ticker, ret));
    pointEl.addEventListener('mousemove', moveHoverTip);
    pointEl.addEventListener('mouseleave', hideHoverTip);
    if (isH8) h8.push({ ticker, x, y });
  }
  placeLabels(h8);
  renderImpact(frame);
  frameDateEl.textContent = fmtDate(frame.end);
  windowEl.textContent = 'Window: ' + fmtDate(frame.start) + ' to ' + fmtDate(frame.end);
  requestAnimationFrame(postHeight);
}

function stopPlay() {
  if (timer) clearInterval(timer);
  timer = null;
  playing = false;
  playBtn.textContent = 'Play';
}
function startPlay() {
  if (playing || frames.length < 2) return;
  playing = true;
  playBtn.textContent = 'Pause';
  timer = setInterval(() => {
    current = (current + 1) % frames.length;
    slider.value = String(current);
    renderFrame(current);
  }, 550);
}

function switchWindow(nextWindow) {
  if (!DATA.framesByWindow[nextWindow]) return;
  currentWindow = nextWindow;
  frames = DATA.framesByWindow[currentWindow];
  current = Math.max(0, frames.length - 1);
  setWindowButtons();
  setXAxisLabel();
  setSliderBounds();
  renderFrame(current);
}

playBtn.addEventListener('click', () => {
  if (playing) stopPlay();
  else startPlay();
});
slider.addEventListener('input', () => {
  current = Number(slider.value);
  renderFrame(current);
});
for (const btn of windowBtns) {
  btn.addEventListener('click', () => {
    stopPlay();
    switchWindow(btn.dataset.window);
  });
}

window.addEventListener('resize', () => requestAnimationFrame(postHeight));
svg.addEventListener('mouseleave', hideHoverTip);
window.addEventListener('message', (event) => {
  if (event.origin !== window.location.origin) return;
  if (event.data?.type === 'hateful-eight:request-height') postHeight();
});

setWindowButtons();
setXAxisLabel();
setSliderBounds();
renderFrame(current);
setTimeout(postHeight, 120);
</script>
</body>
</html>
"""
    return template.replace("__DATA__", data_js)


def main() -> None:
    df, asof, spx_base = build_dataset()
    df.to_csv(OUT_CSV, index=False)
    html = build_html(df, asof, spx_base)
    OUT_HTML.write_text(html)
    frames = df.groupby("lookback")["frame_end"].nunique().to_dict()
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote {OUT_HTML}")
    print(f"As-of={asof.strftime('%Y-%m-%d')} | Frames by window={frames}")


if __name__ == "__main__":
    main()
