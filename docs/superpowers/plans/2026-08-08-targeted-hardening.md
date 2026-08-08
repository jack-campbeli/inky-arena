# Targeted Hardening Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the five confirmed security, hardware-publication, rate-limit, state-persistence, and development-dependency findings, then document a guarded Pi deployment and rollback procedure.

**Architecture:** Preserve the existing module boundaries and tighten the contracts at their seams. `ArenaClient` separates API authentication from asset traffic; `runtime.py` distinguishes intentional previews from hardware failures and enforces hard sync backoff; `state.py` owns atomic replacement and corrupt-state recovery; dependency and deployment files make the Mac/Pi boundary explicit.

**Tech Stack:** Python 3.11+, `unittest`, Requests, Pillow, Pimoroni Inky on Raspberry Pi, systemd user services.

## Global Constraints

- Do not send `ARENA_TOKEN` or `arena_token` to candidate-controlled image hosts.
- Missing `inky` is an intentional development-preview path; installed-but-failing Inky hardware is a terminal process error.
- Do not advance `shown_ids`, `queue_ids`, or `last_displayed_id` after a hardware publication failure.
- `force_refresh=True` may bypass `sync_minutes` but must never bypass a valid future `next_sync_not_before_iso`.
- Preserve malformed state as a timestamped `.corrupt` sibling; propagate filesystem access failures.
- Keep Requests and Pillow in `requirements.txt`; keep Inky in `requirements-pi.txt`.
- Preserve candidate selection, channel walking, rendering, orientation, image caching, and ordinary rotation behavior.
- Do not modify `config.toml`, deploy to the Pi, or change the service interpreter path without separate live-device verification.
- Use `unittest` and behavior-focused test names consistent with the existing suite.

---

## File Structure

- Modify `inky_arena/arena_client.py`: separate authenticated API headers from unauthenticated image headers.
- Modify `inky_arena/runtime.py`: add the display-loading boundary, dedicated publication error, hard backoff check, and truthful state transitions.
- Modify `inky_arena/state.py`: validate persisted state, quarantine corrupt content, and atomically replace state files.
- Modify `tests/test_arena_client.py`: verify header isolation while retaining API authentication.
- Modify `tests/test_runtime.py`: verify preview, hardware failure, service exit, and hard-backoff behavior.
- Create `tests/test_state.py`: cover atomic persistence, corrupt-state recovery, and filesystem errors.
- Modify `requirements.txt`: retain only platform-neutral runtime dependencies.
- Create `requirements-pi.txt`: include core dependencies plus the Inky package.
- Modify `README.md`: document Mac and Pi setup separately and link the deployment guide.
- Create `docs/deployment.md`: provide guarded deployment, verification, and rollback commands.

---

### Task 1: Isolate API Credentials From Image Downloads

**Files:**
- Modify: `inky_arena/arena_client.py:90-97,130-136,222-226`
- Modify: `tests/test_arena_client.py:38-45,128-161`

**Interfaces:**
- Consumes: `AppConfig.arena_token: str | None` and the existing `requests.Session` interface.
- Produces: `ArenaClient._api_headers() -> dict[str, str]` and `ArenaClient._image_headers() -> dict[str, str]`; public client methods retain their existing signatures.

- [ ] **Step 1: Record request headers in the existing fake session**

Update `FakeSession` without changing the existing `calls` tuple shape used by other tests:

```python
class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.request_headers: list[dict[str, str]] = []

    def get(self, url: str, headers: dict | None = None, params: dict | None = None, timeout: float | None = None):  # type: ignore[override]
        self.calls.append((url, params or {}))
        self.request_headers.append(dict(headers or {}))
        return self.responses.pop(0)
```

- [ ] **Step 2: Write failing credential-boundary tests**

Add these tests to `ArenaClientTests`:

```python
def test_api_requests_include_configured_bearer_token(self) -> None:
    payload = {"data": [], "meta": {"has_more_pages": False}}
    session = FakeSession([FakeResponse(payload)])
    config = AppConfig(channel_slugs=["demo"], arena_token="private-token")
    client = ArenaClient(config, session=session)  # type: ignore[arg-type]

    client.fetch_channel_candidates("demo")

    self.assertEqual(session.request_headers[0]["Authorization"], "Bearer private-token")
    self.assertEqual(session.request_headers[0]["Accept"], "application/json")

def test_image_requests_never_include_api_bearer_token(self) -> None:
    session = FakeSession([FakeResponse(content=b"image-bytes")])
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            channel_slugs=["demo"],
            arena_token="private-token",
            download_cache_dir=Path(tmpdir),
        )
        client = ArenaClient(config, session=session)  # type: ignore[arg-type]

        client.fetch_image_bytes("https://attacker.example/image.png")

    self.assertNotIn("Authorization", session.request_headers[0])
    self.assertEqual(session.request_headers[0]["Accept"], "image/*")
```

