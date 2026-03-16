#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf


ROOT = Path("/Users/pk/dev/hateful-eight")
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

    asof = pd.Timestamp(spx_close.index[-1])
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

    frames_by_window: dict[str, list[dict]] = {}
    for lookback, ldf in df.groupby("lookback", sort=False):
        frames = []
        for frame_end, fdf in ldf.groupby("frame_end", sort=True):
            window_start = pd.Timestamp(fdf["window_start"].iloc[0])
            points = [
                [r.ticker, round(float(r.ret_pct), 4), round(float(r.pts), 4), r.group]
                for r in fdf.itertuples(index=False)
            ]
            frames.append(
                {
                    "end": pd.Timestamp(frame_end).strftime("%Y-%m-%d"),
                    "start": window_start.strftime("%Y-%m-%d"),
                    "points": points,
                }
            )
        frames_by_window[lookback] = frames

    payload = {
        "title": "Hateful Eight vs S&P 500: Weekly Rolling Contribution",
        "subtitle": "Choose 3M / 6M / 1Y window and play weekly snapshots over the last year",
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
  @import url('https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@300;400;500;600;700&family=Merriweather:wght@700&display=swap');
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Libre Franklin', -apple-system, sans-serif;
    background: #f4f1eb;
    color: #1a1a1a;
    padding: 20px;
  }
  .card {
    max-width: 1100px;
    margin: 0 auto;
    background: #FFFCF7;
    border: 1px solid #d6d0c4;
  }
  .top-rule { height: 4px; background: #1a1a1a; }
  .header { padding: 18px 22px 14px; }
  h1 {
    font-family: 'Merriweather', Georgia, serif;
    font-size: 42px;
    line-height: 1.15;
    margin-bottom: 8px;
  }
  .sub {
    color: #4e4e4e;
    font-size: 22px;
    font-weight: 300;
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
  .impact-box {
    border: 1px solid #d8d0c3;
    background: #f9f6f0;
    border-radius: 8px;
    padding: 10px 12px;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
  }
  .impact-item { min-width: 0; }
  .impact-label {
    color: #6c6c6c;
    font-size: 12px;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    margin-bottom: 3px;
  }
  .impact-value {
    color: #1f1f1f;
    font-size: 19px;
    font-weight: 600;
    line-height: 1.15;
  }
  .impact-sub {
    color: #7a7a7a;
    font-size: 12px;
    margin-top: 2px;
  }
  .impact-value.pos, .impact-sub.pos { color: #1b7f3b; }
  .impact-value.neg, .impact-sub.neg { color: #b33a2f; }
  .impact-value.neu, .impact-sub.neu { color: #6a6a6a; }
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
    border: 1px solid #b9b1a5;
    background: #fff;
    color: #222;
    border-radius: 6px;
    font-size: 14px;
    padding: 7px 12px;
    cursor: pointer;
  }
  .btn:hover { background: #f8f4ec; }
  .window-toggle {
    display: inline-flex;
    gap: 4px;
    background: #f4efe6;
    border: 1px solid #d8d0c3;
    border-radius: 8px;
    padding: 3px;
  }
  .window-btn {
    border: none;
    background: transparent;
    color: #4a4a4a;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .window-btn.active {
    background: #fff;
    color: #222;
    box-shadow: 0 1px 2px rgba(0,0,0,0.08);
  }
  .slider {
    width: 100%;
    accent-color: #2E5B88;
  }
  .frame-date {
    font-weight: 600;
    color: #333;
    min-width: 126px;
    text-align: right;
    font-size: 14px;
  }
  .window {
    color: #666;
    font-size: 13px;
    min-width: 260px;
    text-align: right;
  }
  .chart-wrap { padding: 0 16px 0; }
  svg { width: 100%; height: auto; display: block; }
  .footer {
    border-top: 1px solid #d6d0c4;
    padding: 8px 22px;
    display: flex;
    justify-content: space-between;
    color: #888;
    font-size: 11px;
  }
</style>
</head>
<body>
  <div class="card">
    <div class="top-rule"></div>
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

titleEl.textContent = DATA.title;
subtitleEl.textContent = DATA.subtitle;
footnoteEl.textContent = 'Estimated contribution uses index-weight approximation. YTD S&P base: ' + DATA.spxBase.toFixed(2) + '. Data as of ' + DATA.asOf + '.';

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
function renderImpact(frame) {
  let h8Pts = 0;
  let otherPts = 0;
  for (const p of frame.points) {
    if (p[3] === 'h8') h8Pts += p[2];
    else otherPts += p[2];
  }
  const totalPts = h8Pts + otherPts;
  const h8Pct = DATA.spxBase ? (h8Pts / DATA.spxBase) * 100 : 0;
  const totalPct = DATA.spxBase ? (totalPts / DATA.spxBase) * 100 : 0;
  const h8Share = totalPts !== 0 ? (h8Pts / totalPts) * 100 : 0;
  const h8Tone = toneClass(h8Pts);
  const otherTone = toneClass(otherPts);
  const netTone = toneClass(totalPts);
  const h8PctTone = toneClass(h8Pct);
  const h8ShareTone = toneClass(h8Share);
  const netPctTone = toneClass(totalPct);
  impactBoxEl.innerHTML = `
    <div class="impact-item">
      <div class="impact-label">Hateful Eight</div>
      <div class="impact-value ${h8Tone}">${fmtSigned(h8Pts, 1)} pts</div>
      <div class="impact-sub ${h8PctTone}">${fmtSigned(h8Pct, 2)}% of S&P</div>
    </div>
    <div class="impact-item">
      <div class="impact-label">Rest Of S&P 500</div>
      <div class="impact-value ${otherTone}">${fmtSigned(otherPts, 1)} pts</div>
      <div class="impact-sub ${h8ShareTone}">${fmtSigned(h8Share, 1)}% H8 share of net move</div>
    </div>
    <div class="impact-item">
      <div class="impact-label">Net Modeled Move</div>
      <div class="impact-value ${netTone}">${fmtSigned(totalPts, 1)} pts</div>
      <div class="impact-sub ${netPctTone}">${fmtSigned(totalPct, 2)}% over selected window</div>
    </div>
  `;
}
function postHeight() {
  if (window.parent === window) return;
  const h = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
  window.parent.postMessage({ type: 'hateful-eight:height', height: h }, window.location.origin);
}

el('rect', { x: M.left, y: M.top, width: CW, height: CH, fill: '#FFFCF7' });

const yStep = 20;
for (let yv = Math.ceil(Y_MIN / yStep) * yStep; yv <= Y_MAX; yv += yStep) {
  const y = yScale(yv);
  el('line', {
    x1: M.left, x2: M.left + CW, y1: y, y2: y,
    stroke: yv === 0 ? '#b8b8b8' : '#d7d7d7',
    'stroke-width': yv === 0 ? 1.1 : 0.8,
    'stroke-dasharray': yv === 0 ? 'none' : '3,4'
  });
  txt(sign(yv), {
    x: M.left - 8, y, dy: '0.35em', 'text-anchor': 'end',
    'font-family': "'Libre Franklin', sans-serif", 'font-size': 15, fill: '#8a8a8a'
  });
}

const xTicks = [-20, 0, 20, 40, 60, 80];
for (const xv of xTicks) {
  const x = xScale(xv);
  el('line', { x1: x, x2: x, y1: M.top + CH, y2: M.top + CH + 5, stroke: '#b8b8b8', 'stroke-width': 1 });
  txt(xv === 0 ? '0%' : ((xv > 0 ? '+' : '') + xv + '%'), {
    x, y: M.top + CH + 22, 'text-anchor': 'middle',
    'font-family': "'Libre Franklin', sans-serif", 'font-size': 15, fill: '#8a8a8a'
  });
}

el('line', { x1: M.left, x2: M.left + CW, y1: yScale(0), y2: yScale(0), stroke: '#b8b8b8', 'stroke-width': 1.1 });
el('line', { x1: xScale(0), x2: xScale(0), y1: M.top, y2: M.top + CH, stroke: '#d0d0d0', 'stroke-width': 1 });
el('line', { x1: M.left, x2: M.left + CW, y1: M.top + CH, y2: M.top + CH, stroke: '#b8b8b8', 'stroke-width': 1 });
el('line', { x1: M.left, x2: M.left, y1: M.top, y2: M.top + CH, stroke: '#b8b8b8', 'stroke-width': 1 });

const xAxisLabel = txt('', {
  x: M.left + CW / 2, y: H - 28, 'text-anchor': 'middle',
  'font-family': "'Libre Franklin', sans-serif", 'font-size': 17, fill: '#777'
});

const yAxisLabel = txt('Estimated S&P 500 point contribution', {
  x: 0, y: 0, 'text-anchor': 'middle',
  'font-family': "'Libre Franklin', sans-serif", 'font-size': 17, fill: '#777'
});
yAxisLabel.setAttribute('transform', 'translate(30,' + (M.top + CH / 2) + ') rotate(-90)');

txt('† extreme x-axis outliers are clipped', {
  x: M.left + CW, y: M.top - 9, 'text-anchor': 'end',
  'font-family': "'Libre Franklin', sans-serif", 'font-size': 13, fill: '#b1b1b1', 'font-style': 'italic'
});

const lg = el('g', {});
const legendItems = [
  { label: 'S&P 500', fill: '#2E5B88', op: 0.28, stroke: '#2E5B88' },
  { label: 'Hateful Eight', fill: '#C4533A', op: 0.9, stroke: '#C4533A' },
];
let ly = M.top + 20;
for (const it of legendItems) {
  el('circle', { cx: M.left + CW - 180, cy: ly, r: 5, fill: it.fill, 'fill-opacity': it.op, stroke: it.stroke, 'stroke-width': 1 }, lg);
  txt(it.label, {
    x: M.left + CW - 166, y: ly + 5,
    'font-family': "'Libre Franklin', sans-serif", 'font-size': 16, fill: '#555'
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
  const left = [];
  const right = [];

  for (const p of h8Points) {
    const sideRight = p.x < M.left + CW - 60;
    const item = {
      ticker: p.ticker,
      x: p.x,
      y: p.y,
      sideRight,
      lx: sideRight ? p.x + 7 : p.x - 7,
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
      stroke: '#B05A4A',
      'stroke-width': 0.9,
      'stroke-opacity': 0.75
    }, labelsLayer);
    txt(p.ticker, {
      x: p.lx, y: p.ly,
      'text-anchor': p.anchor,
      'font-family': "'Libre Franklin', sans-serif",
      'font-size': 14,
      'font-weight': 600,
      fill: '#C4533A'
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
  while (pointsLayer.firstChild) pointsLayer.removeChild(pointsLayer.firstChild);
  const h8 = [];
  for (const p of frame.points) {
    const ticker = p[0], ret = p[1], pts = p[2], grp = p[3];
    const x = xScale(ret), y = yScale(pts);
    const isH8 = grp === 'h8';
    el('circle', {
      cx: x, cy: y, r: 4.5,
      fill: isH8 ? '#C4533A' : '#2E5B88',
      'fill-opacity': isH8 ? 0.9 : 0.28,
      stroke: isH8 ? '#C4533A' : '#2E5B88',
      'stroke-width': isH8 ? 1.0 : 0.8
    }, pointsLayer);
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
