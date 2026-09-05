"""Layer stack on a canvas Image with numpy compositing. Index 0 is the top layer."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np
from bpy.app.handlers import persistent

from . import cels, overlay, paint, props, select, undo

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

BLEND_ITEMS = (
    ("MIX", "Mix", ""),
    ("MULTIPLY", "Multiply", ""),
    ("SCREEN", "Screen", ""),
    ("OVERLAY", "Overlay", ""),
    ("ADD", "Add", ""),
    ("SUBTRACT", "Subtract", ""),
    ("DARKEN", "Darken", ""),
    ("LIGHTEN", "Lighten", ""),
    ("DIFFERENCE", "Difference", ""),
)


def _blend_rgb(mode: str, dest: np.ndarray, src: np.ndarray) -> np.ndarray:
    match mode:
        case "MULTIPLY":
            return dest * src
        case "SCREEN":
            return 1.0 - (1.0 - dest) * (1.0 - src)
        case "OVERLAY":
            return np.where(dest <= 0.5, 2.0 * dest * src, 1.0 - 2.0 * (1.0 - dest) * (1.0 - src))
        case "ADD":
            return np.clip(dest + src, 0.0, 1.0)
        case "SUBTRACT":
            return np.clip(dest - src, 0.0, 1.0)
        case "DARKEN":
            return np.minimum(dest, src)
        case "LIGHTEN":
            return np.maximum(dest, src)
        case "DIFFERENCE":
            return np.abs(dest - src)
        case _:
            return src


def blend_over(dest: np.ndarray, src: np.ndarray, mode: str, opacity: float) -> np.ndarray:
    src_a = src[:, :, 3:4] * opacity
    dst_a = dest[:, :, 3:4]
    blended = _blend_rgb(mode, dest[:, :, :3], src[:, :, :3])
    src_rgb = (1.0 - dst_a) * src[:, :, :3] + dst_a * blended
    out_a = src_a + dst_a * (1.0 - src_a)
    safe_a = np.where(out_a == 0.0, 1.0, out_a)
    out_rgb = (src_a * src_rgb + dst_a * dest[:, :, :3] * (1.0 - src_a)) / safe_a
    return np.concatenate([out_rgb, out_a], axis=2).astype(np.float32)


def composite_pixels(canvas: bpy.types.Image) -> np.ndarray:
    width, height = canvas.size
    dest = np.zeros((height, width, 4), dtype=np.float32)
    for layer in reversed(list(props.layers(canvas))):
        image = layer.image
        if not shown(canvas, layer) or layer.opacity == 0.0 or image is None:
            continue
        if tuple(image.size) != (width, height):
            continue
        dest = blend_over(dest, select.read_pixels(image), layer.blend, layer.opacity)
    return dest


def shown(canvas: bpy.types.Image, layer: Any) -> bool:
    """Effective visibility: the eye toggle gated by the layer's cel track at this frame."""
    return bool(layer.visible) and cels.on(canvas, layer)


_composite_cache: dict[str, np.ndarray] = {}
_sync_targets: dict[str, int] = {}

_HISTORY_CAP = 32
_HistoryEntry = tuple[np.ndarray, dict[str, np.ndarray]]
_history: dict[str, list[_HistoryEntry]] = {}
_cursor: dict[str, int] = {}


def _same_layers(a: dict[str, np.ndarray], b: dict[str, np.ndarray]) -> bool:
    return a.keys() == b.keys() and all(np.array_equal(a[name], b[name]) for name in a)


def push_history(canvas: bpy.types.Image) -> None:
    """Snapshot canvas and layer pixels after the cursor entry so undo can walk layer states."""
    if len(props.layers(canvas)) == 0:
        return
    layer_map = {
        layer.image.name: select.read_pixels(layer.image)
        for layer in props.layers(canvas)
        if layer.image is not None
    }
    entries = _history.setdefault(canvas.name, [])
    pixels = select.read_pixels(canvas)
    cursor = _cursor.get(canvas.name, len(entries) - 1)
    if 0 <= cursor < len(entries):
        saved, saved_layers = entries[cursor]
        if np.array_equal(saved, pixels) and _same_layers(saved_layers, layer_map):
            return
        del entries[cursor + 1 :]
    entries.append((pixels, layer_map))
    del entries[:-_HISTORY_CAP]
    _cursor[canvas.name] = len(entries) - 1