- [ ] **Step 3: Run the new tests and verify the image test fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_arena_client.ArenaClientTests.test_api_requests_include_configured_bearer_token tests.test_arena_client.ArenaClientTests.test_image_requests_never_include_api_bearer_token
```

Expected: the API test passes and the image test fails because `fetch_image_bytes` currently uses the shared authenticated headers.

- [ ] **Step 4: Split API and image header builders**

Replace `_headers` with these methods and update both call sites:

```python
def _api_headers(self) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if self.config.arena_token:
        headers["Authorization"] = f"Bearer {self.config.arena_token}"
    return headers

def _image_headers(self) -> dict[str, str]:
    return {"Accept": "image/*"}
```

In `fetch_image_bytes`, use:

```python
response = self.session.get(
    image_url,
    headers=self._image_headers(),
    timeout=self.config.request_timeout_seconds,
)
```

In `_api_get`, use:

```python
headers = self._api_headers()
```

- [ ] **Step 5: Run the Arena client tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_arena_client
```

Expected: all Arena client tests pass, including image caching, API retries, pagination, and the two header-boundary tests.

- [ ] **Step 6: Commit the credential fix**

```bash
git add inky_arena/arena_client.py tests/test_arena_client.py
git commit -m "Isolate Are.na API credentials"
```

---

### Task 2: Make Display Publication Fail Truthfully

**Files:**
- Modify: `inky_arena/runtime.py:21-55,363-383,409-439`
- Modify: `tests/test_runtime.py:15-19,93-110,148-164,629-681`

**Interfaces:**
- Consumes: `publish_image(image: Image.Image, config: AppConfig) -> None` and `_save_preview`.
- Produces: `DisplayPublishError(RuntimeError)` and `_load_inky_display() -> object | None`. A normal return means the physical display or intentional development preview was updated; `DisplayPublishError` means no successful publish occurred.

- [ ] **Step 1: Update hardware tests to target an internal display-loading boundary**

Add `DisplayPublishError` and `_try_display_queue` to the imports from `inky_arena.runtime`. Change the two existing orientation tests from:

```python
with patch("inky.auto.auto", return_value=fake_display):
    publish_image(image, config)
```

to:

```python
with patch("inky_arena.runtime._load_inky_display", return_value=fake_display):
    publish_image(image, config)
```

- [ ] **Step 2: Write failing preview and hardware-failure tests**

Add these tests to `RuntimeTests`:

```python
def test_publish_image_writes_preview_when_inky_is_unavailable(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            channel_slugs=["demo"],
            preview_output=Path(tmpdir) / "preview.png",
        )
        image = Image.new("RGB", (800, 480), "red")

        with patch("inky_arena.runtime._load_inky_display", return_value=None):
            publish_image(image, config)

        self.assertTrue(config.preview_output.exists())

def test_publish_image_raises_when_display_discovery_fails(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            channel_slugs=["demo"],
            preview_output=Path(tmpdir) / "diagnostic.png",
        )
        image = Image.new("RGB", (800, 480), "red")

        with patch("inky_arena.runtime._load_inky_display", side_effect=OSError("display missing")):
            with self.assertRaises(DisplayPublishError):
                publish_image(image, config)

        self.assertTrue(config.preview_output.exists())

def test_publish_image_raises_when_display_show_fails(self) -> None:
    class FailingDisplay:
        WIDTH = 800
        HEIGHT = 480

        def set_image(self, image: Image.Image) -> None:
            self.image = image

        def show(self) -> None:
            raise OSError("SPI failure")

    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            channel_slugs=["demo"],
            preview_output=Path(tmpdir) / "diagnostic.png",
        )
        image = Image.new("RGB", (800, 480), "red")

        with patch("inky_arena.runtime._load_inky_display", return_value=FailingDisplay()):
            with self.assertRaises(DisplayPublishError):
                publish_image(image, config)

        self.assertTrue(config.preview_output.exists())

def test_publish_image_raises_when_set_image_fails(self) -> None:
    class FailingDisplay:
        WIDTH = 800
        HEIGHT = 480

        def set_image(self, image: Image.Image) -> None:
            raise OSError("frame transfer failed")

        def show(self) -> None:
            raise AssertionError("show must not run after set_image failure")

    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            channel_slugs=["demo"],
            preview_output=Path(tmpdir) / "diagnostic.png",
        )

        with patch("inky_arena.runtime._load_inky_display", return_value=FailingDisplay()):
            with self.assertRaises(DisplayPublishError):
                publish_image(Image.new("RGB", (800, 480), "red"), config)

        self.assertTrue(config.preview_output.exists())

def test_diagnostic_preview_failure_does_not_hide_hardware_error(self) -> None:
    config = AppConfig(channel_slugs=["demo"])
    discovery_error = OSError("display missing")

    with (
        patch("inky_arena.runtime._load_inky_display", side_effect=discovery_error),
        patch("inky_arena.runtime._save_preview", side_effect=OSError("preview unwritable")),
    ):
        with self.assertRaises(DisplayPublishError) as context:
            publish_image(Image.new("RGB", (800, 480), "red"), config)

    self.assertIs(context.exception.__cause__, discovery_error)
```

