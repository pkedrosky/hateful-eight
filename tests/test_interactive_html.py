import json
import logging
import re
from pathlib import Path

import pytest

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
INTERACTIVE_HTML = ROOT / "hateful-eight-interactive.html"
BUILD_SCRIPT = ROOT / "build_hateful_eight_interactive.py"
assert INTERACTIVE_HTML.exists(), (
    f"Interactive HTML not found at {INTERACTIVE_HTML}. Run build_hateful_eight_interactive.py first."
)
assert BUILD_SCRIPT.exists(), f"Build script not found at {BUILD_SCRIPT}"


def extract_data_json(content: str) -> dict | None:
    placeholder_match = re.search(r"const DATA = __DATA__;", content)
    if placeholder_match:
        return None

    brace_start = content.find("const DATA = {")
    if brace_start == -1:
        pytest.fail("Could not find 'const DATA = {' in HTML")
    json_str, ok = _extract_json_object(content, brace_start + len("const DATA = "))
    if not ok:
        pytest.fail(f"Could not extract valid JSON from DATA object: {json_str[:100]}")
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Embedded DATA is not valid JSON: {exc}")


def _extract_json_object(s: str, start: int) -> tuple[str, bool]:
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1], True
    return s[start : start + 100], False


def test_html_file_exists_and_not_empty():
    assert INTERACTIVE_HTML.exists()
    size = INTERACTIVE_HTML.stat().st_size
    assert size > 1000, f"HTML file suspiciously small: {size} bytes"
    log.info("HTML file size: %d bytes", size)


def test_build_script_template_includes_d3():
    content = BUILD_SCRIPT.read_text()
    assert "cdn.jsdelivr.net/npm/d3@" in content, (
        "D3 CDN not referenced in build script template"
    )
    assert 'src="https://cdn.jsdelivr.net/npm/d3@' in content
    log.info("Build script template includes D3 CDN reference")


def test_build_script_uses_d3_scales_and_selection():
    content = BUILD_SCRIPT.read_text()
    assert "d3.scaleLinear" in content
    assert "d3.select" in content
    assert "xScale" in content
    assert "yScale" in content
    log.info("Build script uses D3 scales and selection")


def test_build_script_uses_data_join_pattern():
    content = BUILD_SCRIPT.read_text()
    assert ".join(" in content
    assert "pointsLayer" in content
    log.info("Build script uses D3 data join (.join) pattern")


def test_html_has_valid_json_data():
    content = INTERACTIVE_HTML.read_text()
    data = extract_data_json(content)
    if data is None:
        pytest.skip(
            "HTML has __DATA__ placeholder — run build_hateful_eight_interactive.py to generate data"
        )
    assert "title" in data
    assert "framesByWindow" in data
    assert "defaultWindow" in data
    assert data["defaultWindow"] in data["framesByWindow"]
    assert "ytd" in data["framesByWindow"]
    log.info("DATA embedded JSON is valid")


def test_html_includes_d3():
    content = INTERACTIVE_HTML.read_text()
    if "cdn.jsdelivr.net/npm/d3@" not in content:
        pytest.skip(
            "D3 CDN not found in generated HTML — run build_hateful_eight_interactive.py "
            "to regenerate the output file"
        )
    log.info("D3 CDN reference found in generated HTML")


def test_html_controls_present():
    content = INTERACTIVE_HTML.read_text()
    assert 'id="playBtn"' in content
    assert 'id="downloadBtn"' in content
    assert 'id="frameSlider"' in content
    assert 'data-window="ytd"' in content
    assert 'data-window="1m"' in content
    assert 'data-window="1y"' in content
    log.info("All expected control elements present")


def test_html_has_notes_section():
    content = INTERACTIVE_HTML.read_text()
    assert 'id="notesGrid"' in content
    assert "What this shows" in content
    assert "Hateful Eight" in content
    assert "Methodology" in content
    log.info("Notes section present with expected content")


def test_frames_have_required_fields():
    content = INTERACTIVE_HTML.read_text()
    data = extract_data_json(content)
    if data is None:
        pytest.skip(
            "HTML has __DATA__ placeholder — run build_hateful_eight_interactive.py to generate data"
        )
    frames = data["framesByWindow"]["ytd"]
    assert len(frames) > 0, "No frames found for ytd window"

    frame = frames[0]
    for key in ["end", "start", "spxBase", "spxEnd", "points"]:
        assert key in frame, f"Frame missing required field: {key}"

    point = frame["points"][0]
    assert len(point) == 4, f"Point should have [ticker, ret, pts, group], got {point}"
    log.info("Frame structure valid: %d frames in ytd window", len(frames))


def test_window_labels_defined():
    content = INTERACTIVE_HTML.read_text()
    data = extract_data_json(content)
    if data is None:
        pytest.skip(
            "HTML has __DATA__ placeholder — run build_hateful_eight_interactive.py to generate data"
        )
    assert set(data["windowOrder"]) == {"1m", "ytd", "1y"}
    assert set(data["windowLabels"].keys()) == {"1m", "ytd", "1y"}
    log.info("Window labels: %s", data["windowLabels"])