def _match_index(
    entries: list[_HistoryEntry], current: np.ndarray, cursor: int, step: int
) -> int | None:
    """Entry equal to current, nearest along step; an unchanged canvas moves one equal neighbor."""

    def equal(index: int) -> bool:
        pixels = entries[index][0]
        return pixels.shape == current.shape and np.array_equal(pixels, current)

    count = len(entries)
    if equal(cursor):
        neighbor = cursor + step
        return neighbor if 0 <= neighbor < count and equal(neighbor) else cursor
    ahead = range(cursor + step, count if step > 0 else -1, step)
    behind = range(cursor - step, -1 if step > 0 else count, -step)
    for index in (*ahead, *behind):
        if equal(index):
            return index
    return None


def _apply_history(canvas: bpy.types.Image, current: np.ndarray, step: int) -> bool:
    entries = _history.get(canvas.name)
    if not entries:
        return False
    cursor = min(max(_cursor.get(canvas.name, 0), 0), len(entries) - 1)
    index = _match_index(entries, current, cursor, step)
    if index is None:
        return False
    layer_map = entries[index][1]
    for layer in props.layers(canvas):
        image = layer.image
        if image is None:
            continue
        saved = layer_map.get(image.name)
        if saved is None or tuple(image.size) != (saved.shape[1], saved.shape[0]):
            continue
        if not np.array_equal(select.read_pixels(image), saved):
            select.write_pixels(image, saved)
    _cursor[canvas.name] = index
    _composite_cache[canvas.name] = current
    return True


def layer_images(canvas: bpy.types.Image) -> Iterator[bpy.types.Image]:
    """Every image a layer of the canvas owns: its current image and all of its cel slots."""
    for layer in props.layers(canvas):
        if layer.image is not None:
            yield layer.image
        for slot in layer.cels:
            if slot.image is not None:
                yield slot.image


def _pack_layers(canvas: bpy.types.Image) -> None:
    """Refresh packed payloads so memfile undo restores current layer pixels, not stale ones."""
    for image in layer_images(canvas):
        if image.is_dirty:
            image.pack()


def _repair_stale_layers(canvas: bpy.types.Image) -> bool:
    """Restore layers reloaded from stale packed files with the newest history snapshot."""
    entries = _history.get(canvas.name)
    if not entries:
        return False
    repaired = False
    for layer in props.layers(canvas):
        image = layer.image
        if image is None or image.is_dirty:
            continue
        for _pixels, layer_map in reversed(entries):
            saved = layer_map.get(image.name)
            if saved is None:
                continue
            if tuple(image.size) == (saved.shape[1], saved.shape[0]) and not np.array_equal(
                select.read_pixels(image), saved
            ):
                select.write_pixels(image, saved)
                repaired = True
            break
    return repaired


def _sync_target(canvas: bpy.types.Image) -> Any:
    stack = props.layers(canvas)
    index = _sync_targets.get(canvas.name, props.layers_index(canvas))
    if 0 <= index < len(stack):
        return stack[index]
    return active_layer(canvas)


def _changed_mask(
    current: np.ndarray, cached: np.ndarray, written: np.ndarray | None = None
) -> np.ndarray:
    """Pixels the canvas changed since the cache, plus swapped stand-in and written pixels."""
    mask = np.any(current != cached, axis=2)
    if _stand_in is not None and _stand_in.mask is not None and _stand_in.mask.shape == mask.shape:
        mask |= _stand_in.mask
    if written is not None and written.shape == mask.shape:
        mask |= written
    return mask