- [ ] **Step 3: Write failing state-preservation and service-exit tests**

Add:

```python
def test_display_failure_preserves_candidate_rotation_state(self) -> None:
    candidate = DisplayCandidate(
        id="hardware-failure",
        channel_slug="demo",
        channel_title="Demo",
        block_type="Image",
        title="Hardware failure",
        image_url="https://example.com/failure.png",
    )
    state = AppState(queue_ids=[candidate.id])
    config = AppConfig(channel_slugs=["demo"])

    with (
        patch("inky_arena.runtime.render_candidate", return_value=Image.new("RGB", (800, 480), "red")),
        patch("inky_arena.runtime.publish_image", side_effect=DisplayPublishError("display failed")),
    ):
        with self.assertRaises(DisplayPublishError):
            _try_display_queue(config, FakeClient([candidate]), state, {candidate.id: candidate}, [candidate])

    self.assertEqual(state.queue_ids, [candidate.id])
    self.assertEqual(state.shown_ids, [])
    self.assertIsNone(state.last_displayed_id)

def test_run_forever_exits_when_display_publish_fails(self) -> None:
    config = AppConfig(channel_slugs=["demo"])

    with (
        patch("inky_arena.runtime.ArenaClient"),
        patch("inky_arena.runtime.load_state", return_value=AppState()),
        patch("inky_arena.runtime.refresh_once", side_effect=DisplayPublishError("display failed")),
        patch("inky_arena.runtime.notify_systemd") as mock_notify,
        patch("inky_arena.runtime.sleep_with_watchdog") as mock_sleep,
    ):
        with self.assertRaises(SystemExit) as context:
            run_forever(config)

    self.assertEqual(context.exception.code, 1)
    self.assertFalse(mock_sleep.called)
    self.assertTrue(any("display failed" in call.args[0] for call in mock_notify.call_args_list))
```

- [ ] **Step 4: Run the new publication tests and verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_runtime.RuntimeTests.test_publish_image_writes_preview_when_inky_is_unavailable tests.test_runtime.RuntimeTests.test_publish_image_raises_when_display_discovery_fails tests.test_runtime.RuntimeTests.test_publish_image_raises_when_display_show_fails tests.test_runtime.RuntimeTests.test_publish_image_raises_when_set_image_fails tests.test_runtime.RuntimeTests.test_diagnostic_preview_failure_does_not_hide_hardware_error tests.test_runtime.RuntimeTests.test_display_failure_preserves_candidate_rotation_state tests.test_runtime.RuntimeTests.test_run_forever_exits_when_display_publish_fails
```

Expected: failures because the error type and internal display-loading boundary do not exist and hardware failures are currently swallowed.

- [ ] **Step 5: Add the publication error and display-loading boundary**

Near `RefreshTimeout`, add:

```python
class DisplayPublishError(RuntimeError):
    """Raised when installed Inky hardware cannot publish a frame."""


def _load_inky_display() -> object | None:
    try:
        from inky.auto import auto
    except ImportError:
        return None
    return auto()
