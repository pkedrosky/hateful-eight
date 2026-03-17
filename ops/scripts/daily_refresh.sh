#!/usr/bin/env bash
set -euo pipefail

cd /srv/repos/hateful-eight

# Rebuild interactive HTML from fresh market data and write only the served asset.
PYTHON_BIN="python3"
if [[ -x /srv/repos/hateful-eight/.venv/bin/python ]]; then
  PYTHON_BIN="/srv/repos/hateful-eight/.venv/bin/python"
fi

"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import build_hateful_eight_interactive as app

df, asof, spx_base = app.build_dataset()
html = app.build_html(df, asof, spx_base)

out = Path("dist/index.html")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(html)

print(f"Wrote {out} | asOf={asof:%Y-%m-%d} | spxBase={spx_base:.2f}")
PY