def sync_canvas(canvas: bpy.types.Image, written: np.ndarray | None = None) -> None:
    """Attribute canvas changes to the active layer; written marks writes the diff cannot see."""
    target = _sync_target(canvas)
    _sync_targets[canvas.name] = props.layers_index(canvas)
    current = select.read_pixels(canvas)
    cached = _composite_cache.get(canvas.name)
    if cached is None or cached.shape != current.shape:
        _composite_cache[canvas.name] = current
        return
    mask = _changed_mask(current, cached, written)
    if not mask.any():
        return
    if target is None or target.image is None or locked(target):
        return
    if tuple(target.image.size) != (current.shape[1], current.shape[0]):
        return
    pixels = select.read_pixels(target.image)
    erased = mask & (current[:, :, 3] < cached[:, :, 3])
    painted = mask & ~erased
    pixels[painted] = current[painted]
    ratio = current[:, :, 3][erased] / np.maximum(cached[:, :, 3][erased], 1e-6)
    pixels[erased, 3] *= ratio
    select.write_pixels(target.image, pixels)
    cached[mask] = current[mask]


def composite(canvas: bpy.types.Image) -> None:
    """Recomposite canvas; skip the undo record when output matches canvas pixels."""
    pixels = composite_pixels(canvas)
    if (pixels != select.read_pixels(canvas)).any():
        select.write_pixels(canvas, pixels)
        _composite_cache[canvas.name] = select.read_pixels(canvas)
        undo.defer(canvas)
        return
    _composite_cache[canvas.name] = pixels
    push_history(canvas)


def _heal(canvas: bpy.types.Image) -> bool:
    """Recomposite after undo moved layer state the canvas no longer matches; pushes no step."""
    pixels = composite_pixels(canvas)
    current = select.read_pixels(canvas)
    if np.array_equal(np.rint(pixels * 255.0), np.rint(current * 255.0)):
        return False
    select.write_pixels(canvas, pixels)
    _composite_cache[canvas.name] = select.read_pixels(canvas)
    undo.mark(canvas)
    return True


def _layer_changed(self: bpy.types.PropertyGroup, context: bpy.types.Context) -> None:
    canvas = cast(bpy.types.Image, self.id_data)
    if len(props.layers(canvas)):
        sync_canvas(canvas)
        composite(canvas)
        overlay.tag_redraw(context)


def _stack_changed(self: bpy.types.PropertyGroup, context: bpy.types.Context) -> None:
    overlay.tag_redraw(context)


def _text_changed(self: bpy.types.PropertyGroup, context: bpy.types.Context) -> None:
    from . import text

    layer = cast(Any, self)
    if layer.is_text:
        text.refresh(cast(bpy.types.Image, self.id_data), layer)


def _active_changed(self: bpy.types.Image, context: bpy.types.Context) -> None:
    if len(props.layers(self)):
        sync_canvas(self)
    space = context.space_data
    if space is None or space.type != "IMAGE_EDITOR":
        return
    editor = cast(bpy.types.SpaceImageEditor, space)
    shown = editor.image
    if shown is None or props.canvas_of(shown) != self:
        return
    layer = active_layer(self)
    if layer is not None and layer.image is not None:
        editor.image = layer.image


class BlixCelSlot(bpy.types.PropertyGroup):
    number: bpy.props.IntProperty(name="Number", min=0)
    image: bpy.props.PointerProperty(type=bpy.types.Image)


class BlixLayer(bpy.types.PropertyGroup):
    number: bpy.props.IntProperty(name="Number", min=0)
    copy: bpy.props.IntProperty(name="Copy", min=0)
    label: bpy.props.StringProperty(name="Name")
    image: bpy.props.PointerProperty(type=bpy.types.Image)
    opacity: bpy.props.FloatProperty(
        name="Opacity", default=1.0, min=0.0, max=1.0, update=_layer_changed
    )
    blend: bpy.props.EnumProperty(
        name="Blend", items=BLEND_ITEMS, default="MIX", update=_layer_changed
    )
    visible: bpy.props.BoolProperty(name="Visible", default=True, update=_layer_changed)
    lock: bpy.props.BoolProperty(name="Lock", default=False)
    height: bpy.props.IntProperty(
        name="Height", default=1, min=1, soft_max=16, update=_stack_changed
    )
    cel: bpy.props.BoolProperty(
        name="Cel", description="Treat this layer as an animation frame", default=True
    )
    cels: bpy.props.CollectionProperty(type=BlixCelSlot)
    is_text: bpy.props.BoolProperty(name="Text Layer", default=False)
    text: bpy.props.StringProperty(name="Text", update=_text_changed)
    font: bpy.props.PointerProperty(type=bpy.types.VectorFont, name="Font", update=_text_changed)
    text_size: bpy.props.IntProperty(
        name="Size",
        description="Font size in canvas pixels",
        default=16,
        min=1,
        soft_max=256,
        update=_text_changed,
    )
    text_color: bpy.props.FloatVectorProperty(
        name="Color",
        subtype="COLOR_GAMMA",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
        update=_text_changed,
    )
    text_origin: bpy.props.IntVectorProperty(
        name="Origin",
        description="Baseline start in canvas pixels from the top-left corner",
        size=2,
        subtype="XYZ",
        update=_text_changed,
    )


