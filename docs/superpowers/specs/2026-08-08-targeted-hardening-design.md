# Targeted Hardening Release Design

**Date:** 2026-08-08
**Status:** Approved for implementation planning

## Goal

Resolve the five confirmed review findings as one bounded hardening release while preserving the application's existing Are.na rotation, rendering, caching, and development-preview behavior. Add a deployment and rollback runbook, but do not deploy to the Raspberry Pi until its access method and current state are verified separately.

## Confirmed Problems

1. Image downloads reuse API headers, so an Are.na bearer token can be sent to an arbitrary candidate image host.
2. Inky initialization and refresh failures are converted into successful preview writes, after which rotation state records the candidate as displayed.
3. Queue exhaustion forces a live sync even while `next_sync_not_before_iso` says the API is rate-limited.
4. State is written directly to `state.json`, and malformed state aborts startup before the refresh loop can recover.
5. The documented development install pulls Linux-only Inky dependencies on macOS even though development preview mode does not need the hardware package.

## Scope

This release will:

- separate authenticated Are.na API requests from unauthenticated image downloads;
- distinguish intentional development previews from failed hardware publication;
- treat server rate-limit backoff as a hard synchronization boundary;
- make state replacement atomic and recover from malformed or incompatible state;
- split core development dependencies from Raspberry Pi hardware dependencies;
- add focused regression tests for all five findings; and
- document a guarded deployment, verification, and rollback process.

This release will not:

- change candidate selection, channel walking, image composition, metadata layout, or rotation policy beyond the backoff correction;
- add deployment automation or attempt to discover, access, or modify the Pi;
- change `config.toml` or expose tokens in commands, logs, or documentation;
- alter the systemd unit's Python path without first verifying the live Pi; or
- introduce a general publisher, storage, or plugin framework.

## Architecture and Component Changes

### 1. Request authentication boundary

`ArenaClient` will expose separate internal header builders for API and asset traffic.

- Are.na API requests continue to use `Accept: application/json` and include `Authorization: Bearer <token>` when configured.
- Candidate image requests use image-appropriate headers without `Authorization`.
- The existing image URL sources, HTTP timeout, disk cache, and cached-image fallback remain unchanged.
- Redirect handling remains delegated to `requests`; because the original image request has no API credential, redirects cannot forward that credential.

No candidate-provided host will receive the Are.na token. If a future private-image flow demonstrably requires authentication, it must use an explicit trusted-host rule rather than restoring a shared header builder.

### 2. Display publication contract

The runtime will introduce a dedicated display-publication error and a small internal display-loading boundary that tests can replace without importing Raspberry Pi libraries.

`publish_image` will have three outcomes:

1. When the `inky` package is unavailable, save the normal development preview and return successfully.
2. When the package is available and the physical display refresh succeeds, return successfully.
3. When display discovery, image assignment, or refresh fails, attempt to save a diagnostic preview and then raise the dedicated display-publication error. Failure to save the diagnostic preview must not hide the original hardware failure.

The queue processor will propagate the dedicated publication error without marking the candidate shown, changing `last_displayed_id`, or trying every other candidate against the same failed device. Image download, decoding, blank-image, and candidate-specific render failures will retain the existing skip-and-continue behavior.

`run_forever` will handle the publication error as a terminal refresh failure: log it, update systemd status, and exit nonzero so the existing `Restart=always` policy can restart the process. Successful development previews continue to advance local rotation state because the preview file is the intended development output.

### 3. Hard rate-limit boundary

Rate-limit eligibility will be evaluated separately from ordinary `sync_minutes` freshness.

- An active, valid `next_sync_not_before_iso` always prevents a live API sync.
- `force_refresh=True` may bypass the ordinary sync interval but may not bypass active rate-limit backoff.
- Invalid stored timestamps are logged or ignored and do not permanently suppress synchronization.
- When the current rotation is exhausted during backoff, the runtime reuses cached candidates and begins a new cached rotation instead of calling the API.
- Once the backoff expires, queue exhaustion can perform the existing forced live sync before beginning another rotation.

The implementation may reuse `_should_use_cached_candidates`, but the hard-backoff rule must remain independently testable so future changes cannot accidentally couple it to cache age.

### 4. Atomic state and startup recovery

`save_state` will serialize the complete payload before replacing the live file.

1. Ensure the parent directory exists.
2. Create a uniquely named temporary file in the same directory as the target.
3. Write the serialized JSON, flush it, and sync the file descriptor.
4. Atomically replace the target with `os.replace`.
5. Remove a leftover temporary file on failure when possible, then propagate the filesystem error.

Using the same directory is required so the replacement stays on one filesystem and remains atomic.

`load_state` will distinguish bad content from environmental failures:

- Missing state still returns a fresh `AppState`.
- JSON decoding, required-field conversion, and incompatible persisted-value errors cause the bad file to be renamed to a unique, timestamped `.corrupt` sibling. The recovery is logged and startup continues with a fresh `AppState`.
- Permission errors and other filesystem failures remain visible and abort startup rather than silently discarding state.
- If the corrupt file cannot be preserved, that filesystem failure remains visible.

This recovery deliberately sacrifices rotation history only when the persisted data cannot be safely interpreted. Cached candidates can then be rebuilt through the normal sync path.

### 5. Platform-specific dependency boundary

