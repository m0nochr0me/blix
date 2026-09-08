# Blix

Pixel-art editing tools for the Blender image editor: pixel grid, rulers and guides, box,
ellipse, lasso, brush and wand selection with boolean modes, RotSprite transforms and a
clipboard, shape drawing, mirror symmetry, brush line mode and erase, a layer stack with
painting aids, editable text layers, keyframed cel animation with onion skin, sprite stacking
with mesh slicing and voxel mesh export, ordered and error-diffusion dithering and `.hex`
palette import.

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
pixels, origin top-left. Guides are vertical, horizontal or diagonal. A diagonal runs through an
anchor pixel (X, Y, which may lie outside the image) at an angle counter-clockwise from the X axis,
so several diagonals sharing one anchor fan out from a vanishing point. Guides are stored per image
and survive save/reload.

| Action          | Input                                              |
| --------------- | -------------------------------------------------- |
| Create guide    | LMB drag out of a ruler band                       |
| Create diagonal | LMB drag out of the corner where the bands meet    |
| Move guide      | Ctrl+LMB within 6 px of a guide (moves the anchor) |
| Delete guide    | Drag it so its line no longer crosses the image    |
| Cancel drag     | Esc or RMB                                         |

The `+` menu adds a guide of any kind at the image center; the list edits positions and the
diagonal angle numerically; remove and clear are buttons. The drag binding sits in the
`Screen Editing` keymap so it wins over the active tool. A press that is neither in a band nor
near a guide passes through to the tool.

### Selection & Transform

One toolbar group holds five tools — _Blix Select Box_, _Ellipse_, _Lasso_, _Brush_ and _Wand_.
A selection is a per-pixel mask, so it can be any shape. Marquees snap to whole pixels. The lasso
drags a freehand outline, the brush paints the mask with a round brush (diameter in the Selection
panel), and the wand click-selects the contiguous region of one color. Moving or transforming
lifts the masked pixels into a floating buffer drawn with a nearest-neighbor GPU preview; the
commit clears the masked source and composites the buffer with straight-alpha over. Only the
pixels lifted first move: a second move or transform carries that same buffer and restores what
lay beneath it, instead of re-lifting whatever the selection now covers. Painting inside the
moved footprint drops the carry, so the next lift reads the canvas again. Marching ants trace
the mask itself, holes included. With a box or ellipse tool active, the selection bounds show
eight handles: dragging one scales, dragging just outside a corner rotates, dragging inside
moves. Scale and free rotation resample with RotSprite (three EPX passes, then nearest), so
edges stay crisp and no new colors appear.

| Action                  | Input                      |
| ----------------------- | -------------------------- |
| Marquee / lasso / brush | LMB drag                   |
| Wand select             | LMB click                  |
| Add to selection        | Shift+LMB                  |
| Subtract from selection | Ctrl+LMB                   |
| Move selection          | LMB drag inside it, or `G` |
| Lock move to an axis    | Ctrl while moving          |
| Duplicate and move      | Shift+`D`                  |
| Rotate freely           | `R`, Ctrl snaps to 15°     |
| Scale                   | `S`, or drag a handle      |
| Keep aspect ratio       | Shift while scaling        |
| Nudge 1 px              | Arrow keys while moving    |
| Confirm                 | LMB or Enter               |
| Cancel                  | Esc or RMB                 |
| Copy                    | Ctrl+`C`                   |
| Paste                   | Ctrl+`V`                   |
| Delete pixels           | `X` or Del                 |
| Invert selection        | Ctrl+`I`                   |
| Clear selection         | Esc                        |