def locked(layer: Any) -> bool:
    """Layers that take no pixel edits: locked ones, and text layers until rasterized."""
    return bool(layer.lock or layer.is_text)


def layer_tag(layer: Any) -> str:
    """Metadata part of a layer name: L<number>, plus .C<copy> for duplicates."""
    tag = f"L{layer.number:03d}"
    return f"{tag}.C{layer.copy:03d}" if layer.copy else tag


def layer_name(layer: Any) -> str:
    """Full layer name: tag, then -<label> when the layer has one."""
    tag = layer_tag(layer)
    return f"{tag}-{layer.label}" if layer.label else tag


def slot_tag(slot: Any) -> str:
    return f"F{slot.number:03d}"


def current_slot(layer: Any) -> Any:
    """Cel slot whose image the layer shows now; None for a layer without cels."""
    return next((slot for slot in layer.cels if slot.image == layer.image), None)


def next_slot_number(layer: Any) -> int:
    return max((slot.number for slot in layer.cels), default=-1) + 1


def add_slot(canvas: bpy.types.Image, layer: Any, pixels: np.ndarray | None) -> Any:
    """Append a cel slot, blank or from pixels; the first call adopts the layer image as slot 0."""
    if len(layer.cels) == 0:
        first = layer.cels.add()
        first.image = layer.image
    slot = layer.cels.add()
    slot.number = next_slot_number(layer)
    name = f"{layer_tag(layer)}.{slot_tag(slot)}"
    if pixels is None:
        slot.image = _new_layer_image(canvas, name)
    else:
        slot.image = _clone_image(canvas, name, pixels)
    return slot


def active_layer(canvas: bpy.types.Image) -> Any:
    stack = props.layers(canvas)
    index = props.layers_index(canvas)
    if 0 <= index < len(stack):
        return stack[index]
    return None


def resolve_canvas(context: bpy.types.Context) -> bpy.types.Image | None:
    image = select.edit_image(context)
    if image is None:
        return None
    if len(props.layers(image)):
        return image
    return props.canvas_of(image)


def _clone_image(canvas: bpy.types.Image, name: str, pixels: np.ndarray) -> bpy.types.Image:
    width, height = canvas.size
    image = bpy.data.images.new(f"{canvas.name}.{name}", width, height, alpha=True)
    props.set_canvas_of(image, canvas)
    select.write_pixels(image, pixels)
    image.pack()
    return image


def _new_layer_image(canvas: bpy.types.Image, name: str) -> bpy.types.Image:
    width, height = canvas.size
    return _clone_image(canvas, name, np.zeros((height, width, 4), dtype=np.float32))


def init_layers(canvas: bpy.types.Image) -> None:
    pixels = select.read_pixels(canvas)
    layer = props.layers(canvas).add()
    layer.label = "Background"
    layer.image = _clone_image(canvas, layer_name(layer), pixels)
    props.set_layers_index(canvas, 0)
    _composite_cache[canvas.name] = pixels
    _sync_targets[canvas.name] = 0
    push_history(canvas)


def next_layer_number(canvas: bpy.types.Image) -> int:
    return max((layer.number for layer in props.layers(canvas)), default=-1) + 1


def next_copy_number(canvas: bpy.types.Image, number: int) -> int:
    copies = (layer.copy for layer in props.layers(canvas) if layer.number == number)
    return max(copies, default=0) + 1


def add_layer(canvas: bpy.types.Image, number: int) -> None:
    sync_canvas(canvas)
    stack = props.layers(canvas)
    layer = stack.add()
    layer.number = number
    layer.image = _new_layer_image(canvas, layer_name(layer))
    index = max(props.layers_index(canvas), 0)
    stack.move(len(stack) - 1, index)
    props.set_layers_index(canvas, index)


