# Blix

Pixel-art editing tools for the Blender image editor: pixel grid, rulers and guides, box,
ellipse, lasso, brush and wand selection with boolean modes, nearest-neighbor transforms and a
clipboard, shape drawing, mirror symmetry, a layer stack, and ordered Bayer dithering.

Requires Blender 5.2 LTS or newer.

## Install

Edit > Preferences > Get Extensions > Install from Disk, pick `blix-<version>.zip`.

Build the zip from a checkout:

```sh
blender --command extension build
```

Run it with no project `.venv` on PATH (`deactivate` first). Otherwise Blender's Python picks up
the venv and fails with `ModuleNotFoundError: No module named 'math'`.

## Use

Panels live in the Image Editor sidebar (`N`) under the **Blix** tab. Tools live in the toolbar
(`T`) in Paint mode.

### Pixel Grid

Draws one line per pixel boundary, fading in past 4 screen pixels per image pixel, plus major
lines every _Major Every_ pixels (0 disables). Blix disables Blender's native image grid overlay
while its own grid is on.

### Rulers & Guides

Rulers band the top and left edges of the region, with zoom-adaptive ticks labelled in image
pixels, origin top-left. Guides are stored per image and survive save/reload.

| Action       | Input                           |
| ------------ | ------------------------------- |
| Create guide | LMB drag out of a ruler band    |
| Move guide   | Ctrl+LMB within 6 px of a guide |
| Delete guide | Drag it outside the image       |
| Cancel drag  | Esc or RMB                      |

Add, remove and clear also exist as buttons; the list edits positions numerically.

### Selection & Transform

One toolbar group holds five tools — _Blix Select Box_, _Ellipse_, _Lasso_, _Brush_ and _Wand_.
A selection is a per-pixel mask, so it can be any shape. Marquees snap to whole pixels. The lasso
drags a freehand outline, the brush paints the mask with a round brush (diameter in the Selection
panel), and the wand click-selects the contiguous region of one color. Moving or transforming
lifts the masked pixels into a floating buffer drawn with a nearest-neighbor GPU preview; the
commit clears the masked source and composites the buffer with straight-alpha over. Marching ants
trace the mask itself, holes included.

| Action                  | Input                      |
| ----------------------- | -------------------------- |
| Marquee / lasso / brush | LMB drag                   |
| Wand select             | LMB click                  |
| Add to selection        | Shift+LMB                  |
| Subtract from selection | Ctrl+LMB                   |
| Move selection          | LMB drag inside it, or `G` |
| Duplicate and move      | Shift+`D`                  |
| Rotate freely           | `R`, Ctrl snaps to 15°     |
| Scale                   | `S`                        |
| Nudge 1 px              | Arrow keys while moving    |
| Confirm                 | LMB or Enter               |
| Cancel                  | Esc or RMB                 |
| Copy                    | Ctrl+`C`                   |
| Paste                   | Ctrl+`V`                   |
| Delete pixels           | `X` or Del                 |
| Clear selection         | Esc                        |

The _Mode_ row in the Selection panel sets the default for a plain LMB gesture; Shift and Ctrl
override it for that gesture. Ctrl+LMB within 6 px of a guide still grabs the guide instead of
subtracting. Quarter turns and flips are panel buttons, and Copy and Delete exist as buttons too.
Paste floats the clipboard as a movable selection — click or Enter commits, Esc cancels. With a
layer stack present, selection edits target the active layer.

### Shapes

One toolbar group holds four tools — _Blix Rectangle_, _Ellipse_, _Hexagon_ and _Line_ — hold the
button to switch. Drag to draw; the GPU preview shows the exact pixels before the commit. _Filled_
lives in the Shapes panel; _Pointy Top_ appears there while the hexagon tool is active. Outlines
are 1 px. An active selection clips the result, and enabled mirror axes reflect it.

| Action           | Input                                                |
| ---------------- | ---------------------------------------------------- |
| Draw             | LMB drag, corner to corner                           |
| Constrain        | Shift — square/circle/regular hexagon, 45° for lines |
| Draw from center | Alt                                                  |
| Secondary color  | Ctrl                                                 |
| Cancel           | Esc or RMB                                           |

### Mirror

