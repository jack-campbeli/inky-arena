# Landscape Art Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render `inky-arena` as a landscape-first 800x480 art display with no normal footer, a tiny clock overlay, and orientation-aware hardware publishing.

**Architecture:** Add explicit orientation and metadata-mode config to `AppConfig`, then make rendering and hardware publishing consume those settings. Keep runtime rotation/cache/sync behavior unchanged; this is a presentation-only change plus tests and docs.

**Tech Stack:** Python 3, `unittest`, Pillow (`PIL.Image`, `ImageOps`, `ImageFilter`, `ImageDraw`), systemd user service for deployment.

---

## File Structure

- Modify `inky_arena/config.py`: add `display_orientation` and `metadata_mode`, validate them, expose computed `display_width`, `display_height`, and `uses_footer` properties.
- Modify `inky_arena/render.py`: route candidate rendering through landscape art mode when `metadata_mode == "time_only"`; keep status screens landscape-aware through the config dimensions.
- Modify `inky_arena/runtime.py`: stop blindly rotating images before publishing; rotate only for portrait orientation.
- Modify `tests/test_config.py`: cover new config parsing and environment overrides.
- Modify `tests/test_runtime.py`: cover landscape dimensions, no-footer art render behavior, portrait matte behavior, degraded overlay, status dimensions, and orientation-aware publishing.
- Modify `config.example.toml`: document landscape art mode settings.
- Modify `README.md`: document `display_orientation` and `metadata_mode`.
- Modify `config.toml`: set this installation to landscape art mode.

## Task 1: Config Surface

**Files:**
- Modify: `inky_arena/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config tests**

Append these tests to `ConfigTests` in `tests/test_config.py`:

```python
    def test_load_reads_landscape_art_mode_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha"]\ndisplay_orientation = "landscape"\nmetadata_mode = "time_only"\n',
                encoding="utf-8",
            )

            config = AppConfig.load(path)

            self.assertEqual(config.display_orientation, "landscape")
            self.assertEqual(config.metadata_mode, "time_only")
            self.assertEqual(config.display_width, 800)
            self.assertEqual(config.display_height, 480)
            self.assertFalse(config.uses_footer)

    def test_load_reads_portrait_footer_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha"]\ndisplay_orientation = "portrait"\nmetadata_mode = "footer"\n',
                encoding="utf-8",
            )

            config = AppConfig.load(path)

            self.assertEqual(config.display_orientation, "portrait")
            self.assertEqual(config.metadata_mode, "footer")
            self.assertEqual(config.display_width, 480)
            self.assertEqual(config.display_height, 800)
            self.assertTrue(config.uses_footer)

    def test_display_settings_can_be_overridden_by_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha"]\ndisplay_orientation = "portrait"\nmetadata_mode = "footer"\n',
                encoding="utf-8",
            )

            old_env = dict(os.environ)
            try:
                os.environ["ARENA_DISPLAY_ORIENTATION"] = "landscape"
                os.environ["ARENA_METADATA_MODE"] = "time_only"
                config = AppConfig.load(path)
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            self.assertEqual(config.display_orientation, "landscape")
            self.assertEqual(config.metadata_mode, "time_only")
            self.assertEqual(config.display_width, 800)
            self.assertEqual(config.display_height, 480)
            self.assertFalse(config.uses_footer)

    def test_invalid_display_settings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha"]\ndisplay_orientation = "diagonal"\nmetadata_mode = "clockish"\n',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as ctx:
                AppConfig.load(path)

            self.assertIn("display_orientation", str(ctx.exception))
```

- [ ] **Step 2: Run config tests to verify failure**

Run:

```bash
/home/jcampbell/inky-app/.venv/bin/python -m unittest tests.test_config
```

Expected: FAIL because `AppConfig` does not yet expose `display_orientation`, `metadata_mode`, or `uses_footer`.

- [ ] **Step 3: Add config fields, validation, env overrides, and computed dimensions**

In `inky_arena/config.py`, add constants near the existing defaults:

```python
VALID_DISPLAY_ORIENTATIONS = {"landscape", "portrait"}
VALID_METADATA_MODES = {"time_only", "footer"}
```

Add this helper below `_normalize_channel_slug`:

```python
def _parse_choice(value: object, *, default: str, valid: set[str], name: str) -> str:
    choice = str(value if value not in (None, "") else default).strip().lower()
    if choice not in valid:
        allowed = ", ".join(sorted(valid))
        raise ValueError(f"Invalid config value for {name}: {choice!r}. Expected one of: {allowed}.")
    return choice