def remove_layer(canvas: bpy.types.Image, index: int) -> None:
    sync_canvas(canvas)
    stack = props.layers(canvas)
    _composite_cache.pop(canvas.name, None)
    stack.remove(index)
    props.set_layers_index(canvas, min(index, len(stack) - 1))
    composite(canvas)


def duplicate_layer(canvas: bpy.types.Image, index: int) -> None:
    sync_canvas(canvas)
    stack = props.layers(canvas)
    source = stack[index]
    layer = stack.add()
    layer.number = source.number
    layer.copy = next_copy_number(canvas, source.number)
    layer.label = source.label
    layer.opacity = source.opacity
    layer.blend = source.blend
    layer.visible = source.visible
    layer.height = source.height
    layer.cel = source.cel
    layer.image = _clone_image(canvas, layer_name(layer), select.read_pixels(source.image))
    for slot in source.cels:
        clone = layer.cels.add()
        clone.number = slot.number
        if slot.image == source.image:
            clone.image = layer.image
            continue
        name = f"{layer_tag(layer)}.{slot_tag(slot)}"
        clone.image = _clone_image(canvas, name, select.read_pixels(slot.image))
    layer.text = source.text
    layer.font = source.font
    layer.text_size = source.text_size
    layer.text_color = source.text_color
    layer.text_origin = source.text_origin
    layer.is_text = source.is_text
    stack.move(len(stack) - 1, index)
    props.set_layers_index(canvas, index)
    composite(canvas)


def merge_down(canvas: bpy.types.Image, index: int) -> None:
    sync_canvas(canvas)
    stack = props.layers(canvas)
    above = stack[index]
    below = stack[index + 1]
    merged = blend_over(
        select.read_pixels(below.image), select.read_pixels(above.image), above.blend, above.opacity
    )
    below.image = _clone_image(canvas, layer_name(below), merged)
    stack.remove(index)
    props.set_layers_index(canvas, index)
    composite(canvas)


def flatten(canvas: bpy.types.Image) -> None:
    sync_canvas(canvas)
    pixels = composite_pixels(canvas)
    select.write_pixels(canvas, pixels)
    _composite_cache[canvas.name] = select.read_pixels(canvas)
    stack = props.layers(canvas)
    for image in layer_images(canvas):
        props.set_canvas_of(image, None)
    stack.clear()
    layer = stack.add()
    layer.label = "Background"
    layer.image = _new_layer_image(canvas, layer_name(layer))
    select.write_pixels(layer.image, pixels)
    layer.image.pack()
    props.set_layers_index(canvas, 0)


POLL = 0.05
_stroke_canvas: str | None = None
_stand_in: paint.StandIn | None = None
_fill_mask: np.ndarray | None = None


def layers_above(canvas: bpy.types.Image) -> list[Any]:
    return list(props.layers(canvas))[: props.layers_index(canvas)]


def toggle_above(canvas: bpy.types.Image) -> None:
    """Hide every layer above the active one, or show them all when none is visible."""
    above = layers_above(canvas)
    shown = not any(layer.visible for layer in above)
    for layer in above:
        layer.visible = shown


def _stroke_tick() -> float | None:
    global _stroke_canvas, _stand_in, _fill_mask
    from . import mirror

    if _stroke_canvas is None:
        return None
    if mirror.busy():
        return POLL
    canvas = bpy.data.images.get(_stroke_canvas)
    _stroke_canvas = None
    if _stand_in is not None:
        paint.restore_color(bpy.context, _stand_in)
    _finish_stroke(canvas)
    _stand_in = None
    _fill_mask = None
    return None


def _finish_stroke(canvas: bpy.types.Image | None) -> None:
    if canvas is None or len(props.layers(canvas)) == 0:
        return
    cached = _composite_cache.get(canvas.name)
    if cached is None:
        return
    current = select.read_pixels(canvas)
    if cached.shape != current.shape or not _changed_mask(current, cached, _fill_mask).any():
        return
    sync_canvas(canvas, _fill_mask)
    composite(canvas)
    overlay.tag_redraw(bpy.context)