The _Mode_ row in the Selection panel sets the default for a plain LMB gesture; Shift and Ctrl
override it for that gesture. Ctrl+LMB within 6 px of a guide still grabs the guide instead of
subtracting. Quarter turns and flips are panel buttons, and Copy, Delete and Invert exist as
buttons too; inverting with no selection selects the whole image.
Copy puts the pixels on the system clipboard as well, and _Copy Flattened_ puts the whole
composite there. Paste takes the clipboard image — copied in Blix or in any other app — lands it
on a new layer above the active one and starts a move: click or Enter confirms, Esc leaves it
where it landed. A Blix copy pastes at its source position, an outside image is centered, and an
image without layers gets a layer stack first. With a layer stack present, selection edits target
the active layer.

### Shapes

One toolbar group holds four tools — _Blix Rectangle_, _Ellipse_, _Hexagon_ and _Line_ — hold the
button to switch. Drag to draw; the GPU preview shows the exact pixels before the commit. _Filled_
lives in the Shapes panel; _Pointy Top_ appears there while the hexagon tool is active. Outlines
are 1 px. An active selection clips the result, and enabled mirror axes reflect it. Ctrl erases
the shape from the active layer instead of painting it; the preview shows the pixels it will
clear as a grey haze. On a layered canvas the result is composited right away, so a shape drawn
on a lower layer shows under the layers above it.

| Action           | Input                                                |
| ---------------- | ---------------------------------------------------- |
| Draw             | LMB drag, corner to corner                           |
| Constrain        | Shift — square/circle/regular hexagon, 45° for lines |
| Draw from center | Alt                                                  |
| Erase            | Ctrl                                                 |
| Cancel           | Esc or RMB                                           |
| Palette popup    | RMB, with _RMB Opens Palette_ on                     |

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
before the next recomposite; _Update Composite_ forces one. A locked layer takes no edits:
selection transforms and Ctrl erase are refused, remove and merge down are disabled, and
strokes painted on the composite over it are dropped at the next recomposite. `H` hides every
layer above the active one, or shows them all when none is visible.

Three toggles under _Edit Active Layer_ help tell the active layer apart while painting on the
composite: _Dim Below_ darkens the layers below, _Hatch Below_ draws diagonal hatching over them,
and _Outline_ traces the active layer's painted pixels. All three follow the stroke in progress.
Colors are set in the add-on preferences (_Layer Dim_, _Layer Hatch_, _Layer Outline_).

### Text

The _Blix Text_ tool adds text layers. Click on the canvas: a dialog takes the text and its size
in pixels, the click point becomes the left end of the baseline, and a new layer named after the
text lands above the active one, colored with the brush primary color. An image without layers
gets a layer stack first. The layer stays live: the _Text_ panel edits the text, font, size, color
and origin, and every edit rerenders the layer. The font is a Blender font datablock, so the folder
button loads any TTF, OTF or WOFF2 file and text objects can share it; with none set, the bundled
DejaVu Sans Mono renders. Glyphs render monochrome, so a pixel font at its native size comes out
exact and no anti-aliasing colors appear. Text is a single line.

A text layer takes no paint, erase or selection edits until _Rasterize Text_ turns it into a
plain layer, and a layer cannot be merged down into it. The active text layer is outlined while
the tool is active. Add, move and rasterize are one undo step each; a panel edit costs one step
plus the usual no-op image step.

| Action               | Input                                    |
| -------------------- | ---------------------------------------- |
| Add text layer       | LMB click on the canvas                  |
| Move text layer      | LMB drag on the active text layer, `G`   |
| Lock move to an axis | Ctrl while moving                        |
| Nudge 1 px           | Arrow keys while moving                  |
| Confirm              | LMB or Enter                             |
| Cancel               | Esc or RMB                               |

### References

The `+` in the _References_ panel loads an image or movie file as a reference layer of the
current image: drawn at its own resolution over or under the painting, never composited or
painted on, and linked by path rather than packed. A reference larger than the canvas is scaled
to fit, a smaller one shows at 1:1. References belong to the canvas, so they stay while a layer
is edited, and they work on plain images without Blix layers.