```

Change `AppConfig` display fields from fixed `display_width`, `display_height`, and `caption_height` fields to explicit layout settings plus properties:

```python
    display_orientation: str = "landscape"
    metadata_mode: str = "time_only"
    panel_width: int = 800
    panel_height: int = 480
    caption_height: int = 64
```

Add these properties to `AppConfig`:

```python
    @property
    def display_width(self) -> int:
        if self.display_orientation == "portrait":
            return self.panel_height
        return self.panel_width

    @property
    def display_height(self) -> int:
        if self.display_orientation == "portrait":
            return self.panel_width
        return self.panel_height

    @property
    def uses_footer(self) -> bool:
        return self.metadata_mode == "footer"
```

Add environment keys to `env_map`:

```python
            "display_orientation": os.getenv("ARENA_DISPLAY_ORIENTATION"),
            "metadata_mode": os.getenv("ARENA_METADATA_MODE"),
```

Add parsed values before `return cls(...)`:

```python
        display_orientation = _parse_choice(
            merged.get("display_orientation"),
            default="landscape",
            valid=VALID_DISPLAY_ORIENTATIONS,
            name="display_orientation",
        )
        metadata_mode = _parse_choice(
            merged.get("metadata_mode"),
            default="time_only",
            valid=VALID_METADATA_MODES,
            name="metadata_mode",
        )
```

Pass them into `cls(...)`:

```python
            display_orientation=display_orientation,
            metadata_mode=metadata_mode,
```

- [ ] **Step 4: Run config tests to verify pass**

Run:

```bash
/home/jcampbell/inky-app/.venv/bin/python -m unittest tests.test_config
```

Expected: PASS.

- [ ] **Step 5: Commit config surface**

Stage only the files from this task, then commit:

```bash
git add inky_arena/config.py tests/test_config.py
git commit -m "Add display layout config"
```

## Task 2: Landscape Art Renderer

**Files:**
- Modify: `inky_arena/render.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write failing render tests**

In `tests/test_runtime.py`, update imports:

```python
from inky_arena.render import render_candidate, render_status
```

Add this helper near `_make_png_bytes`:

```python
def _make_sized_png_bytes(size: tuple[int, int], color: str = "red") -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sample.png"
        Image.new("RGB", size, color).save(path)
        return path.read_bytes()
```

Append these tests to `RuntimeTests`:

```python
    def test_render_candidate_uses_landscape_art_dimensions(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="landscape-frame",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Landscape",
            image_url="https://example.com/landscape.png",
        )

        image = render_candidate(config, candidate, _make_sized_png_bytes((1600, 960), "red"))

        self.assertEqual(image.size, (800, 480))

    def test_landscape_art_mode_has_no_footer_band(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="no-footer",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="No Footer",
            image_url="https://example.com/no-footer.png",
        )

        image = render_candidate(config, candidate, _make_sized_png_bytes((1600, 960), "red"))
        bottom_strip = image.crop((0, 430, 760, 480))
        colors = bottom_strip.getcolors(maxcolors=100000) or []
        red_pixels = sum(count for count, color in colors if color == (255, 0, 0))
        total_pixels = bottom_strip.width * bottom_strip.height

        self.assertGreater(red_pixels / total_pixels, 0.85)

    def test_portrait_image_gets_art_matte_instead_of_tiny_contain(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="portrait-matte",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Portrait",
            image_url="https://example.com/portrait.png",
        )

        image = render_candidate(config, candidate, _make_sized_png_bytes((400, 900), "red"))
        left_edge = image.crop((0, 0, 80, 480))
        center = image.crop((340, 0, 460, 480))
        left_colors = {color for _, color in (left_edge.getcolors(maxcolors=100000) or [])}
        center_colors = {color for _, color in (center.getcolors(maxcolors=100000) or [])}

        self.assertNotEqual(left_colors, {(243, 239, 228)})
        self.assertIn((255, 0, 0), center_colors)

    def test_render_status_uses_landscape_dimensions(self) -> None:
        config = AppConfig(channel_slugs=["demo"])

        image = render_status(config, "Status", "Details")

        self.assertEqual(image.size, (800, 480))

    def test_render_candidate_shows_time_overlay_in_art_mode(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="time-overlay",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Time",
            image_url="https://example.com/time.png",
        )

        image = render_candidate(config, candidate, _make_sized_png_bytes((1600, 960), "red"))
        corner = image.crop((680, 420, 800, 480))
        colors = {color for _, color in (corner.getcolors(maxcolors=100000) or [])}

        self.assertIn((21, 21, 21), colors)
```