```

- [ ] **Step 6: Make hardware failures save diagnostics and then raise**

Replace `publish_image` with:

```python
def publish_image(image: Image.Image, config: AppConfig) -> None:
    try:
        display = _load_inky_display()
    except Exception as exc:  # noqa: BLE001
        _save_failed_publish_preview(image, config, exc)
        raise DisplayPublishError(f"Inky display discovery failed: {exc}") from exc

    if display is None:
        _save_preview(image, config, "Inky library unavailable")
        return

    try:
        logging.info("Publishing image to Inky display")
        output = image
        if config.display_orientation == "portrait":
            output = image.rotate(90, expand=True)
        resized = output.resize((display.WIDTH, display.HEIGHT))
        display.set_image(resized)
        logging.info("Starting Inky display refresh")
        display.show()
        logging.info("Finished Inky display refresh")
    except Exception as exc:  # noqa: BLE001
        _save_failed_publish_preview(image, config, exc)
        raise DisplayPublishError(f"Inky display publish failed: {exc}") from exc


def _save_failed_publish_preview(image: Image.Image, config: AppConfig, hardware_error: Exception) -> None:
    try:
        _save_preview(image, config, f"Inky hardware unavailable ({hardware_error})")
    except Exception:  # noqa: BLE001
        logging.exception("Unable to save diagnostic preview after Inky hardware failure")
```

- [ ] **Step 7: Propagate hardware errors without consuming the candidate**

In `_try_display_queue`, add the dedicated handler before the generic candidate-failure handler:

```python
except DisplayPublishError:
    state.queue_ids.insert(0, next_id)
    raise
except Exception as exc:  # noqa: BLE001
    state.shown_ids = _append_unique(state.shown_ids, candidate.id, limit=shown_limit)
    logging.warning("Skipping block %s after image/render failure: %s", candidate.id, exc)
```

In `run_forever`, add this handler before the generic `except Exception` block:

```python
except DisplayPublishError as exc:
    logging.error("%s", exc)
    notify_systemd(f"STATUS={exc}")
    raise SystemExit(1) from exc
```

- [ ] **Step 8: Run all runtime tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_runtime
```

Expected: all runtime tests pass without the real `inky` package installed.

- [ ] **Step 9: Commit truthful publication behavior**

```bash
git add inky_arena/runtime.py tests/test_runtime.py
git commit -m "Surface Inky hardware failures"
```

---

### Task 3: Enforce Rate-Limit Backoff During Forced Refresh

**Files:**
- Modify: `inky_arena/runtime.py:159-187,285-360`
- Modify: `tests/test_runtime.py:166-195,228-302`

**Interfaces:**
- Consumes: `AppState.next_sync_not_before_iso` and existing cached candidates.
- Produces: `_rate_limit_backoff_active(state: AppState, now: datetime | None = None) -> bool`. `_load_candidates(..., force_refresh=True)` retains its signature but cannot bypass an active backoff.

- [ ] **Step 1: Import the independently testable backoff helper in runtime tests**

Add `_rate_limit_backoff_active` to the `inky_arena.runtime` imports in `tests/test_runtime.py`.

- [ ] **Step 2: Write the failing exhausted-queue backoff test**

Change the datetime import at the top of the test file to:

```python
from datetime import datetime, timedelta
```

Add:

```python
def test_refresh_once_restarts_cached_rotation_without_sync_during_backoff(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        now = datetime.now().astimezone()
        config = AppConfig(
            channel_slugs=["demo"],
            state_path=Path(tmpdir) / "state.json",
            preview_output=Path(tmpdir) / "preview.png",
        )
        cached = DisplayCandidate(
            id="cached",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Cached",
            image_url="https://example.com/cached.png",
        )
        state = AppState(
            cached_candidates=[cached],
            shown_ids=[cached.id],
            last_candidate_ids=[cached.id],
            last_displayed_id=cached.id,
            last_sync_iso=now.isoformat(),
            next_sync_not_before_iso=(now + timedelta(hours=1)).isoformat(),
        )
        client = SequenceClient([[cached]])

        with patch("inky_arena.runtime.publish_image"):
            updated = refresh_once(config, client, state, rng=random.Random(1))

        self.assertEqual(client.fetch_candidates_with_metadata_calls, 0)
        self.assertEqual(updated.last_displayed_id, cached.id)
        self.assertEqual(updated.shown_ids, [cached.id])
```

