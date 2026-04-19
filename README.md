# inky-arena

Always-on Are.na block viewer for the `800x480` Pimoroni Inky Impression 7.3" display.

This project is separate from the weather display app. It fetches visual blocks from one or more Are.na channels, rotates through them every two minutes, and publishes each selected block to the e-ink panel.

## Features

- Reads from multiple Are.na channels
- Rotates through visual blocks in randomized order without repeats
- Supports image blocks plus visual previews from link, embed, and attachment blocks
- Optional personal access token for private or closed channels
- Local state file so rotations survive restarts
- Preview fallback when `inky` is unavailable on a development machine

## Quick Start

1. Create a virtual environment:
   `python3 -m venv .venv`
2. Install dependencies:
   `.venv/bin/pip install -r requirements.txt`
3. Create your local config:
   `cp config.example.toml config.toml`
4. Edit `config.toml` with one or more Are.na channel slugs.
5. Run the app:
   `.venv/bin/python main.py`

When the Inky hardware library is unavailable, the app writes a local render preview to `cache/preview.png`.

## Configuration

Required values:
- `channel_slugs`

Common options:
- `arena_token`
- `refresh_minutes`
- `request_timeout_seconds`
- `max_blocks_per_channel`
- `state_path`
- `preview_output`

Environment overrides:
- `ARENA_CONFIG`
- `ARENA_CHANNEL_SLUGS`
- `ARENA_TOKEN`
- `ARENA_REFRESH_MINUTES`
- `ARENA_REQUEST_TIMEOUT_SECONDS`
- `ARENA_MAX_BLOCKS_PER_CHANNEL`
- `ARENA_STATE_PATH`
- `ARENA_PREVIEW_OUTPUT`

## Are.na API

The client uses `https://api.are.na` and prefers the current v3 channel contents path. If that path is unavailable for a given channel, it falls back to the legacy v2 contents endpoint for compatibility.

Are.na documents rate limits and recommends pagination instead of enumerating entire channels. This app caps the number of fetched blocks per channel to stay lightweight on the device.

## Development

Run all tests with:

```bash
.venv/bin/python -m unittest discover tests/
```

## Deployment

An example systemd unit is included at [deploy/systemd/inky-arena.service](/home/jcampbell/inky-arena/deploy/systemd/inky-arena.service).

