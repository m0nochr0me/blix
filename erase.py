"""Optional Ctrl+LMB behavior: erase the active layer with a round brush, compositing live."""

import math
from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np

from . import layers, mirror, overlay, prefs, props, select, undo

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

Point = tuple[float, float]


def erase_stamp(
    pixels: np.ndarray, center: Point, radius: float, clip_mask: np.ndarray | None = None
) -> None:
    cx, cy = center
    height, width = pixels.shape[:2]
    x0 = max(int(math.floor(cx - radius)), 0)
    y0 = max(int(math.floor(cy - radius)), 0)
    x1 = min(int(math.ceil(cx + radius)), width)
    y1 = min(int(math.ceil(cy + radius)), height)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    inside = (xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2 <= radius * radius
    if clip_mask is not None:
        inside &= clip_mask[y0:y1, x0:x1]
    pixels[y0:y1, x0:x1][inside, 3] = 0.0


def _brush_radius(context: bpy.types.Context) -> float:
    tool_settings = context.tool_settings
    paint_settings = tool_settings.image_paint if tool_settings is not None else None
    if paint_settings is None:
        return 12.0
    unified = cast(Any, paint_settings).unified_paint_settings
    brush = paint_settings.brush
    if unified is not None and unified.use_unified_size:
        return float(unified.size)
    if brush is not None:
        return float(brush.size)
    return 12.0


def _image_radius(region: bpy.types.Region, image: bpy.types.Image, screen: float) -> float:
    origin = overlay.image_to_region(region, image, 0.0, 0.0)
    unit = overlay.image_to_region(region, image, 1.0, 0.0)
    zoom = abs(unit[0] - origin[0])
    return max(screen / zoom, 0.5) if zoom else 0.5


class BLIX_OT_ctrl_erase(bpy.types.Operator):
    """Erase with a round brush on the active layer, compositing live; Esc cancels"""

    bl_idname = "blix.ctrl_erase"
    bl_label = "Ctrl Erase Stroke"
    bl_options = {"INTERNAL"}

    _canvas: bpy.types.Image | None
    _target: bpy.types.Image
    _snapshot: np.ndarray
    _mask: np.ndarray | None
    _last: Point
    _radius: float

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not prefs.ctrl_erase():
            return False
        image = select.edit_image(context)
        return image is not None and image.size[0] > 0 and image.size[1] > 0

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        if not cast(Any, bpy.ops.paint.image_paint).poll():
            return {"PASS_THROUGH"}
        image = select.edit_image(context)
        region = context.region
        assert image is not None
        assert region is not None
        self._canvas = image if len(props.layers(image)) else None
        target = image
        if self._canvas is not None:
            layer = layers.active_layer(self._canvas)
            if layer is None or layer.image is None or layers.locked(layer):
                return {"CANCELLED"}
            if tuple(layer.image.size) != tuple(self._canvas.size):
                return {"CANCELLED"}
            layers.sync_canvas(self._canvas)
            target = layer.image
        self._target = target
        self._snapshot = select.read_pixels(target)
        mask = select.session.mask if select.session.image_name == image.name else None
        if mask is not None and mask.shape != self._snapshot.shape[:2]:
            mask = None
        self._mask = mask
        self._radius = _image_radius(region, image, _brush_radius(context))
        self._last = select.mouse_pixel(region, image, event)
        undo.record(context, image)
        self._stamp_to(context, self._last)
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _stamp_to(self, context: bpy.types.Context, point: Point) -> None:
        scene = context.scene
        axes = mirror.enabled(scene) if scene is not None else (False, False)
        size = (self._target.size[0], self._target.size[1])
        pixels = select.read_pixels(self._target)
        distance = math.hypot(point[0] - self._last[0], point[1] - self._last[1])
        steps = max(int(distance / max(self._radius / 2, 0.5)), 1)
        for step in range(1, steps + 1):
            factor = step / steps
            center = (
                self._last[0] + (point[0] - self._last[0]) * factor,
                self._last[1] + (point[1] - self._last[1]) * factor,
            )
            for stamp in mirror.centers(center, size, *axes):
                erase_stamp(pixels, stamp, self._radius, self._mask)
        select.write_pixels(self._target, pixels)
        self._last = point
        if self._canvas is not None:
            layers.composite(self._canvas)
        overlay.tag_redraw(context)

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        region = context.region
        image = select.edit_image(context)
        if region is None or image is None:
            return {"CANCELLED"}
        if event.type == "MOUSEMOVE":
            self._stamp_to(context, select.mouse_pixel(region, image, event))
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            undo.record(context, image)
            return {"FINISHED"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            select.write_pixels(self._target, self._snapshot)
            if self._canvas is not None:
                layers.composite(self._canvas)
            overlay.tag_redraw(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


_keymaps: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def _register_keymap() -> None:
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return
    keyconfig = window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="Image Paint", space_type="EMPTY")
    item = keymap.keymap_items.new(BLIX_OT_ctrl_erase.bl_idname, "LEFTMOUSE", "PRESS", ctrl=True)
    _keymaps.append((keymap, item))


def _unregister_keymap() -> None:
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()


def register() -> None:
    bpy.utils.register_class(BLIX_OT_ctrl_erase)
    _register_keymap()


def unregister() -> None:
    _unregister_keymap()
    bpy.utils.unregister_class(BLIX_OT_ctrl_erase)