- [ ] **Step 3: Write invalid and expired timestamp tests**

Add:

```python
def test_invalid_backoff_timestamp_warns_and_allows_sync(self) -> None:
    state = AppState(next_sync_not_before_iso="not-a-timestamp")

    with self.assertLogs(level="WARNING") as logs:
        active = _rate_limit_backoff_active(state)

    self.assertFalse(active)
    self.assertTrue(any("invalid next_sync_not_before_iso" in message for message in logs.output))

def test_expired_backoff_does_not_block_forced_sync(self) -> None:
    now = datetime.now().astimezone()
    state = AppState(next_sync_not_before_iso=(now - timedelta(minutes=1)).isoformat())

    self.assertFalse(_rate_limit_backoff_active(state, now=now))
```

- [ ] **Step 4: Run the backoff tests and verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_runtime.RuntimeTests.test_refresh_once_restarts_cached_rotation_without_sync_during_backoff tests.test_runtime.RuntimeTests.test_invalid_backoff_timestamp_warns_and_allows_sync tests.test_runtime.RuntimeTests.test_expired_backoff_does_not_block_forced_sync
```

Expected: the helper tests fail because the helper does not exist, and the exhausted-queue test reports one forced API call.

- [ ] **Step 5: Add the hard-backoff helper**

Add near `_should_use_cached_candidates`:

```python
def _rate_limit_backoff_active(state: AppState, now: datetime | None = None) -> bool:
    if not state.next_sync_not_before_iso:
        return False

    try:
        next_allowed_sync = datetime.fromisoformat(state.next_sync_not_before_iso)
    except ValueError:
        logging.warning("Ignoring invalid next_sync_not_before_iso: %r", state.next_sync_not_before_iso)
        return False

    if next_allowed_sync.tzinfo is None:
        logging.warning("Ignoring invalid next_sync_not_before_iso without timezone: %r", state.next_sync_not_before_iso)
        return False

    current = now or datetime.now().astimezone()
    return current < next_allowed_sync
```

- [ ] **Step 6: Enforce backoff before ordinary freshness and force-refresh logic**

At the start of `_load_candidates`, after `effective_slugs`, add:

```python
if state.cached_candidates and _rate_limit_backoff_active(state):
    logging.info("Using cached candidate pool during API backoff")
    return state.cached_candidates
```

Retain the existing ordinary freshness branch immediately after it:

```python
if not force_refresh and _should_use_cached_candidates(config, state):
    logging.info("Using cached candidate pool")
    return state.cached_candidates
```

Update `_should_use_cached_candidates` to use the helper instead of parsing the timestamp inline:

```python
if _rate_limit_backoff_active(state):
    return True
```

Remove the replaced inline `datetime.fromisoformat(state.next_sync_not_before_iso)` block. Change the queue-exhaustion log message to:

```python
logging.info("Current candidate pool is exhausted; checking live sync eligibility before restarting rotation")
```

- [ ] **Step 7: Run runtime tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_runtime
```

Expected: all runtime tests pass. The existing recent-sync/no-backoff test still performs one forced live sync, while the new future-backoff test performs none.

- [ ] **Step 8: Commit hard backoff behavior**

```bash
git add inky_arena/runtime.py tests/test_runtime.py
git commit -m "Honor API backoff during rotation"
```

---

### Task 4: Make State Persistence Atomic and Recoverable

**Files:**
- Modify: `inky_arena/state.py:1-72`
- Create: `tests/test_state.py`

**Interfaces:**
- Consumes: `AppState`, `DisplayCandidate`, and a filesystem `Path`.
- Produces: unchanged `load_state(path: Path) -> AppState` and `save_state(path: Path, state: AppState) -> None` signatures, plus private validation and corrupt-path helpers.

- [ ] **Step 1: Create state round-trip and corrupt-content tests**

Create `tests/test_state.py` with:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inky_arena.models import AppState, DisplayCandidate
from inky_arena.state import load_state, save_state