Each reference has _Opacity_, _Behind_ (draw under the painting: opaque pixels cover it,
transparent ones reveal it, and _Dim Below_ dims it together with the lower layers), _Offset_
(center in canvas pixels from the top-left corner), _Scale_ (canvas pixels per reference pixel),
_Rotation_ and _Flip X_ / _Flip Y_; the list toggles visibility and behind\front. The _Blix
Reference_ tool transforms the active reference: drag to move (Ctrl locks to an axis, arrows
nudge one pixel), `G` / `R` / `S` start a move, rotate or scale that a click or Enter confirms
and Esc cancels, Ctrl snaps the rotation to 15°. The active reference is outlined while the tool
is active. Every confirmed transform is one undo step.

A movie reference shows one frame: _Frame_ picks it, or _Follow Timeline_ shows the movie frame
at the scene frame plus _Frame Offset_, so scrubbing cels scrubs the reference for rotoscoping.

### Animation

_Animate Layers_ in the Animation panel turns the layers of the current canvas into cels for the
current scene. Each layer gets a visibility track on the Scene, keyed with ordinary Blender
keyframes: the dope sheet lists them as `<canvas>/L<number> (Visible)`, hold bars show each cel's
range, and dragging keys retimes it. Image datablocks cannot carry animation data, which is why
the tracks live on the Scene; a canvas animates independently in every scene that enables it.

A filled diamond in the layer list marks a layer as a cel; untick it for static layers such as
the background. _Layout Cels_ gives every cel a consecutive range (start frame, frames per cel,
bottom layer first unless _Top Layer First_) and fits the scene frame range to them. _Insert Cel_
adds a new layer after the active cel with the same length, shifts the later cels, and jumps to
it. The _Start_ and _End_ fields edit the active layer's range at the current frame (the next
range when the layer is off there), so one cel can show up in several ranges; keys in the dope
sheet are the same data. _Visible_ is the keyed value at the current frame, so `I`, right-click
_Insert Keyframe_ and auto-keying all work on it. The transport row jumps to the scene start
or end, steps to the previous or next cel change, and plays the scene. With _Follow Frame_ on,
scrubbing makes the cel shown at that frame the active layer, so painting always lands on the
visible cel. The eye toggle and `H` still gate a layer on top of its track.

A layer can also hold several cels of its own, so one layer shows different pixels per frame.
_Add Cel_ starts a new blank cel for the active layer at the current frame (the layer's current
image becomes cel `F000`); _Duplicate_ starts one from a copy of the shown cel; _Delete_ drops
the cel shown at this frame. The _Cel_ field is the keyed cel index, stepped so no in-between cel
ever shows, and the layer list tags a layer with its shown cel (`L001 F002`). _Insert Frame_ and
_Remove Frame_ retime every key of the canvas after the current frame; cels that start inside
removed frames are dropped. The dope sheet groups a layer's _Visible_ and _Cel_ channels under
one name.

_Onion Skin_ ghosts the cels before and after the current one over the canvas, tinted (previous
red, next green by default; both colors live in the add-on preferences) and fading with
distance. _Before_ and _After_ count cel changes rather than frames, so a held cel is one step.
Ghosts are overlay only; export and the stack preview ignore them.

_Export Animation_ writes the composite of every frame in the scene range, either as a horizontal
strip (first frame left) or as a numbered PNG sequence. _Stop Animating_ removes the tracks and
their keys.

### Sprite Stacking

The Sprite Stacking panel appears once the image has layers. _Preview_ draws the visible layers
beside the canvas as a stack of slices, bottom layer lowest, in the chosen _Projection_ —
Isometric (35.26° elevation), Dimetric (30°, the 2:1 pixel-art view) or Trimetric (20° with a
30° yaw offset) — rotated by _Angle_. _Height_ on the active layer repeats its slice that many
units; hold Alt while editing the field to set every layer of the canvas to that height. Layers
hidden by the eye toggle or by their cel track are left out, as are layers whose size differs
from the canvas.

