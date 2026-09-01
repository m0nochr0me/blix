"""Layer stack on a canvas Image with numpy compositing. Index 0 is the top layer."""

import re
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np
from bpy.app.handlers import persistent

from . import overlay, props, select, undo

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
        if not layer.visible or layer.opacity == 0.0 or image is None:
            continue
        if tuple(image.size) != (width, height):
            continue
        dest = blend_over(dest, select.read_pixels(image), layer.blend, layer.opacity)
    return dest


_composite_cache: dict[str, np.ndarray] = {}
_sync_targets: dict[str, int] = {}

_HISTORY_CAP = 32
_HistoryEntry = tuple[np.ndarray, dict[str, np.ndarray]]
_history: dict[str, deque[_HistoryEntry]] = {}


def push_history(canvas: bpy.types.Image) -> None:
    """Snapshot canvas and layer pixels so undo can restore layers by canvas state."""
    if len(props.layers(canvas)) == 0:
        return
    layer_map = {
        layer.image.name: select.read_pixels(layer.image)
        for layer in props.layers(canvas)
        if layer.image is not None
    }
    entries = _history.setdefault(canvas.name, deque(maxlen=_HISTORY_CAP))
    pixels = select.read_pixels(canvas)
    if entries and np.array_equal(entries[-1][0], pixels):
        entries[-1] = (entries[-1][0], layer_map)
        return
    entries.append((pixels, layer_map))


def _apply_history(canvas: bpy.types.Image, current: np.ndarray) -> bool:
    entries = _history.get(canvas.name)
    if entries is None:
        return False
    for pixels, layer_map in reversed(entries):
        if pixels.shape != current.shape or not np.array_equal(pixels, current):
            continue
        for layer in props.layers(canvas):
            image = layer.image
            if image is None:
                continue
            saved = layer_map.get(image.name)
            if saved is None or tuple(image.size) != (saved.shape[1], saved.shape[0]):
                continue
            if not np.array_equal(select.read_pixels(image), saved):
                select.write_pixels(image, saved)
        _composite_cache[canvas.name] = current
        return True
    return False


def _sync_target(canvas: bpy.types.Image) -> Any:
    stack = props.layers(canvas)
    index = _sync_targets.get(canvas.name, props.layers_index(canvas))
    if 0 <= index < len(stack):
        return stack[index]
    return active_layer(canvas)


def sync_canvas(canvas: bpy.types.Image) -> None:
    target = _sync_target(canvas)
    _sync_targets[canvas.name] = props.layers_index(canvas)
    cached = _composite_cache.get(canvas.name)
    if cached is None:
        return
    current = select.read_pixels(canvas)
    if cached.shape != current.shape:
        _composite_cache.pop(canvas.name)
        return
    mask = np.any(current != cached, axis=2)
    if not mask.any():
        return
    if target is None or target.image is None or target.lock:
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


def _layer_changed(self: bpy.types.PropertyGroup, context: bpy.types.Context) -> None:
    canvas = cast(bpy.types.Image, self.id_data)
    if len(props.layers(canvas)):
        sync_canvas(canvas)
        composite(canvas)
        overlay.tag_redraw(context)


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