class StateTests(unittest.TestCase):
    def test_state_round_trips_through_atomic_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            candidate = DisplayCandidate(
                id="one",
                channel_slug="demo",
                channel_title="Demo",
                block_type="Image",
                title="One",
                image_url="https://example.com/one.png",
            )
            state = AppState(
                queue_ids=["one"],
                shown_ids=["two"],
                cached_candidates=[candidate],
                last_displayed_id="two",
                channel_failure_counts={"walked": 2},
            )

            save_state(path, state)
            loaded = load_state(path)

            self.assertEqual(loaded.queue_ids, ["one"])
            self.assertEqual(loaded.shown_ids, ["two"])
            self.assertEqual(loaded.cached_candidates, [candidate])
            self.assertEqual(loaded.last_displayed_id, "two")
            self.assertEqual(loaded.channel_failure_counts, {"walked": 2})

    def test_malformed_json_is_preserved_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text('{"queue_ids": [', encoding="utf-8")

            with self.assertLogs(level="WARNING"):
                loaded = load_state(path)

            self.assertEqual(loaded, AppState())
            self.assertFalse(path.exists())
            corrupt_files = list(Path(tmpdir).glob("state.json.*.corrupt"))
            self.assertEqual(len(corrupt_files), 1)
            self.assertEqual(corrupt_files[0].read_text(encoding="utf-8"), '{"queue_ids": [')

    def test_incompatible_state_shape_is_preserved_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text(json.dumps({"queue_ids": "not-a-list"}), encoding="utf-8")

            with self.assertLogs(level="WARNING"):
                loaded = load_state(path)

            self.assertEqual(loaded, AppState())
            self.assertEqual(len(list(Path(tmpdir).glob("state.json.*.corrupt"))), 1)
