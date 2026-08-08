# inky-arena

Always-on Are.na block viewer for the `800x480` Pimoroni Inky Impression 7.3" display.

This project is separate from the weather display app. It fetches visual blocks from one or more Are.na channels, rotates through them every two minutes, and publishes each selected block to the e-ink panel.

## Features

- Reads from multiple Are.na channels
- Rotates through visual blocks in randomized order without replaying previously displayed images
- Walks older Are.na pages in bounded batches after the current batch is exhausted
- Keeps the current e-ink image in place when no genuinely new image is available
- Shows a subdued application version beneath the clock for on-device verification
- Supports image blocks plus visual previews from link, embed, and attachment blocks
- Optional personal access token for private or closed channels
- Local state file so rotations survive restarts
- Preview fallback when `inky` is unavailable on a development machine

## Development Quick Start

On macOS or another development machine without an Inky display:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml
.venv/bin/python main.py
```

Edit `config.toml` with one or more Are.na channel slugs before running the app. The core requirements intentionally omit Raspberry Pi GPIO/SPI packages; when the Inky library is unavailable, the app writes `cache/preview.png` as its development output.

## Raspberry Pi Dependencies

The Pi requires the hardware-specific dependency set:

```bash
.venv/bin/pip install -r requirements-pi.txt
```

For a deployed service, use the Python interpreter from the verified live systemd `ExecStart`. It may not be the repository-local `.venv`; do not change that path without checking the Pi first.

## Configuration

Required values:
- `channel_slugs`

Common options:
- `arena_token`
- `refresh_minutes`
- `sync_minutes`
- `request_timeout_seconds`
- `refresh_timeout_seconds`
- `max_blocks_per_channel` (the per-sync page-window size, not a lifetime channel limit)
- `display_orientation` (`landscape` or `portrait`)
- `metadata_mode` (`time_only` or `footer`)
- `state_path`
- `preview_output`
- `download_cache_dir`

Landscape art mode uses `display_orientation = "landscape"` with `metadata_mode = "time_only"`. It renders an 800x480 image-first frame with a small clock overlay instead of the older title/channel footer.

Set `refresh_timeout_seconds` lower than the service `WatchdogSec` so the app can log a clear timeout before systemd restarts it. Set it to `0` only when you intentionally want to disable the app-level refresh deadline.

Environment overrides:
- `ARENA_CONFIG`
- `ARENA_CHANNEL_SLUGS`
- `ARENA_TOKEN`
- `ARENA_REFRESH_MINUTES`
- `ARENA_SYNC_MINUTES`
- `ARENA_REQUEST_TIMEOUT_SECONDS`
- `ARENA_REFRESH_TIMEOUT_SECONDS`
- `ARENA_MAX_BLOCKS_PER_CHANNEL`
- `ARENA_DISPLAY_ORIENTATION`
- `ARENA_METADATA_MODE`
- `ARENA_STATE_PATH`
- `ARENA_PREVIEW_OUTPUT`
- `ARENA_DOWNLOAD_CACHE_DIR`

## Are.na API

The client uses `https://api.are.na` and prefers the current v3 channel contents path. If that path is unavailable for a given channel, it falls back to the legacy v2 contents endpoint for compatibility.

Are.na documents rate limits and recommends pagination instead of enumerating entire channels. This app fetches a bounded page window per channel, finishes that batch, and then continues from the next page window. The persisted state remembers displayed block IDs and image-content digests across restarts. Deleting the state file intentionally resets that history and can allow old images to appear again.

## Development

Run all tests with:

```bash
.venv/bin/python -m unittest discover tests/
```

## Deployment

Deployment is manual. Follow the guarded [Raspberry Pi deployment and rollback runbook](docs/deployment.md), including clean-worktree, interpreter, service, log, and physical-display checks.

An example unit is included at `deploy/systemd/inky-arena.service`. Its `/home/jcampbell/inky-app/.venv` interpreter path must be verified against the live unit before use; do not assume it matches the Pi.
