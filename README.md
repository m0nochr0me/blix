# Blix

Pixel-art editing tools for the Blender image editor: pixel grid, rulers and guides, box and
ellipse selection with boolean modes and nearest-neighbor transforms, shape drawing, mirror
symmetry, a layer stack, and ordered Bayer dithering.

Requires Blender 5.2 LTS or newer.

## Install

Edit > Preferences > Get Extensions > Install from Disk, pick `blix-<version>.zip`.

Build the zip from a checkout:

```sh
blender --command extension build
```

## Use

Panels live in the Image Editor sidebar (`N`) under the **Blix** tab. Tools live in the toolbar
(`T`) in Paint mode.

### Pixel Grid

Draws one line per pixel boundary, fading in past 4 screen pixels per image pixel, plus major
lines every *Major Every* pixels (0 disables). Blix disables Blender's native image grid overlay
while its own grid is on.

### Rulers & Guides

Rulers band the top and left edges of the region, with zoom-adaptive ticks labelled in image
pixels, origin top-left. Guides are stored per image and survive save/reload.

| Action | Input |
| --- | --- |
| Create guide | LMB drag out of a ruler band |
| Move guide | Ctrl+LMB within 6 px of a guide |
| Delete guide | Drag it outside the image |
| Cancel drag | Esc or RMB |

Add, remove and clear also exist as buttons; the list edits positions numerically.

### Selection & Transform

*Blix Select Box* and *Blix Select Ellipse* tools. A selection is a per-pixel mask, so it can be
any shape. Marquees snap to whole pixels. Moving or transforming lifts the masked pixels into a
floating buffer drawn with a nearest-neighbor GPU preview; the commit clears the masked source and
composites the buffer with straight-alpha over. Marching ants trace the mask itself, holes
included.

| Action | Input |
| --- | --- |
| Marquee | LMB drag |
| Add to selection | Shift+LMB drag |
| Subtract from selection | Ctrl+LMB drag |
| Move selection | LMB drag inside it, or `G` |
| Rotate freely | `R`, Ctrl snaps to 15° |
| Scale | `S` |
| Nudge 1 px | Arrow keys while moving |
| Confirm | LMB or Enter |
| Cancel | Esc or RMB |
| Clear selection | Esc |

The *Mode* row in the Selection panel sets the default for a plain LMB drag; Shift and Ctrl
override it for that drag. Ctrl+LMB within 6 px of a guide still grabs the guide instead of
subtracting. Quarter turns and flips are panel buttons. With a layer stack present, selection edits
target the active layer.

### Shapes

*Blix Shape* tool. Drag to draw; the GPU preview shows the exact pixels before the commit. Shape
kind (line, rectangle, ellipse, hexagon), fill and hexagon orientation live in the Shapes panel.
Outlines are 1 px. An active selection clips the result.

| Action | Input |
| --- | --- |
| Draw | LMB drag, corner to corner |
| Constrain | Shift — square/circle/regular hexagon, 45° for lines |
| Draw from center | Alt |
| Secondary color | Ctrl |
| Cancel | Esc or RMB |

### Mirror

*Mirror H* reflects left to right across the vertical center axis, *Mirror V* top to bottom; both
on gives four quadrant copies. Enabled axes are drawn over the image. Reflection is exact for odd
and even canvas sizes — the center row or column maps onto itself.

Blender's native brush is mirrored too — paint, erase, Ctrl invert, Shift smooth, any brush type.
Blender exposes no stroke hook, so Blix snapshots the image on mouse press and reflects the pixels
the stroke writes while it runs, which lands the mirrored half a frame behind the cursor.

### Layers

*Initialize Layers* turns the current image into a canvas with a Background layer. Layers are
Image datablocks packed into the .blend; the canvas holds the numpy composite. Index 0 is the top
layer. Blend modes: Mix, Multiply, Screen, Overlay, Add, Subtract, Darken, Lighten, Difference.

*Edit Active Layer* switches the editor to the layer image for painting; *Show Composite* switches
back and recomposites. Edits made straight on the composite canvas are synced into the active layer
before the next recomposite.

### Dither

*Blix Dither Gradient* drags a Bayer-thresholded ramp between the brush primary and secondary
colors — Ctrl snaps the direction to 45°. *Blix Dither Brush* paints a Bayer-masked stamp — Ctrl
paints the secondary color. Both honor an active selection as their bounds. Pattern size (2/4/8),
brush size and density are in the Dither panel.

### Palette

*Import Hex Palette* in the Palette panel loads a `.hex` file — one hex code per line, `#` prefix
optional — into a new palette named after the file and makes it the active image-paint palette.
Lines that are not a 6-digit code are skipped and counted in the report. Codes are read as sRGB and
decoded to scene linear, which is how Blender 5 stores brush and swatch colors, so a swatch paints
back the exact source code on an 8-bit image.

## Preferences

Edit > Preferences > Add-ons > Blix: guide, grid, ruler background, ruler text and mirror axis
colors, ruler band width, and every Blix key binding (guide drag plus the tool keymaps) for
rebinding.

## Limits

- Native brush mirroring reflects pixel values, not brush dabs: overlapping soft or low-strength
  dabs do not double-blend where the mirrored half meets the painted one, pixels the mirror wrote
  are not reflected back, and there is no mirrored brush cursor. An active selection does not clip
  it, since native paint ignores the selection as well. The dither gradient is not mirrored; it
  already fills its whole target region.
- Layer images are packed as PNG, so layer storage is 8 bit per channel.
- Every pixel operation pushes one extra no-op image undo step; that bracket is what makes direct
  pixel writes revertible.
- Painting within 0.2 s of a layer property change can bake the stale composite into that stroke's
  undo state.

## License

GPL-3.0-or-later.