def stroke_pixels(canvas: bpy.types.Image) -> np.ndarray | None:
    """Canvas RGBA where the in-progress native stroke changed pixels, transparent elsewhere."""
    if _stroke_canvas != canvas.name:
        return None
    cached = _composite_cache.get(canvas.name)
    if cached is None:
        return None
    current = select.read_pixels(canvas)
    if cached.shape != current.shape:
        return None
    mask = _changed_mask(current, cached)
    if not mask.any():
        return None
    current[~mask] = 0.0
    return current


def stroke_event(event: bpy.types.Event) -> None:
    """On release, swap stand-in pixels for the wanted color before the paint step is encoded."""
    if _stand_in is None or _stroke_canvas is None:
        return
    if event.type != "LEFTMOUSE" or event.value != "RELEASE":
        return
    canvas = bpy.data.images.get(_stroke_canvas)
    if canvas is None:
        return
    current = select.read_pixels(canvas)
    hit = np.all(np.rint(current[:, :, :3] * 255.0) == _stand_in.painted, axis=2)
    if not hit.any():
        return
    current[hit, :3] = _stand_in.wanted.astype(np.float32) / 255.0
    select.write_pixels(canvas, current)
    _stand_in.mask = hit if _stand_in.mask is None else _stand_in.mask | hit


def watch_stroke(
    context: bpy.types.Context, image: bpy.types.Image, event: bpy.types.Event
) -> None:
    """Arm end-of-stroke layer attribution for a native paint stroke on a layered canvas."""
    global _stroke_canvas, _stand_in, _fill_mask
    if len(props.layers(image)) == 0:
        return
    if undo.stale(image):
        undo.record(context, image)
    if image.name not in _composite_cache:
        _composite_cache[image.name] = select.read_pixels(image)
    _stroke_canvas = image.name
    if _stand_in is None and _fill_mask is None:
        _stand_in = paint.stand_in(context, _composite_cache[image.name])
        _fill_mask = paint.fill_mask(context, image, event)
    if not bpy.app.timers.is_registered(_stroke_tick):
        bpy.app.timers.register(_stroke_tick, first_interval=POLL)


class BLIX_OT_stroke_sync(bpy.types.Operator):
    """Apply the native stroke to the active layer and recomposite when it ends"""

    bl_idname = "blix.stroke_sync"
    bl_label = "Sync Stroke to Layer"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        image = select.edit_image(context)
        return image is not None and len(props.layers(image)) > 0

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        assert image is not None
        watch_stroke(context, image, event)
        return {"PASS_THROUGH"}


_keymaps: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def _register_keymap() -> None:
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return
    keyconfig = window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="Image Paint", space_type="EMPTY")
    item = keymap.keymap_items.new(BLIX_OT_stroke_sync.bl_idname, "LEFTMOUSE", "PRESS", any=True)
    _keymaps.append((keymap, item))
    keymap = keyconfig.keymaps.new(name="Image", space_type="IMAGE_EDITOR")
    item = keymap.keymap_items.new(BLIX_OT_layer_toggle_above.bl_idname, "H", "PRESS")
    _keymaps.append((keymap, item))


def _unregister_keymap() -> None:
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()


@persistent
def _clear_cache(*_args: Any) -> None:
    _composite_cache.clear()
    _sync_targets.clear()
    _history.clear()
    _cursor.clear()


@persistent
def _undo_refresh(*_args: Any) -> None:
    _refresh_cache(-1)


@persistent
def _redo_refresh(*_args: Any) -> None:
    _refresh_cache(1)


def _refresh_cache(step: int) -> None:
    """Undo or redo moved the canvas; restore matching layer state, else re-baseline the cache."""
    for name in list(_composite_cache):
        if bpy.data.images.get(name) is None:
            _composite_cache.pop(name)
    restored = False
    for image in bpy.data.images:
        if len(props.layers(image)) == 0:
            continue
        pixels = select.read_pixels(image)
        if _apply_history(image, pixels, step):
            restored = True
        elif _repair_stale_layers(image):
            restored = True
            _composite_cache[image.name] = pixels
        else:
            _composite_cache[image.name] = pixels
        if _heal(image):
            restored = True
    if restored:
        overlay.tag_redraw(bpy.context)


