# Icons

The PNGs here are the project logo — a speech-bubble-and-checklist mark on a navy plate.
`scripts/make-icons.py` draws a different, flat-green placeholder set; run it only if you
want to go back to that mark.

## Using your own

Overwrite the files, keeping the same names and pixel dimensions, and restart the server.
That is the whole procedure. You do not need to touch any Python: the routes that serve
these map a URL to a filename literally, and `../manifest.webmanifest` next door is a
static JSON file if you want to declare a different set of sizes.

| file | size | what it has to be |
| --- | --- | --- |
| `apple-touch-icon.png` | 180×180 | **Opaque, square corners.** iOS applies its own rounded mask; rounding it yourself shows the mask cutting into an already-rounded plate. iOS ignores the manifest entirely and uses this file. |
| `icon-192.png` | 192×192 | Transparent corners are fine. Browser and Android "any" icon. |
| `icon-512.png` | 512×512 | As above. Also the Android splash image. |
| `maskable-512.png` | 512×512 | **Opaque and full-bleed.** Android crops this to the launcher's own shape, so keep the artwork inside the middle ~80% and let the background run to the edge. |
| `favicon.svg` | — | The browser tab. A 64×64 PNG of the mark wrapped in an `<svg><image>` so the `image/svg+xml` route in `assets.py` needs no change. It does not follow `prefers-color-scheme` — that only worked for the old hand-drawn placeholder, which had a separate light/dark stroke pair; this is one fixed raster asset. |

Nothing needs clearing on the client afterwards: the service worker's cache key is a hash
of these files' bytes, so a new icon invalidates the old one on every device that has the
app installed.

## Regenerating the placeholders

`scripts/make-icons.py` draws them. It is a one-off tool, run by hand, and the app never
imports it:

```bash
uv run python scripts/make-icons.py
```