_Pixelate_ rasterizes the preview at _Resolution_ texels per canvas pixel (1 matches the canvas)
and blits it with nearest sampling, so the stack previews as pixel art. _Scale_ shows the stack
at x1 to x16; _Scale Layers_ upscales each slice but keeps one pixel per height unit between
them, off scales the whole stack.

_Noise_ adds uniform brightness noise to every slice so repeated layers stop reading as flat
bands; each slice gets its own pattern, hues stay put and transparent pixels are left alone.
_Grain_ sets the noise cell size in canvas pixels. Zero noise turns the effect off.

_Cavity_ brightens ridges and darkens valleys of the stack so same-coloured steps, grooves and
edges stay readable, much like the viewport cavity shading. Every opaque pixel counts its filled
neighbours in the slices below, around and above it: convex edges and corners get lighter, creases
and pits darker, flat faces stay put. The shading is view independent, so the strip export carries
it at any angle. Zero turns it off.

_Light_ lights the stack from above: pixels with nothing over them in the next slice up are tops
and get lighter, covered pixels form the sides and get darker by the same amount, so walls and
floors of one colour separate. It is view independent too and stacks with cavity. Zero turns it
off.

_Export Sprite Stack_ writes a horizontal PNG strip of the slices, bottom slice first, each layer
repeated by its height; _Apply Scale_ (on by default) upscales the slices by the stack scale
with nearest sampling, _Apply Noise_ (on by default) bakes the same noise pattern the preview
shows into the slices, _Apply Cavity_ and _Apply Light_ (on by default) bake the two shadings.

### Voxel Mesh

The Voxel Mesh panel bridges the layer stack and scene meshes. _Slice Mesh_ voxelizes the
_Mesh_ object at _Resolution_ pixels along its longest XY side with cubic voxels and writes one
layer per slice, bottom slice lowest, into a new square canvas named after the object. Each
face's colour comes from its material — the Principled _Base Color_ when it is not textured,
else the viewport colour — snapped to the perceptually nearest swatch of the active palette
(CIELAB distance, so a teal lands on a teal rather than a green of similar brightness); with no
palette the material colours are kept. _Paint Depth_ sets how many pixels inward from the
surface take the face colour — raise it to 2 or more so the band that shows between stacked
slices keeps the side colour; deeper pixels inherit the colour of the pixel above them.
Modifiers are applied and the object transform is baked, so the canvas shows the mesh as it
stands in the scene. Only closed surfaces fill; overlapping and touching parts are fine, open
meshes leave gaps.

_Stack Projections_ builds the stack from drawn views instead of a mesh: pick the _Front_,
_Right_ and _Top_ images — any images, layer images of the current canvas included — oriented
like Blender's views (front on the left of the right view, front at the bottom of the top view).
Each view is cropped to its opaque pixels, so their widths and heights must agree: the front and
top share a width, the front and right a height, the right and top a depth. The shape is the
visual hull, the volume every view's silhouette allows, so a notch has to be drawn in every view
that sees it. Each view paints the pixels it sees, _Paint Depth_ deep, top winning over front and
back over left and right; pixels no view reaches take the top view's colour. _Back_ and _Left_ are
optional and mirror the front and right views when empty. The new canvas is the size of the top
view image with the slices placed where its drawing sits.

_Build Mesh_ turns the visible stack, heights included and blend modes ignored like the export,
into a mesh object with one quad per exposed voxel face and nothing inside; coplanar faces of one
colour merge into single n-gons. Every face is UV-mapped to a texel of a palette texture — the
active palette when one is loaded, else the colours found in the stack — wired into a Principled
material with nearest sampling, so the mesh shades as pixel art in the viewport and renders.
_Voxel Size_ sets the edge of one canvas pixel in scene units; the object sits centred on X and Y
with its base at Z 0.

_Import Sprite Stack_ loads a horizontal strip PNG, as written by _Export Sprite Stack_, into a
new canvas with one layer per slice; _Slice Width_ (0 uses the strip height) splits the strip.

### Dither