@persistent
def _pack_on_save(*_args: Any) -> None:
    for image in bpy.data.images:
        if len(props.layers(image)) == 0:
            continue
        _pack_layers(image)
        if image.is_dirty:
            image.pack()


@contextmanager
def _undo_step(
    context: bpy.types.Context, canvas: bpy.types.Image, message: str | None = None
) -> Iterator[None]:
    if message is None:
        undo.record(context, canvas)
        yield
        undo.record(context, canvas)
        return
    sync_canvas(canvas)
    push_history(canvas)
    yield
    if context.scene is not None:
        cels.sync(context.scene, canvas)
    push_history(canvas)
    undo.forget(canvas)
    undo.mark(canvas)
    _pack_layers(canvas)
    cast(Any, bpy.ops.ed).undo_push(message=message)


class _CanvasOperator(bpy.types.Operator):
    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return resolve_canvas(context) is not None


class BLIX_OT_layers_init(bpy.types.Operator):
    """Turn current image into a layered canvas with a Background layer"""

    bl_idname = "blix.layers_init"
    bl_label = "Initialize Layers"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        image = select.edit_image(context)
        return (
            image is not None
            and image.size[0] > 0
            and len(props.layers(image)) == 0
            and props.canvas_of(image) is None
        )

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        assert image is not None
        init_layers(image)
        _pack_layers(image)
        cast(Any, bpy.ops.ed).undo_push(message="Blix Init Layers")
        return {"FINISHED"}


class BLIX_OT_layer_add(_CanvasOperator):
    """Add transparent layer above the active layer"""

    bl_idname = "blix.layer_add"
    bl_label = "Add Layer"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = resolve_canvas(context)
        assert canvas is not None
        add_layer(canvas, next_layer_number(canvas))
        if context.scene is not None:
            cels.sync(context.scene, canvas)
        _pack_layers(canvas)
        cast(Any, bpy.ops.ed).undo_push(message="Blix Add Layer")
        return {"FINISHED"}


class BLIX_OT_layer_remove(_CanvasOperator):
    """Remove the active layer"""

    bl_idname = "blix.layer_remove"
    bl_label = "Remove Layer"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        canvas = resolve_canvas(context)
        if canvas is None or len(props.layers(canvas)) < 2:
            return False
        layer = active_layer(canvas)
        return layer is not None and not layer.lock

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = resolve_canvas(context)
        assert canvas is not None
        with _undo_step(context, canvas, "Blix Remove Layer"):
            remove_layer(canvas, props.layers_index(canvas))
        return {"FINISHED"}


class BLIX_OT_layer_duplicate(_CanvasOperator):
    """Duplicate the active layer"""

    bl_idname = "blix.layer_duplicate"
    bl_label = "Duplicate Layer"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = resolve_canvas(context)
        assert canvas is not None
        with _undo_step(context, canvas, "Blix Duplicate Layer"):
            duplicate_layer(canvas, props.layers_index(canvas))
        return {"FINISHED"}


class BLIX_OT_layer_move(_CanvasOperator):
    """Move the active layer up or down the stack"""

    bl_idname = "blix.layer_move"
    bl_label = "Move Layer"
    bl_options = {"REGISTER", "INTERNAL"}

    up: bpy.props.BoolProperty(default=True)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = resolve_canvas(context)
        assert canvas is not None
        stack = props.layers(canvas)
        index = props.layers_index(canvas)
        target = index - 1 if self.up else index + 1
        if not 0 <= target < len(stack):
            return {"CANCELLED"}
        with _undo_step(context, canvas, "Blix Move Layer"):
            sync_canvas(canvas)
            stack.move(index, target)
            props.set_layers_index(canvas, target)
            composite(canvas)
        return {"FINISHED"}


class BLIX_OT_layer_merge_down(_CanvasOperator):
    """Merge the active layer into the layer below"""

    bl_idname = "blix.layer_merge_down"
    bl_label = "Merge Down"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        canvas = resolve_canvas(context)
        if canvas is None:
            return False
        index = props.layers_index(canvas)
        stack = props.layers(canvas)
        if not 0 <= index < len(stack) - 1:
            return False
        if len(stack[index].cels) or len(stack[index + 1].cels):
            cls.poll_message_set("Layers with cels cannot be merged")
            return False
        return not stack[index].lock and not locked(stack[index + 1])

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = resolve_canvas(context)
        assert canvas is not None
        with _undo_step(context, canvas, "Blix Merge Down"):
            merge_down(canvas, props.layers_index(canvas))
        return {"FINISHED"}


