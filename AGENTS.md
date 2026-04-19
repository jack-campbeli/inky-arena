# Repository Guidelines

## Project Structure & Module Organization

`main.py` is the entrypoint for local runs and the systemd service. Core application code lives in `inky_arena/`:

- `arena_client.py` fetches and normalizes Are.na channel content
- `render.py` builds the portrait e-ink frame and footer
- `runtime.py` handles refresh scheduling and publishing to the Inky display
- `config.py`, `models.py`, and `state.py` manage settings, data models, and persisted rotation state

Tests live in `tests/` and follow the module split (`test_config.py`, `test_runtime.py`, etc.). Runtime artifacts such as previews and state files go in `cache/`. Keep local secrets and machine-specific settings in `config.toml`; use `config.example.toml` as the checked-in template.

## Build, Test, and Development Commands

- `python3 -m venv .venv` creates a local virtualenv
- `.venv/bin/pip install -r requirements.txt` installs `inky`, `Pillow`, and `requests`
- `cp config.example.toml config.toml` creates a local config file
- `.venv/bin/python main.py` runs the app locally
- `.venv/bin/python -m unittest discover tests/` runs the full test suite
- `systemctl --user restart inky-arena.service` restarts the persistent display service on the Pi

## Coding Style & Naming Conventions

Use 4-space indentation and standard Python naming: `snake_case` for functions/variables, `PascalCase` for dataclasses, and short module names matching responsibilities. Prefer small, single-purpose helpers over deeply nested logic. Keep rendering constants near the top of `render.py` and keep config defaults in `config.py`.

No formatter or linter is configured in this repo today, so match the surrounding style and keep changes tidy and minimal.

## Testing Guidelines

This project uses `unittest`. Add or update tests whenever you touch config parsing, Are.na response handling, rotation logic, or rendering behavior. Name new tests `test_<feature>.py` and keep test methods behavior-focused, for example `test_refresh_once_skips_blank_images`.

## Commit & Pull Request Guidelines

Current history uses short, imperative commit messages like `Initial Are.na e-ink display app`. Continue with that style, for example `Tighten footer layout` or `Add portrait display rotation`.

Pull requests should include:

- a brief summary of the visual or runtime change
- notes about config or service changes
- a screenshot or photo when the display output changes
- test results from `.venv/bin/python -m unittest discover tests/`

## Security & Configuration Tips

Do not commit `config.toml`, tokens, or machine-specific state files. Prefer environment overrides such as `ARENA_TOKEN` for sensitive values, and keep systemd paths aligned with the actual checkout and Python environment on the Pi.