_Blix Dither Gradient_ drags a dithered ramp from the brush primary color — Ctrl snaps the
direction to 45°. _Blix Dither Brush_ paints a pattern-masked stamp. With _Transparent_ on (the
default) the ramp fades to transparency, transparent pixels leave the canvas untouched, and a
Ctrl stroke of the brush erases with the pattern; with it off the secondary color takes
transparency's place. Both tools honor an active selection as their bounds.

The Dither panel picks the _Pattern_: Bayer 2x2/4x4/8x8, Blue Noise (a 64x64 void-and-cluster
tile), horizontal, vertical and diagonal lines, halftone dots on a square or offset grid, or
_Custom_ — any image up to 256x256 used as a tile, darker pixels painting first, so a black and
white tile reproduces exactly at a density equal to its black fraction. Patterns are anchored to
image coordinates, so strokes and fills line up. _Gradient_ switches the gradient tool from the
ordered pattern to Floyd-Steinberg or Atkinson error diffusion (the brush always uses the
pattern). Brush size, density and the _Transparent_ toggle sit in the same panel.

### Palette

_Import Hex Palette_ in the Palette panel loads a `.hex` file — one hex code per line, `#` prefix
optional — into a new palette named after the file and makes it the active image-paint palette.
Lines that are not a 6-digit code are skipped and counted in the report. Codes are read as sRGB and
decoded to scene linear, which is how Blender 5 stores brush and swatch colors, so a swatch paints
back the exact source code on an 8-bit image.

_RMB Opens Palette_ (off by default, in the add-on preferences) makes the right mouse button in
paint mode pop up the palette under the cursor — palette selector and swatches — instead of
Blender's color wheel. The stock RMB clone-grab and stencil-control bindings are shadowed while it
is on. The shape and dither tools open the same popup on RMB.

## Preferences

Edit > Preferences > Add-ons > Blix: guide, grid, ruler background, ruler text, mirror axis,
layer dim, hatch and outline, and onion previous and next colors, ruler band width, the _Ctrl+LMB
Erases_, _Shift Line Mode_ and _RMB Opens Palette_ toggles, and every Blix key binding (guide drag,
mirror watch, stroke sync, `H`, erase, line mode and palette popup) for rebinding. Tool keymaps live under the tool in
Preferences > Keymap > Image > Image Paint.

## Limits

- Native brush mirroring reflects pixel values, not brush dabs: overlapping soft or low-strength
  dabs do not double-blend where the mirrored half meets the painted one, pixels the mirror wrote
  are not reflected back, and there is no mirrored brush cursor. The selection clamp on native
  strokes works the same way — pixels outside the mask flash painted for a frame before the watch
  restores them. The dither gradient is not mirrored; it already fills its whole target region.
- Layer images are packed as PNG, so layer storage is 8 bit per channel.
- Text layers hold one line, render without anti-aliasing and reload their font by path; a
  font packed into the .blend renders from a temporary copy.
- Scrubbing recomposites the canvas without an undo step, and a stroke painted at a frame
  records the cel state first, so undo reverts strokes, not scrubs.
- Layers with cels cannot be merged down; _Flatten_ bakes the current frame only.
- The sprite stack preview and export take the layers as they show at the current frame; a
  layer larger or smaller than the canvas is skipped.
- Every pixel operation pushes one extra no-op image undo step; that bracket is what makes direct
  pixel writes revertible. Layer operations are single undo steps; a layer property edited in the
  panel costs one extra no-op step.
- Slicing reads material colours only, so a textured mesh slices in its viewport colour; UV
  texture sampling is not implemented.
- References sample nearest-neighbour, so a photo or video minified onto few canvas pixels
  aliases. Reference files stay linked by path, never packed; each import makes its own image
  datablock, so two frames of one movie need two imports. Semi-transparent paint over a _Behind_
  reference blends slightly more opaque, since the painting is redrawn over the reference.

## License

GPL-3.0-or-later.