Update the existing star-field tests so they explicitly use footer mode, because the normal landscape art mode no longer draws stars:

```python
        config = AppConfig(channel_slugs=["demo"], display_orientation="portrait", metadata_mode="footer")
```

Use that config line in `test_star_field_is_stable_for_same_image`, `test_star_field_varies_for_different_images`, and `test_star_field_uses_black_only_in_margins`.

- [ ] **Step 2: Run render tests to verify failure**

Run:

```bash
/home/jcampbell/inky-app/.venv/bin/python -m unittest tests.test_runtime.RuntimeTests.test_render_candidate_uses_landscape_art_dimensions tests.test_runtime.RuntimeTests.test_landscape_art_mode_has_no_footer_band tests.test_runtime.RuntimeTests.test_portrait_image_gets_art_matte_instead_of_tiny_contain tests.test_runtime.RuntimeTests.test_render_status_uses_landscape_dimensions tests.test_runtime.RuntimeTests.test_render_candidate_shows_time_overlay_in_art_mode
```

Expected: FAIL because `render_candidate` still uses portrait contain plus footer, and `render_status` was designed around portrait geometry.

- [ ] **Step 3: Add landscape art rendering helpers**

In `inky_arena/render.py`, add `ImageFilter` to imports:

```python
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat
```

Replace the body of `render_candidate` after the blank-image check with this routing:

```python
    if not config.uses_footer:
        return _render_art_candidate(config, candidate, source_image, degraded=degraded)

    return _render_footer_candidate(config, candidate, source_image, degraded=degraded)
```

Create `_render_footer_candidate` by moving the current footer-rendering body into a helper with this signature:

```python
def _render_footer_candidate(
    config: AppConfig,
    candidate: DisplayCandidate,
    source_image: Image.Image,
    degraded: bool = False,
) -> Image.Image:
```

In that helper, keep the existing contain/footer/star behavior unchanged except remove the `Image.open(...)` and blank check, because `render_candidate` now does those before routing.

Add these new helpers below `_render_footer_candidate`:

```python
def _render_art_candidate(
    config: AppConfig,
    candidate: DisplayCandidate,
    source_image: Image.Image,
    degraded: bool = False,
) -> Image.Image:
    size = (config.display_width, config.display_height)
    canvas = _compose_art_image(source_image, size)
    draw = ImageDraw.Draw(canvas)
    fonts = FontSet(config.primary_font_path, config.bold_font_path, config.mono_font_path)
    _draw_time_overlay(draw, canvas.size, fonts)
    if degraded:
        _draw_degraded_dot(draw, canvas.size)
    return canvas


def _compose_art_image(source_image: Image.Image, size: tuple[int, int]) -> Image.Image:
    source_ratio = source_image.width / max(1, source_image.height)
    target_ratio = size[0] / max(1, size[1])
    if source_ratio >= target_ratio * 0.8:
        return ImageOps.fit(source_image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    background = ImageOps.fit(source_image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    background = background.filter(ImageFilter.GaussianBlur(radius=18))
    background = ImageOps.autocontrast(background)

    foreground = ImageOps.contain(source_image, size, method=Image.Resampling.LANCZOS)
    paste_x = (size[0] - foreground.width) // 2
    paste_y = (size[1] - foreground.height) // 2
    background.paste(foreground, (paste_x, paste_y))
    return background


def _draw_time_overlay(draw: ImageDraw.ImageDraw, size: tuple[int, int], fonts: FontSet) -> None:
    time_font = fonts.bold(15)
    time_text = datetime.now().astimezone().strftime("%-I:%M %p")
    bbox = draw.textbbox((0, 0), time_text, font=time_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x = 8
    pad_y = 5
    right = size[0] - 10
    bottom = size[1] - 10
    box = (right - text_w - pad_x * 2, bottom - text_h - pad_y * 2, right, bottom)
    draw.rounded_rectangle(box, radius=5, fill=BACKGROUND)
    draw.rectangle(box, outline=TEXT, width=1)
    draw.text((right - pad_x, bottom - pad_y - text_h), time_text, fill=TEXT, font=time_font, anchor="ra")


def _draw_degraded_dot(draw: ImageDraw.ImageDraw, size: tuple[int, int]) -> None:
    cx = size[0] - 10
    cy = 10
    draw.ellipse([(cx - 5, cy - 5), (cx + 5, cy + 5)], fill=ACCENT)
```