class BLIX_OT_layer_flatten(_CanvasOperator):
    """Flatten all layers into a single Background layer"""

    bl_idname = "blix.layer_flatten"
    bl_label = "Flatten"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = resolve_canvas(context)
        assert canvas is not None
        with _undo_step(context, canvas, "Blix Flatten"):
            flatten(canvas)
        return {"FINISHED"}


class BLIX_OT_layers_update(_CanvasOperator):
    """Recomposite all layers into the canvas image"""

    bl_idname = "blix.layers_update"
    bl_label = "Update Composite"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = resolve_canvas(context)
        assert canvas is not None
        with _undo_step(context, canvas):
            sync_canvas(canvas)
            composite(canvas)
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_OT_layer_view_toggle(_CanvasOperator):
    """Switch the editor between the composite canvas and the active layer"""

    bl_idname = "blix.layer_view_toggle"
    bl_label = "Toggle Layer View"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = resolve_canvas(context)
        assert canvas is not None
        space = cast(bpy.types.SpaceImageEditor, context.space_data)
        if space.image == canvas:
            layer = active_layer(canvas)
            if layer is None or layer.image is None:
                return {"CANCELLED"}
            sync_canvas(canvas)
            undo.record(context, layer.image)
            space.image = layer.image
        else:
            with _undo_step(context, canvas):
                composite(canvas)
            space.image = canvas
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_OT_layer_toggle_above(_CanvasOperator):
    """Hide every layer above the active one, or show them all when none is visible"""

    bl_idname = "blix.layer_toggle_above"
    bl_label = "Toggle Layers Above"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        canvas = resolve_canvas(context)
        return canvas is not None and len(layers_above(canvas)) > 0

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = resolve_canvas(context)
        assert canvas is not None
        with _undo_step(context, canvas, "Blix Toggle Layers Above"):
            toggle_above(canvas)
        return {"FINISHED"}


_classes = (
    BlixCelSlot,
    BlixLayer,
    BLIX_OT_stroke_sync,
    BLIX_OT_layers_init,
    BLIX_OT_layer_add,
    BLIX_OT_layer_remove,
    BLIX_OT_layer_duplicate,
    BLIX_OT_layer_move,
    BLIX_OT_layer_merge_down,
    BLIX_OT_layer_flatten,
    BLIX_OT_layers_update,
    BLIX_OT_layer_view_toggle,
    BLIX_OT_layer_toggle_above,
)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    image_cls = cast(Any, bpy.types.Image)
    image_cls.blix_layers = bpy.props.CollectionProperty(type=BlixLayer)
    image_cls.blix_layers_index = bpy.props.IntProperty(default=0, update=_active_changed)
    image_cls.blix_canvas = bpy.props.PointerProperty(type=bpy.types.Image)
    bpy.app.handlers.save_pre.append(_pack_on_save)
    bpy.app.handlers.load_post.append(_clear_cache)
    bpy.app.handlers.undo_post.append(_undo_refresh)
    bpy.app.handlers.redo_post.append(_redo_refresh)
    _register_keymap()


def unregister() -> None:
    global _stroke_canvas
    _unregister_keymap()
    if bpy.app.timers.is_registered(_stroke_tick):
        bpy.app.timers.unregister(_stroke_tick)
    _stroke_canvas = None
    bpy.app.handlers.redo_post.remove(_redo_refresh)
    bpy.app.handlers.undo_post.remove(_undo_refresh)
    bpy.app.handlers.load_post.remove(_clear_cache)
    bpy.app.handlers.save_pre.remove(_pack_on_save)
    _composite_cache.clear()
    _sync_targets.clear()
    _history.clear()
    image_cls = cast(Any, bpy.types.Image)
    del image_cls.blix_canvas
    del image_cls.blix_layers_index
    del image_cls.blix_layers
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