Dependency files will reflect the application's existing runtime boundary.

- `requirements.txt` contains the platform-neutral runtime dependencies: Pillow and Requests.
- `requirements-pi.txt` includes `-r requirements.txt` and the Inky hardware package.
- Mac development setup installs `requirements.txt` and uses preview output.
- Raspberry Pi setup installs `requirements-pi.txt` into the interpreter used by the live service.

Tests will patch the runtime's internal display-loading boundary instead of importing or installing the real `inky` package. This verifies publication control flow on macOS while reserving SPI/GPIO and physical-panel verification for the Pi.

## Data Flow

The healthy hardware path remains:

1. Determine whether cached candidates may be used.
2. Sync Are.na only when ordinary freshness and hard backoff rules allow it.
3. Select the next candidate from the rotation queue.
4. Download its image without API credentials.
5. Render the frame.
6. Publish it to the physical display.
7. Only after successful publication, update and atomically persist rotation state.

The development path replaces step 6 with an intentional preview write. The hardware-failure path stops before step 7 and exits for service restart.

## Error Handling

- API and image request errors retain the existing channel or candidate fallback behavior.
- A rate-limit reset controls when another API request is allowed; it is not itself treated as a process failure when cached candidates exist.
- Candidate-specific image and render errors remain skippable.
- Display hardware errors are process-level failures because trying another candidate cannot repair the device.
- Malformed state is quarantined and recovered; filesystem access failures are surfaced.
- Diagnostic-preview failure is logged alongside, but does not replace, the display error that triggered it.

## Testing Strategy

Focused `unittest` coverage will verify:

### Request security

- API calls include the configured bearer token.
- Image calls to both Are.na and external hosts omit the bearer token.
- Existing successful image caching and cached fallback still work.

### Display publication

- Missing `inky` support writes a preview and returns success.
- A successful fake hardware display receives the correctly oriented image.
- Display discovery, `set_image`, and `show` failures raise the dedicated publication error.
- Hardware failure does not mark a candidate displayed or remove it from the durable rotation.
- `run_forever` exits nonzero on the dedicated error so systemd can restart it.

### Rate limiting

- An exhausted queue inside a future backoff window performs no API call.
- Cached candidates begin another rotation during that window.
- An expired backoff permits the existing forced live sync.
- Ordinary sync freshness behavior remains unchanged.

### State persistence

- State round-trips through the atomic writer.
- Replacement failure preserves the previous valid state.
- Malformed JSON and incompatible values are moved to a `.corrupt` file and return a fresh state.
- Permission and other filesystem failures propagate.

### Dependencies and regression coverage

- The standard Mac test suite runs with only core requirements installed.
- All existing config, candidate normalization, rendering, rotation, cache, watchdog, and orientation tests continue passing.

## Deployment and Rollback Runbook

Add `docs/deployment.md` with a guarded manual procedure. It will not assume a hostname or automatically connect to a device.

The runbook will require an operator to:

1. Identify the Pi and establish an authenticated shell.
2. Confirm the hostname, repository path, current Git commit, current branch, and clean tracked worktree.
3. Stop if tracked changes, Git locks, unexpected paths, or an unexpected branch are present.
4. Inspect the live systemd unit and record the actual Python interpreter before installing anything.
5. Record the pre-deployment commit for rollback.
6. Update the checkout using a fast-forward-only Git operation.
7. Install `requirements-pi.txt` with the verified service interpreter.
8. Restart `inky-arena.service` and inspect status and recent journal output.
9. Confirm a physical panel refresh and expected rotation behavior.
10. If verification fails, return the device checkout to the recorded commit without deleting `config.toml` or other untracked machine-specific files, restart the service, and verify the prior version.

The runbook will explicitly warn against displaying `config.toml`, tokens, or complete service environments in shared logs. It will also call out the currently checked-in `/home/jcampbell/inky-app/.venv` interpreter path as something to verify rather than silently preserve or replace.

## Acceptance Criteria

- No candidate-controlled image request receives the Are.na bearer token.
- A physical publication failure cannot be recorded as a successful display and causes a nonzero process exit.
- No API request occurs before a valid future `next_sync_not_before_iso`, including after queue exhaustion.
- An interrupted state replacement cannot leave a partially written live state file.
- Malformed or incompatible state is preserved and recovered without a restart loop.
- Core dependencies install and tests run on macOS without Linux GPIO/SPI packages.
- Raspberry Pi hardware dependencies remain available through `requirements-pi.txt`.
- The full existing and new test suite passes.
- The deployment runbook covers preflight, update, verification, and rollback without assuming live device access.
- No actual Pi deployment occurs as part of this implementation unless separately authorized after device state is verified.

## Risks and Mitigations

- **A private image host might require authentication.** Never send the Are.na token to an arbitrary host. If a real failure demonstrates an authenticated asset requirement, add a narrowly documented trusted-host mechanism with tests.
- **A hardware failure may cause repeated systemd restarts.** This is preferable to reporting a stale panel as healthy and follows the service's existing restart policy. Logs and systemd status will expose the failure for diagnosis.
- **State recovery loses rotation history.** Recovery occurs only for uninterpretable state, and the corrupt file is preserved for inspection.
- **The live Pi may use an unexpected interpreter or checkout state.** The runbook fails closed on those differences; this release does not alter the live device automatically.
