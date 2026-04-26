# Degraded State Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the overlapping text badge in the footer with a small filled orange dot in the bottom-right corner of the canvas that appears only when the Are.na sync has an error.

**Architecture:** `render_candidate` in `render.py` gains a `degraded: bool = False` parameter replacing `footer_note`. When `degraded=True`, a 10px filled circle in accent orange is drawn at the very bottom-right corner of the canvas. In `runtime.py`, `_try_display_queue` derives `degraded` from `state.last_error is not None` instead of `image_result.from_cache`.

**Tech Stack:** Python 3, Pillow (PIL), unittest

---

### Task 1: Write failing render test

**Files:**
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Add the new degraded-dot render test**

In `tests/test_runtime.py`, replace the method `test_render_candidate_changes_footer_when_fallback_note_is_present` (near line 506) with:

```python
def test_render_candidate_shows_degraded_dot_when_degraded(self) -> None:
    config = AppConfig(channel_slugs=["demo"])
    candidate = DisplayCandidate(
        id="degraded-dot",
        channel_slug="demo",
        channel_title="Demo",
        block_type="Image",
        title="Degraded",
        image_url="https://example.com/degraded.png",
    )

    image_healthy = render_candidate(config, candidate, _make_png_bytes("red"))
    image_degraded = render_candidate(config, candidate, _make_png_bytes("red"), degraded=True)

    diff = ImageChops.difference(image_healthy, image_degraded)
    self.assertIsNotNone(diff.getbbox())
```

- [ ] **Step 2: Run the new test to confirm it fails**

```bash
.venv/bin/python -m unittest tests.test_runtime.RuntimeTests.test_render_candidate_shows_degraded_dot_when_degraded -v
```

Expected: `TypeError: render_candidate() got an unexpected keyword argument 'degraded'`

---

### Task 2: Update render.py to make the render test pass

**Files:**
- Modify: `inky_arena/render.py`

- [ ] **Step 1: Replace the render_candidate signature and body**

Replace the entire `render_candidate` function (lines 46–120) with:

```python
def render_candidate(
    config: AppConfig,
    candidate: DisplayCandidate,
    image_bytes: bytes,
    degraded: bool = False,
) -> Image.Image:
    canvas = Image.new("RGB", (config.display_width, config.display_height), BACKGROUND)
    source_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if _looks_blank(source_image):
        raise ValueError("source image is visually blank")
    image_height = config.display_height - config.caption_height
    fitted = ImageOps.contain(source_image, (config.display_width, image_height), method=Image.Resampling.LANCZOS)
    paste_x = (config.display_width - fitted.width) // 2
    paste_y = (image_height - fitted.height) // 2
    _draw_pixel_stars(canvas, fitted.size, (paste_x, paste_y), candidate.id, image_height)
    canvas.paste(fitted, (paste_x, paste_y))

    draw = ImageDraw.Draw(canvas)
    footer_y = config.display_height - config.caption_height
    draw.rectangle((0, footer_y, config.display_width, config.display_height), fill=BACKGROUND)
    draw.line((0, footer_y, config.display_width, footer_y), fill=TEXT, width=1)

    fonts = FontSet(config.primary_font_path, config.bold_font_path, config.mono_font_path)
    raw_title = (candidate.title or "").strip()
    title = shorten(raw_title, width=42, placeholder="...") if raw_title else ""
    meta = shorten(candidate.channel_title, width=34, placeholder="...")

    title_font = fonts.bold(18)
    meta_font = fonts.bold(16)
    time_font = fonts.bold(14)
    time_text = datetime.now().astimezone().strftime("%-I:%M %p")
    time_bbox = draw.textbbox((0, 0), time_text, font=time_font)
    top_padding = 9
    line_gap = 8
    title_y = footer_y + top_padding
    title_height = 0
    if title:
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_height = title_bbox[3] - title_bbox[1]
    meta_y = title_y + title_height + (line_gap if title else 0)

    time_x = config.display_width - 16
    time_y = footer_y + max(1, (config.caption_height - (time_bbox[3] - time_bbox[1])) // 2 - 1)
    draw.text((time_x, time_y), time_text, fill=TEXT, font=time_font, anchor="ra")
    time_left = time_x - (time_bbox[2] - time_bbox[0])

    if title:
        draw.text((16, title_y), title, fill=TEXT, font=title_font)
    meta_max_width = config.display_width - 32
    meta_text = _fit_text_to_width(draw, meta, meta_font, max_width=meta_max_width)
    if meta_text:
        draw.text((16, meta_y), meta_text, fill=MUTED, font=meta_font)

    if time_left < 250:
        draw.text((time_x, title_y), time_text, fill=TEXT, font=time_font, anchor="ra")

    if degraded:
        cx = config.display_width - 10
        cy = config.display_height - 10
        draw.ellipse([(cx - 5, cy - 5), (cx + 5, cy + 5)], fill=ACCENT)

    return canvas
```