class BlixLayer(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name", default="Layer")
    image: bpy.props.PointerProperty(type=bpy.types.Image)
    opacity: bpy.props.FloatProperty(
        name="Opacity", default=1.0, min=0.0, max=1.0, update=_layer_changed
    )
    blend: bpy.props.EnumProperty(
        name="Blend", items=BLEND_ITEMS, default="MIX", update=_layer_changed
    )
    visible: bpy.props.BoolProperty(name="Visible", default=True, update=_layer_changed)
    lock: bpy.props.BoolProperty(name="Lock", default=False)


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
    background = _clone_image(canvas, "Background", pixels)
    layer = props.layers(canvas).add()
    layer.name = "Background"
    layer.image = background
    props.set_layers_index(canvas, 0)
    _composite_cache[canvas.name] = pixels
    _sync_targets[canvas.name] = 0
    push_history(canvas)


def next_layer_name(canvas: bpy.types.Image) -> str:
    numbers = [
        int(match.group(1))
        for layer in props.layers(canvas)
        if (match := re.fullmatch(r"Layer (\d+)", layer.name))
    ]
    return f"Layer {max(numbers, default=0) + 1}"


def add_layer(canvas: bpy.types.Image, name: str) -> None:
    sync_canvas(canvas)
    stack = props.layers(canvas)
    layer = stack.add()
    layer.name = name
    layer.image = _new_layer_image(canvas, name)
    index = max(props.layers_index(canvas), 0)
    stack.move(len(stack) - 1, index)
    props.set_layers_index(canvas, index)


def remove_layer(canvas: bpy.types.Image, index: int) -> None:
    stack = props.layers(canvas)
    _composite_cache.pop(canvas.name, None)
    stack.remove(index)
    props.set_layers_index(canvas, min(index, len(stack) - 1))
    composite(canvas)


def copy_layer_name(canvas: bpy.types.Image, source: str) -> str:
    names = {layer.name for layer in props.layers(canvas)}
    name = f"{source} Copy"
    counter = 2
    while name in names:
        name = f"{source} Copy {counter}"
        counter += 1
    return name


def duplicate_layer(canvas: bpy.types.Image, index: int) -> None:
    sync_canvas(canvas)
    stack = props.layers(canvas)
    source = stack[index]
    name = copy_layer_name(canvas, source.name)
    layer = stack.add()
    layer.name = name
    layer.opacity = source.opacity
    layer.blend = source.blend
    layer.visible = source.visible
    layer.image = _clone_image(canvas, layer.name, select.read_pixels(source.image))
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
    below.image = _clone_image(canvas, below.name, merged)
    stack.remove(index)
    props.set_layers_index(canvas, index)
    composite(canvas)


def flatten(canvas: bpy.types.Image) -> None:
    sync_canvas(canvas)
    pixels = composite_pixels(canvas)
    select.write_pixels(canvas, pixels)
    _composite_cache[canvas.name] = select.read_pixels(canvas)
    stack = props.layers(canvas)
    for layer in stack:
        if layer.image is not None:
            props.set_canvas_of(layer.image, None)
    stack.clear()
    layer = stack.add()
    layer.name = "Background"
    layer.image = _new_layer_image(canvas, "Background")
    select.write_pixels(layer.image, pixels)
    layer.image.pack()
    props.set_layers_index(canvas, 0)


POLL = 0.05
_stroke_canvas: str | None = None


def _stroke_tick() -> float | None:
    global _stroke_canvas
    from . import mirror

    if _stroke_canvas is None:
        return None
    if mirror.busy():
        return POLL
    canvas = bpy.data.images.get(_stroke_canvas)
    _stroke_canvas = None
    if canvas is None or len(props.layers(canvas)) == 0:
        return None
    cached = _composite_cache.get(canvas.name)
    if cached is None:
        return None
    current = select.read_pixels(canvas)
    if cached.shape != current.shape or not (current != cached).any():
        return None
    sync_canvas(canvas)
    composite(canvas)
    overlay.tag_redraw(bpy.context)
    return None


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
        global _stroke_canvas
        image = select.edit_image(context)
        assert image is not None
        if image.name not in _composite_cache:
            _composite_cache[image.name] = select.read_pixels(image)
        _stroke_canvas = image.name
        if not bpy.app.timers.is_registered(_stroke_tick):
            bpy.app.timers.register(_stroke_tick, first_interval=POLL)
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


def _unregister_keymap() -> None:
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()


@persistent
def _clear_cache(*_args: Any) -> None:
    _composite_cache.clear()
    _sync_targets.clear()
    _history.clear()


@persistent
def _refresh_cache(*_args: Any) -> None:
    """Undo restored canvas pixels; restore matching layer state, else re-baseline the cache."""
    for name in list(_composite_cache):
        if bpy.data.images.get(name) is None:
            _composite_cache.pop(name)
    restored = False
    for image in bpy.data.images:
        if len(props.layers(image)) == 0:
            continue
        pixels = select.read_pixels(image)
        if _apply_history(image, pixels):
            restored = True
            continue
        _composite_cache[image.name] = pixels
    if restored:
        overlay.tag_redraw(bpy.context)


@persistent
def _pack_on_save(*_args: Any) -> None:
    for image in bpy.data.images:
        if len(props.layers(image)) == 0:
            continue
        for layer in props.layers(image):
            if layer.image is not None and layer.image.is_dirty:
                layer.image.pack()
        if image.is_dirty:
            image.pack()


@contextmanager
def _undo_step(
    context: bpy.types.Context, canvas: bpy.types.Image, message: str | None = None
) -> Iterator[None]:
    undo.record(context, canvas)
    yield
    undo.record(context, canvas)
    if message is not None:
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
        add_layer(canvas, next_layer_name(canvas))
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
        return not stack[index].lock and not stack[index + 1].lock

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


_classes = (
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
    bpy.app.handlers.undo_post.append(_refresh_cache)
    bpy.app.handlers.redo_post.append(_refresh_cache)
    _register_keymap()


def unregister() -> None:
    global _stroke_canvas
    _unregister_keymap()
    if bpy.app.timers.is_registered(_stroke_tick):
        bpy.app.timers.unregister(_stroke_tick)
    _stroke_canvas = None
    bpy.app.handlers.redo_post.remove(_refresh_cache)
    bpy.app.handlers.undo_post.remove(_refresh_cache)
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