```

- [ ] **Step 2: Add atomic-replacement and filesystem-failure tests**

Append:

```python
    def test_replace_failure_preserves_previous_valid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            save_state(path, AppState(last_displayed_id="old"))

            with patch("inky_arena.state.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    save_state(path, AppState(last_displayed_id="new"))

            self.assertEqual(load_state(path).last_displayed_id, "old")
            self.assertEqual(list(Path(tmpdir).glob(".state.json.*.tmp")), [])

    def test_state_read_permission_error_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text("{}", encoding="utf-8")

            with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    load_state(path)

    def test_corrupt_state_rename_failure_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text("not-json", encoding="utf-8")

            with patch.object(Path, "replace", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    load_state(path)
```

- [ ] **Step 3: Run the state tests and verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_state
```

Expected: the round-trip test passes against the old writer, while malformed recovery and replacement-preservation tests fail.

- [ ] **Step 4: Add strict state decoding and corrupt-file recovery**

Add imports:

```python
import logging
import os
import tempfile
from datetime import datetime
```

Extract candidate and state decoding into helpers:

```python
def _string_list(payload: dict[str, object], name: str) -> list[str]:
    value = payload.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Invalid persisted {name}: expected a list of strings")
    return list(value)


def _optional_string(payload: dict[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Invalid persisted {name}: expected a string or null")
    return value


def _decode_state(payload: object) -> AppState:
    if not isinstance(payload, dict):
        raise ValueError("Invalid persisted state: expected a JSON object")

    raw_candidates = payload.get("cached_candidates", [])
    if not isinstance(raw_candidates, list) or not all(isinstance(item, dict) for item in raw_candidates):
        raise ValueError("Invalid persisted cached_candidates: expected a list of objects")

    cached_candidates = [
        DisplayCandidate(
            id=str(item["id"]),
            channel_slug=str(item["channel_slug"]),
            channel_title=str(item["channel_title"]),
            block_type=str(item["block_type"]),
            title=str(item["title"]),
            image_url=str(item["image_url"]),
            source_url=item.get("source_url"),
            source_title=item.get("source_title"),
            href=item.get("href"),
            updated_at=item.get("updated_at"),
        )
        for item in raw_candidates
    ]

    raw_failure_counts = payload.get("channel_failure_counts", {})
    if not isinstance(raw_failure_counts, dict):
        raise ValueError("Invalid persisted channel_failure_counts: expected an object")

    return AppState(
        queue_ids=_string_list(payload, "queue_ids"),
        shown_ids=_string_list(payload, "shown_ids"),
        last_candidate_ids=_string_list(payload, "last_candidate_ids"),
        cached_candidates=cached_candidates,
        last_displayed_id=_optional_string(payload, "last_displayed_id"),
        last_sync_iso=_optional_string(payload, "last_sync_iso"),
        next_sync_not_before_iso=_optional_string(payload, "next_sync_not_before_iso"),
        last_error=_optional_string(payload, "last_error"),
        discovered_channels=_string_list(payload, "discovered_channels"),
        channel_failure_counts={str(key): int(value) for key, value in raw_failure_counts.items()},
    )


def _corrupt_state_path(path: Path) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    return path.with_name(f"{path.name}.{timestamp}.corrupt")
```

Replace `load_state` with:

```python
def load_state(path: Path) -> AppState:
    if not path.exists():
        return AppState()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _decode_state(payload)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        corrupt_path = _corrupt_state_path(path)
        path.replace(corrupt_path)
        logging.warning("Preserved invalid state at %s and started fresh: %s", corrupt_path, exc)
        return AppState()
```

- [ ] **Step 5: Replace direct state writes with an atomic writer**

Keep the existing payload shape, serialize it to `serialized`, and replace the final `path.write_text(...)` with:

```python
serialized = json.dumps(payload, indent=2, sort_keys=True)
temporary_path: Path | None = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, path)
except Exception:
    if temporary_path is not None:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            logging.warning("Unable to clean up temporary state file %s: %s", temporary_path, cleanup_error)
    raise
```

- [ ] **Step 6: Run state and runtime tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_state tests.test_runtime
```

Expected: all state and runtime tests pass, including existing refresh-cycle state writes.

- [ ] **Step 7: Commit atomic state recovery**

```bash
git add inky_arena/state.py tests/test_state.py
git commit -m "Harden persisted rotation state"
```

---

### Task 5: Split Core and Raspberry Pi Dependencies

**Files:**
- Modify: `requirements.txt`
- Create: `requirements-pi.txt`
- Modify: `README.md:16-28,73-83`

**Interfaces:**
- Consumes: the display-loading boundary from Task 2, which allows tests to run without importing `inky`.
- Produces: a platform-neutral `requirements.txt` and a Pi-specific `requirements-pi.txt`.

- [ ] **Step 1: Move Inky to the Pi requirements file**

Set `requirements.txt` to:

```text
Pillow>=10.0.0
requests>=2.31.0
```

Create `requirements-pi.txt` with:

```text
-r requirements.txt
inky>=2.0.0
```

- [ ] **Step 2: Document separate Mac and Pi setup commands**

Replace the README quick-start installation step with text that gives these exact commands for development:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml
.venv/bin/python main.py
```

Explain that this path writes `cache/preview.png` because the hardware package is absent. Add a Pi setup subsection with:

```bash
.venv/bin/pip install -r requirements-pi.txt
```

State that the live Pi must use the interpreter from its verified systemd `ExecStart`, which may not be the repository-local `.venv`.

- [ ] **Step 3: Verify core installation in a clean Mac virtual environment**

Run:

```bash
python3 -m venv /tmp/inky-arena-core-test-venv
/tmp/inky-arena-core-test-venv/bin/pip install -r requirements.txt
/tmp/inky-arena-core-test-venv/bin/python -m unittest discover tests/
```

Expected: dependency installation succeeds without attempting to build `gpiod` or `spidev`, and the complete test suite passes without the real `inky` package.

- [ ] **Step 4: Commit the dependency split**

```bash
git add requirements.txt requirements-pi.txt README.md
git commit -m "Split development and Pi dependencies"
```

---

### Task 6: Add the Guarded Deployment and Rollback Runbook

**Files:**
- Create: `docs/deployment.md`
- Modify: `README.md:81-83`

**Interfaces:**
- Consumes: `requirements-pi.txt`, `deploy/systemd/inky-arena.service`, and the verified commit produced by the completed implementation.
- Produces: an operator-facing manual procedure; it performs no deployment automatically.

- [ ] **Step 1: Write the preflight section**

Create `docs/deployment.md` with an explicit warning that `config.toml`, tokens, and full environment output must not be shared. Require the operator to enter a confirmed Pi address rather than embedding one:

```bash
read -r INKY_PI_HOST
ssh "jcampbell@$INKY_PI_HOST"
```

On the Pi, document these read-only checks:

```bash
hostname
cd /home/jcampbell/inky-arena
pwd -P
git status --short --branch
git branch --show-current
git rev-parse HEAD
find .git -maxdepth 1 -name '*.lock' -print
systemctl --user cat inky-arena.service
systemctl --user status inky-arena.service --no-pager
```

State that deployment stops if the path is unexpected, tracked changes are present, a Git lock exists, the branch is unexpected, or the live `ExecStart` interpreter cannot be identified. Do not instruct the operator to display `config.toml` or `ARENA_TOKEN`.

- [ ] **Step 2: Write the guarded update and verification section**

Record rollback information without overwriting repository files:

```bash
git rev-parse HEAD > /tmp/inky-arena-predeploy-sha
git branch --show-current > /tmp/inky-arena-predeploy-branch
git fetch --prune origin
git merge --ff-only origin/main
```

Require the operator to set the interpreter path from the inspected `ExecStart` and verify it before installation:

```bash
read -r INKY_SERVICE_PYTHON
"$INKY_SERVICE_PYTHON" --version
"$INKY_SERVICE_PYTHON" -m pip install -r requirements-pi.txt
"$INKY_SERVICE_PYTHON" -m unittest discover tests/
systemctl --user restart inky-arena.service
systemctl --user status inky-arena.service --no-pager
journalctl --user -u inky-arena.service -n 80 --no-pager
```

Require physical confirmation that the panel refreshes and that a second rotation does not immediately show a hardware-failure restart loop.

- [ ] **Step 3: Write the non-destructive rollback section**

Document rollback only after the pre-deployment SHA file is confirmed to contain a commit:

```bash
git cat-file -e "$(cat /tmp/inky-arena-predeploy-sha)^{commit}"
git switch --detach "$(cat /tmp/inky-arena-predeploy-sha)"
systemctl --user restart inky-arena.service
systemctl --user status inky-arena.service --no-pager
journalctl --user -u inky-arena.service -n 80 --no-pager
```

Explain that this leaves the device detached at the known-good commit without deleting untracked `config.toml`. Returning to the deployment branch is a separate, deliberate `git switch` after the failure is understood. Explicitly prohibit `git reset --hard`, recursive deletion, and overwriting the machine-specific configuration.

- [ ] **Step 4: Link the runbook from README**

Replace the current one-line deployment section with a link to `docs/deployment.md`, state that deployment is manual, and note that the checked-in service unit's `/home/jcampbell/inky-app/.venv` path must be verified against the live unit before use.

- [ ] **Step 5: Verify runbook completeness and formatting**

Run:

```bash
rg -n "predeploy|ff-only|requirements-pi|restart|journalctl|physical|rollback|reset --hard|config.toml|ExecStart" docs/deployment.md README.md
git diff --check
```

Expected: every preflight, update, verification, security, and rollback term appears, and there are no whitespace errors.

- [ ] **Step 6: Commit the runbook**

```bash
git add docs/deployment.md README.md
git commit -m "Document guarded Pi deployment"
```

---

### Task 7: Run Full Regression and Acceptance Verification

**Files:**
- Verify only; modify files only if a preceding task's acceptance test exposes a defect, then amend that task's implementation with a new focused commit.

**Interfaces:**
- Consumes: all outputs from Tasks 1-6.
- Produces: evidence that every specification acceptance criterion passes in the Mac development environment. Physical Pi verification remains pending and separately authorized.

- [ ] **Step 1: Run the complete test suite in the core environment**

Run:

```bash
/tmp/inky-arena-core-test-venv/bin/python -m unittest discover tests/
```

Expected: all existing and new tests pass.

- [ ] **Step 2: Run syntax and whitespace verification**

Run:

```bash
/tmp/inky-arena-core-test-venv/bin/python -m compileall -q main.py inky_arena tests
git diff --check
```

Expected: both commands exit successfully with no output.

- [ ] **Step 3: Verify the dependency boundary**

Run:

```bash
/tmp/inky-arena-core-test-venv/bin/python -c "import requests; from PIL import Image; import importlib.util; assert importlib.util.find_spec('inky') is None"
```

Expected: exit status 0, proving the suite and core runtime imports do not require the Pi hardware package.

- [ ] **Step 4: Verify repository scope and commit history**

Run:

```bash
git status --short --branch
git log --oneline -10
```

Expected: only the intended hardening files changed, all implementation changes are committed, and the branch is ahead of `origin/main` only by the approved design, plan, and focused hardening commits.

- [ ] **Step 5: Record the remaining hardware boundary**

In the implementation handoff, state explicitly that Mac verification is complete but the following remain unverified until separately authorized on the Pi:

- live systemd `ExecStart` interpreter;
- installation of `requirements-pi.txt` on Raspberry Pi OS;
- SPI/GPIO communication with the Inky panel;
- physical refresh and restart behavior; and
- deployment and rollback commands against the live checkout.
