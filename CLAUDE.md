# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml

# Run
.venv/bin/python main.py

# Test
.venv/bin/python -m unittest discover tests/
.venv/bin/python -m unittest tests.test_runtime.TestRefreshOnce.test_refresh_once_skips_blank_images  # single test

# Deploy (on Pi)
systemctl --user restart inky-arena.service
```

## Architecture

The app fetches visual blocks from Are.na channels and rotates through them on a Pimoroni Inky Impression 7.3" e-ink display (800×480). When `inky` is unavailable (dev machine), it falls back to writing `cache/preview.png`.

**Data flow:**
1. `runtime.py` (`run_forever`) loops on a clock-aligned interval (`refresh_minutes`, default 2 min)
2. `ArenaClient` (`arena_client.py`) fetches and normalizes Are.na blocks into `DisplayCandidate` objects — syncing only every `sync_minutes` (default 15 min) using `AppState.next_sync_not_before_iso` and rate-limit headers
3. `runtime.py` maintains a shuffle queue (`AppState.queue_ids`) so blocks rotate without repeats until all are seen; `shown_ids` is bounded at `max(200, len(candidates) * 4)`
4. `render.py` composites the image onto an off-screen PIL canvas. Default art mode is landscape 800×480 with a small clock overlay and no title/channel footer; portrait/footer mode remains available through config
5. `runtime.py` publishes the render to the Inky display. Landscape output is sent without rotation; portrait output is rotated for the 800×480 panel
6. `state.py` persists `AppState` as JSON to `cache/state.json` after every change so rotations survive restarts

**Key resilience behaviors:**
- If Are.na sync fails, the client falls back to `state.cached_candidates` and logs a warning; if no cache exists, raises and shows a status screen
- `render.py:_looks_blank()` rejects visually blank images (all-white or all-black); the runtime skips them and records them as shown to avoid retries
- Blank-screen fallback: `render_status()` renders a styled status card instead of a block when the queue is empty or all syncs fail

## Module Responsibilities

| File | Role |
|---|---|
| `arena_client.py` | HTTP, pagination, v3/v2 API fallback, image URL normalization, disk image cache |
| `render.py` | PIL canvas composition, landscape art mode, footer fallback mode, blank-image detection |
| `runtime.py` | Main loop, queue management, orientation-aware display publishing, dev preview fallback |
| `config.py` | TOML + env loading; env vars (`ARENA_*`) override config file values |
| `models.py` | `DisplayCandidate` and `AppState` dataclasses |
| `state.py` | JSON serialization/deserialization of `AppState` |

## Config

`config.toml` is gitignored; `config.example.toml` is the checked-in template. Only `channel_slugs` is required. Sensitive values (token) should use `ARENA_TOKEN` env var rather than committing to the file.

Display layout is controlled by `display_orientation` (`landscape` or `portrait`) and `metadata_mode` (`time_only` or `footer`). The current art-object mode uses `display_orientation = "landscape"` with `metadata_mode = "time_only"`.

## Style

No linter/formatter is configured. Use 4-space indentation, `snake_case` functions, `PascalCase` dataclasses. Keep rendering constants near the top of `render.py`. Commit messages are short imperatives: `Tighten footer layout`.

## Testing

Uses `unittest`. Tests are behavior-focused; name them `test_<feature>.py` with methods like `test_refresh_once_skips_blank_images`. Cover config parsing, Are.na response handling, rotation logic, and rendering changes.

## PR Guidelines

Include: brief summary of visual/runtime change, config or service changes, screenshot/photo when display output changes, test results.