_Mirror H_ reflects left to right across the vertical center axis, _Mirror V_ top to bottom; both
on gives four quadrant copies. Enabled axes are drawn over the image. Reflection is exact for odd
and even canvas sizes — the center row or column maps onto itself.

Blender's native brush is mirrored too — paint, erase, Ctrl invert, Shift smooth, any dab-based
brush type. Blender exposes no stroke hook, so Blix snapshots the image on mouse press and reflects
the pixels the stroke writes while it runs, which lands the mirrored half a frame behind the cursor.
The same watch clamps native strokes to an active selection by restoring the pixels outside it. The
bucket fill is not reflected — flooding one region and mirroring its pixels leaks fill across the
axis near the image edges; a selection still clamps the fill. The dither brush and Ctrl erase mirror
their stamps directly.

### Brush Helpers

Two native-brush extras, toggled in the add-on preferences:

- _Shift Line Mode_ (on by default) — hold Shift with the brush to preview a straight line from
  the last painted point; LMB paints it, Shift release or Esc ends the mode.
- _Ctrl+LMB Erases_ (off by default) — Ctrl+LMB erases the active layer with a round brush,
  compositing live, instead of painting the background color. It honors mirror axes and clips to
  an active selection.

### Layers

_Initialize Layers_ turns the current image into a canvas with a Background layer. Layers are
Image datablocks packed into the .blend; the canvas holds the numpy composite. Index 0 is the top
layer. Blend modes: Mix, Multiply, Screen, Overlay, Add, Subtract, Darken, Lighten, Difference.
Layers are tagged `L<number>` in creation order (the Background is `L000`); duplicates keep the
source number and add `.C<copy>`. An optional name follows a dash (`L000-Background`) and is
edited by double-clicking it in the list; the tag is fixed.

_Edit Active Layer_ switches the editor to the layer image for painting; _Show Composite_ switches
back and recomposites. Edits made straight on the composite canvas are synced into the active layer
before the next recomposite.

Three toggles under _Edit Active Layer_ help tell the active layer apart while painting on the
composite: _Dim Below_ darkens the layers below, _Hatch Below_ draws diagonal hatching over them,
and _Outline_ traces the active layer's painted pixels. All three follow the stroke in progress.
Colors are set in the add-on preferences (_Layer Dim_, _Layer Hatch_, _Layer Outline_).

### Dither

_Blix Dither Gradient_ drags a Bayer-thresholded ramp from the brush primary color — Ctrl snaps
the direction to 45°. _Blix Dither Brush_ paints a Bayer-masked stamp. With _Transparent_ on (the
default) the ramp fades to transparency, transparent pixels leave the canvas untouched, and a
Ctrl stroke of the brush erases with the pattern; with it off the secondary color takes
transparency's place. Both tools honor an active selection as their bounds. Pattern size (2/4/8),
brush size, density and the _Transparent_ toggle are in the Dither panel.

### Palette

_Import Hex Palette_ in the Palette panel loads a `.hex` file — one hex code per line, `#` prefix
optional — into a new palette named after the file and makes it the active image-paint palette.
Lines that are not a 6-digit code are skipped and counted in the report. Codes are read as sRGB and
decoded to scene linear, which is how Blender 5 stores brush and swatch colors, so a swatch paints
back the exact source code on an 8-bit image.

## Preferences

Edit > Preferences > Add-ons > Blix: guide, grid, ruler background, ruler text and mirror axis
colors, ruler band width, the _Ctrl+LMB Erases_ and _Shift Line Mode_ toggles, and every Blix key
binding (guide drag plus the tool keymaps) for rebinding.

## Limits

- Native brush mirroring reflects pixel values, not brush dabs: overlapping soft or low-strength
  dabs do not double-blend where the mirrored half meets the painted one, pixels the mirror wrote
  are not reflected back, and there is no mirrored brush cursor. The selection clamp on native
  strokes works the same way — pixels outside the mask flash painted for a frame before the watch
  restores them. The dither gradient is not mirrored; it already fills its whole target region.
- Layer images are packed as PNG, so layer storage is 8 bit per channel.
- Every pixel operation pushes one extra no-op image undo step; that bracket is what makes direct
  pixel writes revertible.
- Painting within 0.2 s of a layer property change can bake the stale composite into that stroke's
  undo state.

## License

GPL-3.0-or-later.