- [ ] **Step 2: Run the render test to confirm it passes**

```bash
.venv/bin/python -m unittest tests.test_runtime.RuntimeTests.test_render_candidate_shows_degraded_dot_when_degraded -v
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add inky_arena/render.py tests/test_runtime.py
git commit -m "Replace footer badge with degraded dot in render_candidate"
```

---

### Task 3: Write failing runtime tests

**Files:**
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Replace the two footer_note runtime tests**

In `tests/test_runtime.py`, replace `test_refresh_once_adds_fallback_footer_note_for_cached_image_bytes` (around line 371) and `test_refresh_once_omits_fallback_footer_note_for_live_image_bytes` (around line 394) with:

```python
def test_refresh_once_passes_degraded_true_when_state_has_error(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            channel_slugs=["demo"],
            state_path=Path(tmpdir) / "state.json",
            preview_output=Path(tmpdir) / "preview.png",
        )
        candidate = DisplayCandidate(
            id="err-image",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Error",
            image_url="https://example.com/err.png",
        )
        state = AppState(last_error="sync failed")

        with patch("inky_arena.runtime.publish_image"), patch("inky_arena.runtime.render_candidate") as mock_render:
            mock_render.return_value = Image.new("RGB", (config.display_width, config.display_height), "red")
            refresh_once(config, FakeClient([candidate]), state, rng=random.Random(1))

        self.assertTrue(mock_render.call_args.kwargs["degraded"])

def test_refresh_once_passes_degraded_false_when_state_has_no_error(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            channel_slugs=["demo"],
            state_path=Path(tmpdir) / "state.json",
            preview_output=Path(tmpdir) / "preview.png",
        )
        candidate = DisplayCandidate(
            id="ok-image",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="OK",
            image_url="https://example.com/ok.png",
        )
        state = AppState()

        with patch("inky_arena.runtime.publish_image"), patch("inky_arena.runtime.render_candidate") as mock_render:
            mock_render.return_value = Image.new("RGB", (config.display_width, config.display_height), "red")
            refresh_once(config, FakeClient([candidate]), state, rng=random.Random(1))

        self.assertFalse(mock_render.call_args.kwargs["degraded"])
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```bash
.venv/bin/python -m unittest tests.test_runtime.RuntimeTests.test_refresh_once_passes_degraded_true_when_state_has_error tests.test_runtime.RuntimeTests.test_refresh_once_passes_degraded_false_when_state_has_no_error -v
```

Expected: `AssertionError` — `render_candidate` is called with `footer_note` not `degraded`

---

### Task 4: Update runtime.py and clean up test imports

**Files:**
- Modify: `inky_arena/runtime.py`
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Remove FALLBACK_IMAGE_NOTE from runtime.py**

Delete this line from `inky_arena/runtime.py` (line 22):

```python
FALLBACK_IMAGE_NOTE = "Stored image shown - live refresh unavailable."
```

- [ ] **Step 2: Update _try_display_queue in runtime.py**

In `_try_display_queue` (around line 426–429), replace:

```python
image_result = client.fetch_image_bytes(candidate.image_url)
footer_note = FALLBACK_IMAGE_NOTE if image_result.from_cache else None
logging.info("Rendering block %s", candidate.id)
image = render_candidate(config, candidate, image_result.payload, footer_note=footer_note)
```

with:

```python
image_result = client.fetch_image_bytes(candidate.image_url)
degraded = state.last_error is not None
logging.info("Rendering block %s", candidate.id)
image = render_candidate(config, candidate, image_result.payload, degraded=degraded)
```

- [ ] **Step 3: Remove FALLBACK_IMAGE_NOTE from the test import**

In `tests/test_runtime.py` line 18, replace:

```python
from inky_arena.runtime import FALLBACK_IMAGE_NOTE, RefreshTimeout, _prepare_queue, _should_use_cached_candidates, notify_systemd, refresh_deadline, refresh_once, run_forever, seconds_until_next_refresh, sleep_with_watchdog
```

with:

```python
from inky_arena.runtime import RefreshTimeout, _prepare_queue, _should_use_cached_candidates, notify_systemd, refresh_deadline, refresh_once, run_forever, seconds_until_next_refresh, sleep_with_watchdog
```

- [ ] **Step 4: Run the full test suite**

```bash
.venv/bin/python -m unittest discover tests/ -v
```

Expected: all tests pass with no errors or failures.

- [ ] **Step 5: Commit**

```bash
git add inky_arena/runtime.py tests/test_runtime.py
git commit -m "Wire degraded state indicator to state.last_error"
```