Use `_draw_degraded_dot(draw, canvas.size)` from `_render_footer_candidate` too, replacing the inline degraded ellipse. This keeps the dot location consistent with the active canvas size.

- [ ] **Step 4: Make `render_status` landscape-aware**

In `render_status`, replace the fixed portrait card coordinates with values derived from width and height:

```python
    margin = max(16, min(width, height) // 28)
    outer = (margin, margin, width - margin, height - margin)
    card = (margin * 2, margin * 2, width - margin * 2, height - margin * 2)
    header_h = max(46, height // 8)
    detail_box = (card[0] + 24, card[1] + 150, card[2] - 24, max(card[1] + 220, card[3] - 88))
    footer_y = card[3] - 70
```

Adjust the subtitle and detail line limits so they use available height instead of portrait assumptions:

```python
    subtitle_lines = _wrap_text(draw, subtitle, subtitle_font, max_width=card[2] - card[0] - 68)
    subtitle_y = card[1] + 112
    for line in subtitle_lines[:2]:
        draw.text((card[0] + 34, subtitle_y), line, fill=MUTED, font=subtitle_font)
        subtitle_y += 22

    detail_y = detail_box[1] + 64
    line_height = 23
    max_lines = max(3, (detail_box[3] - detail_y - 14) // line_height)
```

Keep the exact visual polish flexible during implementation, but verify the status image is 800x480 and text does not obviously overlap.

- [ ] **Step 5: Run render tests to verify pass**

Run:

```bash
/home/jcampbell/inky-app/.venv/bin/python -m unittest tests.test_runtime
```

Expected: PASS for runtime tests.

- [ ] **Step 6: Commit landscape renderer**

Stage only the files from this task, then commit:

```bash
git add inky_arena/render.py tests/test_runtime.py
git commit -m "Render landscape art mode"
```

## Task 3: Orientation-Aware Hardware Publishing

**Files:**
- Modify: `inky_arena/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write failing publish tests**

Append these tests to `RuntimeTests` in `tests/test_runtime.py`:

```python
    def test_publish_image_does_not_rotate_landscape_output_for_hardware(self) -> None:
        config = AppConfig(channel_slugs=["demo"], display_orientation="landscape", metadata_mode="time_only")
        image = Image.new("RGB", (800, 480), "red")

        class FakeDisplay:
            WIDTH = 800
            HEIGHT = 480

            def __init__(self) -> None:
                self.image: Image.Image | None = None
                self.show_called = False

            def set_image(self, image: Image.Image) -> None:
                self.image = image

            def show(self) -> None:
                self.show_called = True

        fake_display = FakeDisplay()

        with patch("inky.auto.auto", return_value=fake_display):
            publish_image(image, config)

        self.assertEqual(fake_display.image.size, (800, 480))
        self.assertTrue(fake_display.show_called)

    def test_publish_image_rotates_portrait_output_for_hardware(self) -> None:
        config = AppConfig(channel_slugs=["demo"], display_orientation="portrait", metadata_mode="footer")
        image = Image.new("RGB", (480, 800), "red")

        class FakeDisplay:
            WIDTH = 800
            HEIGHT = 480

            def __init__(self) -> None:
                self.image: Image.Image | None = None

            def set_image(self, image: Image.Image) -> None:
                self.image = image

            def show(self) -> None:
                pass

        fake_display = FakeDisplay()

        with patch("inky.auto.auto", return_value=fake_display):
            publish_image(image, config)

        self.assertEqual(fake_display.image.size, (800, 480))
```

Add `publish_image` to the runtime imports at the top of the test file:

```python
from inky_arena.runtime import RefreshTimeout, _prepare_queue, _should_use_cached_candidates, notify_systemd, publish_image, refresh_deadline, refresh_once, run_forever, seconds_until_next_refresh, sleep_with_watchdog
```

- [ ] **Step 2: Run publish tests to verify failure**

Run:

```bash
/home/jcampbell/inky-app/.venv/bin/python -m unittest tests.test_runtime.RuntimeTests.test_publish_image_does_not_rotate_landscape_output_for_hardware tests.test_runtime.RuntimeTests.test_publish_image_rotates_portrait_output_for_hardware
```

Expected: FAIL because `publish_image` always rotates before publishing.

- [ ] **Step 3: Make `publish_image` orientation-aware**

In `inky_arena/runtime.py`, replace:

```python
        rotated = image.rotate(90, expand=True)
        resized = rotated.resize((display.WIDTH, display.HEIGHT))
        display.set_image(resized)
