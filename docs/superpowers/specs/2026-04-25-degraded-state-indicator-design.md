# Degraded State Indicator

**Date:** 2026-04-25

## Problem

The current UI shows a text badge ("Stored image shown - live refresh unavailable.") when an image is served from disk cache. This badge overlaps with the clock in the footer, and the signal it conveys (image-level caching) is not meaningful to the user. What matters is whether the Are.na sync is healthy.

## Goal

Replace the text badge with a small, unobtrusive filled dot in the bottom-right corner of the display that lights up when the system is in a degraded state (Are.na sync failed). The indicator should be subtle — visible to someone looking for it, but not disruptive to the visual design.

## Signal Definition

**Degraded** = `state.last_error is not None`

This is set in `runtime.py` whenever an Are.na channel sync fails and the system falls back to cached candidates. It is cleared on a successful sync.

## Rendering (`render.py`)

- Replace `footer_note: str | None = None` with `degraded: bool = False` on `render_candidate`.
- When `degraded=True`, draw a filled circle: accent orange (`#c86c20`), radius 5px, centered at `(display_width - 10, display_height - 10)`.
- Remove all badge-related code: the rounded rect, note text, `note_left` variable, and the `meta_max_width` adjustment that compensated for the badge.

The dot sits entirely within the bottom-right corner of the canvas and does not interact with any footer text elements.

## Runtime wiring (`runtime.py`)

- Remove the `FALLBACK_IMAGE_NOTE` constant.
- In `_try_display_queue`, replace:
  ```python
  footer_note = FALLBACK_IMAGE_NOTE if image_result.from_cache else None
  ```
  with:
  ```python
  degraded = state.last_error is not None
  ```
- Pass `degraded=degraded` to `render_candidate`. No signature changes to `_try_display_queue` are needed since `state` is already an argument.

## Tests

Update any tests referencing `footer_note` or `FALLBACK_IMAGE_NOTE` to use the new `degraded` parameter.
