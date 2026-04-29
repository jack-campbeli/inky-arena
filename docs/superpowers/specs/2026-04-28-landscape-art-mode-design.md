# Landscape Art Mode Design

## Goal

Turn `inky-arena` from a portrait, captioned block viewer into a landscape-first art display for the 800x480 Inky panel. The normal display should prioritize aesthetically pleasing image presentation over metadata readability.

The physical display can be mounted horizontally, so the app should render natively in landscape rather than trying to make landscape images work inside a portrait frame.

## Current Behavior

The current renderer creates a 480x800 portrait canvas. It reserves a 64px footer and uses `ImageOps.contain` to fit the source image into the remaining 480x736 image area.

This preserves the full source image, but landscape images often become small on the vertical display. Using full fill/crop inside the portrait frame also looks wrong because landscape sources must be cropped too aggressively.

## Approved Direction

Use a whole-app landscape layout:

- Normal frame size is 800x480.
- The image owns essentially the full canvas.
- Remove the normal title/channel footer.
- Keep only a small time display in normal mode.
- Keep the degraded/error indicator tiny and only visible when needed.
- Continue using explicit text status screens for operational failures.

## Normal Render Layout

The normal candidate render should be an image-first landscape frame. The renderer should not draw the existing footer by default in landscape art mode.

The small time display should sit in a corner, likely bottom-right or top-right. If it is hard to read over busy images, it can use a compact backing shape. The backing should stay small and should not become a footer.

The degraded indicator should remain quiet, near the time display or another unobtrusive corner. It should preserve the existing distinction between healthy output and degraded/fallback output.

## Image Fitting Policy

The renderer should treat landscape or near-landscape images as the native case:

- Fill the 800x480 frame.
- Center the crop.
- Accept only small cropping when the source aspect ratio is near the display ratio.

Portrait and unusual aspect-ratio images should still look intentional:

- Prefer a full-frame aesthetic treatment over a tiny centered image.
- Allow side cropping when it produces a good art-object result.
- If the crop would be extreme, use a subtle image-derived background or matte with the source centered over it.
- Avoid decorative stars in normal landscape mode unless an image-derived matte leaves empty space that needs intentional treatment.

The guiding rule is: fill first, protect only against ugly or unreadable extreme crops, and keep metadata almost invisible.

## Configuration

Add a small layout/orientation concept instead of hard-coding a one-off landscape render.

Suggested configuration:

- `display_orientation = "landscape"` for this install.
- A metadata setting such as `metadata_mode = "time_only"` or an equivalent boolean/config pair that disables the footer while preserving the clock.

The exact names can be finalized during implementation, but the configuration should make the selected behavior explicit and testable.

The implementation should preserve existing config behavior where practical. If defaults change, the README and `config.example.toml` should make that clear.

## Status And Error Screens

Status/error screens are operational messages, not normal art mode. They should remain text-based and readable.

They should become landscape-aware so preview output and hardware output match the horizontal physical display. They do not need to follow the no-footer art layout.

## Runtime Impact

Runtime image rotation, caching, retry behavior, stale/degraded handling, and Are.na sync policy should not change as part of this work.

This change is scoped to presentation, configuration, and tests. Existing distinctions between healthy rotation, stale/fallback output, render-failure retry behavior, and true errors should remain intact.

## Testing

Add or update `unittest` coverage for:

- Landscape orientation/config parsing.
- Normal candidate render output dimensions.
- Landscape images filling most or all of the frame.
- Portrait images avoiding tiny centered contain behavior.
- Time-only overlay presence.
- Degraded indicator behavior in landscape art mode.
- Status rendering dimensions in landscape orientation.
- Existing runtime calls into `render_candidate`.

Run the full suite with:

```bash
.venv/bin/python -m unittest discover tests/
```

If the local checkout does not have `.venv`, use the known working project interpreter:

```bash
/home/jcampbell/inky-app/.venv/bin/python -m unittest discover tests/
```

## Out Of Scope

- Changing Are.na candidate selection.
- Changing no-repeat queue behavior.
- Resetting or clearing runtime cache.
- Adding image similarity dedupe.
- Building a browser or remote control UI.
- Making metadata prominent in normal mode.