```

with:

```python
        output = image
        if config.display_orientation == "portrait":
            output = image.rotate(90, expand=True)
        resized = output.resize((display.WIDTH, display.HEIGHT))
        display.set_image(resized)
```

- [ ] **Step 4: Run publish tests to verify pass**

Run:

```bash
/home/jcampbell/inky-app/.venv/bin/python -m unittest tests.test_runtime.RuntimeTests.test_publish_image_does_not_rotate_landscape_output_for_hardware tests.test_runtime.RuntimeTests.test_publish_image_rotates_portrait_output_for_hardware
```

Expected: PASS.

- [ ] **Step 5: Commit publishing change**

Stage only the files from this task, then commit:

```bash
git add inky_arena/runtime.py tests/test_runtime.py
git commit -m "Publish landscape output without rotation"
```

## Task 4: Docs And Local Configuration

**Files:**
- Modify: `README.md`
- Modify: `config.example.toml`
- Modify: `config.toml`

- [ ] **Step 1: Update example config**

In `config.example.toml`, add the layout settings after `max_blocks_per_channel`:

```toml
display_orientation = "landscape"
metadata_mode = "time_only"
```

- [ ] **Step 2: Update local config**

In `config.toml`, add the same settings after `max_blocks_per_channel`:

```toml
display_orientation = "landscape"
metadata_mode = "time_only"
```

- [ ] **Step 3: Update README configuration docs**

In `README.md`, add `display_orientation` and `metadata_mode` to the common options list:

```markdown
- `display_orientation` (`landscape` or `portrait`)
- `metadata_mode` (`time_only` or `footer`)
```

Add this paragraph under the common options:

```markdown
Landscape art mode uses `display_orientation = "landscape"` with `metadata_mode = "time_only"`. It renders an 800x480 image-first frame with a small clock overlay instead of the older title/channel footer.
```

Add environment overrides to the list:

```markdown
- `ARENA_DISPLAY_ORIENTATION`
- `ARENA_METADATA_MODE`
```

- [ ] **Step 4: Run full tests**

Run:

```bash
/home/jcampbell/inky-app/.venv/bin/python -m unittest discover tests/
```

Expected: PASS.

- [ ] **Step 5: Commit docs and config**

Do not commit `config.toml` if it is ignored or intentionally local-only. Stage tracked docs/config template only:

```bash
git add README.md config.example.toml
git commit -m "Document landscape art mode"
```

## Task 5: Preview And Service Verification

**Files:**
- Runtime artifacts only: `cache/preview.png`
- Service: `inky-arena.service`

- [ ] **Step 1: Generate or refresh a preview**

Run the app locally through the known interpreter:

```bash
/home/jcampbell/inky-app/.venv/bin/python main.py
```

If the app runs continuously, stop it after one render or use the service restart in the next step as the hardware verification path. Confirm `cache/preview.png` is 800x480 if hardware is unavailable.

- [ ] **Step 2: Restart the user service to push the new layout**

Run:

```bash
systemctl --user restart inky-arena.service
```

Expected: command exits successfully.

- [ ] **Step 3: Check service status**

Run:

```bash
systemctl --user status inky-arena.service
```

Expected: service is active/running or has recently completed a refresh without a traceback.

- [ ] **Step 4: Check recent logs**

Run:

```bash
journalctl --user -u inky-arena.service -n 80 --no-pager
```

Expected: logs show a refresh cycle, rendering, publishing, and no repeated exceptions.

- [ ] **Step 5: Final working tree review**

Run:

```bash
git status --short
```

Expected: only intentional local runtime files or pre-existing unrelated work remain unstaged. Do not revert unrelated user changes.

## Self-Review

- Spec coverage: the plan covers landscape canvas, no footer, tiny clock/degraded overlay, portrait matte fallback, landscape-aware status screens, orientation-aware hardware publishing, config/docs, and tests. Runtime rotation/cache/sync behavior remains out of scope.
- Placeholder scan: checked for forbidden placeholder tokens; none are used as placeholders.
- Type consistency: the plan consistently uses `display_orientation`, `metadata_mode`, `display_width`, `display_height`, and `uses_footer`.
