# hateful-eight

Financial charting repo for the "Hateful Eight" (Mag 7 + Oracle), focused on return vs estimated S&P 500 point contribution.

## What This Generates

- `hateful-eight-ytd*.csv` and `hateful-eight-ytd.png` via `hateful_eight_chart.py`
- `hateful-eight-rolling-6m-weekly.gif` and summary CSV via `animate_hateful_eight.py`
- `hateful-eight-interactive.html` and interactive CSV via `build_hateful_eight_interactive.py`

## Local Run

```bash
python3 hateful_eight_chart.py --year 2026 --csv hateful-eight-ytd-latest.csv --png hateful-eight-ytd.png
python3 animate_hateful_eight.py
python3 build_hateful_eight_interactive.py
./scripts/build_dist.sh
```

## Production Path (Ghost + paywall)

- Ghost route page: `/tools/hateful-eight/` (template: `hateful-eight`)
- App asset path: `/tools/hateful-eight/app/`
- Served from: `/srv/repos/hateful-eight/dist/`

See:

- `ops/ghost/hateful-eight.hbs`
- `ops/ghost/routes-snippet.yaml`
- `ops/nginx/44-tools-hateful-eight.conf`
- `ops/DEPLOY.md`
