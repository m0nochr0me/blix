# Blix

Pixel-art editing tools for the Blender image editor: pixel grid, rulers and guides, marquee
selection with nearest-neighbor transforms, a layer stack, and ordered Bayer dithering.

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

*Blix Select* tool. Marquee snaps to whole pixels. Moving or transforming lifts the pixels into a
floating buffer drawn with a nearest-neighbor GPU preview; the commit clears the source rect and
composites the buffer with straight-alpha over.

| Action | Input |
| --- | --- |
| Marquee | LMB drag |
| Move selection | LMB drag inside it, or `G` |
| Rotate freely | `R`, Ctrl snaps to 15° |
| Scale | `S` |
| Nudge 1 px | Arrow keys while moving |
| Confirm | LMB or Enter |
| Cancel | Esc or RMB |
| Clear selection | Esc |

Quarter turns and flips are panel buttons. With a layer stack present, selection edits target the
active layer.

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

## Preferences

Edit > Preferences > Add-ons > Blix: guide, grid, ruler background and ruler text colors, ruler
band width, and every Blix key binding (guide drag plus the tool keymaps) for rebinding.

## Limits

- Layer images are packed as PNG, so layer storage is 8 bit per channel.
- Every pixel operation pushes one extra no-op image undo step; that bracket is what makes direct
  pixel writes revertible.
- Painting within 0.2 s of a layer property change can bake the stale composite into that stroke's
  undo state.

## License

GPL-3.0-or-later.
