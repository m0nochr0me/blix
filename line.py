"""Shift line mode: hold Shift with the brush to chain straight strokes from the last point."""

import math
from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np

from . import mirror, overlay, paint, prefs, select, shapes, undo

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

preview = select.Preview()

_anchor: tuple[int, int] | None = None
_anchor_image = ""


def set_anchor(image_name: str, point: tuple[int, int]) -> None:
    global _anchor, _anchor_image
    _anchor = point
    _anchor_image = image_name


def anchor_for(image: bpy.types.Image) -> tuple[int, int] | None:
    return _anchor if _anchor_image == image.name else None


def _brush_tool_active(context: bpy.types.Context) -> bool:
    workspace = context.workspace
    if workspace is None:
        return False
    tool = workspace.tools.from_space_image_mode("PAINT", create=False)
    return tool is not None and tool.idname == "builtin.brush"


def _mouse_floor(
    region: bpy.types.Region, image: bpy.types.Image, event: bpy.types.Event
) -> tuple[int, int]:
    x, y = select.mouse_pixel(region, image, event)
    return math.floor(x), math.floor(y)


def _line_mask(
    context: bpy.types.Context,
    image: bpy.types.Image,
    start: tuple[int, int],
    end: tuple[int, int],
) -> np.ndarray:
    scene = context.scene
    assert scene is not None
    coverage = shapes.line_coverage((image.size[0], image.size[1]), start, end)
    coverage = mirror.expand(coverage, *mirror.enabled(scene))
    if select.session.image_name == image.name and select.session.mask is not None:
        coverage &= select.session.mask
    return coverage


def draw_line(
    context: bpy.types.Context,
    image: bpy.types.Image,
    start: tuple[int, int],
    end: tuple[int, int],
) -> None:
    coverage = _line_mask(context, image, start, end)
    if not coverage.any():
        return
    pixels = select.read_pixels(image)
    pixels[coverage] = paint.brush_colors(context)[0]
    undo.record(context, image)
    select.write_pixels(image, pixels)
    undo.record(context, image)


class BLIX_OT_line_anchor(bpy.types.Operator):
    """Run the native brush stroke, tracking where it ends as the Shift line anchor"""

    bl_idname = "blix.line_anchor"
    bl_label = "Line Anchor"
    bl_options = {"INTERNAL"}

    _image: str

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not prefs.shift_line() or not _brush_tool_active(context):
            return False
        image = select.edit_image(context)
        return image is not None and image.size[0] > 0 and image.size[1] > 0

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        region = context.region
        if image is None or region is None or not cast(Any, bpy.ops.paint.image_paint).poll():
            return {"PASS_THROUGH"}
        self._image = image.name
        self._record(context, event)
        result = cast(Any, bpy.ops.paint).image_paint("INVOKE_DEFAULT", mode="NORMAL")
        if "RUNNING_MODAL" not in result:
            return {"FINISHED"}
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        if mirror.painting():
            self._record(context, event)
            return {"PASS_THROUGH"}
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            return {"FINISHED"}
        return {"PASS_THROUGH"}

    def _record(self, context: bpy.types.Context, event: bpy.types.Event) -> None:
        image = select.edit_image(context)
        region = context.region
        if image is None or region is None or image.name != self._image:
            return
        set_anchor(image.name, _mouse_floor(region, image, event))


class BLIX_OT_brush_line(bpy.types.Operator):
    """Preview a line from the last painted point; click to paint it with the brush"""

    bl_idname = "blix.brush_line"
    bl_label = "Brush Line"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not prefs.shift_line() or not _brush_tool_active(context) or mirror.painting():
            return False
        image = select.edit_image(context)
        if image is None or image.size[0] == 0 or image.size[1] == 0:
            return False
        return anchor_for(image) is not None

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        region = context.region
        if image is None or region is None:
            return {"PASS_THROUGH"}
        self._update(context, image, event)
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        if image is None or anchor_for(image) is None:
            self._finish(context)
            return {"CANCELLED"}
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            self._update(context, image, event)
            return {"PASS_THROUGH"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            self._commit(context, image, event)
            return {"RUNNING_MODAL"}
        if event.type in {"LEFT_SHIFT", "RIGHT_SHIFT"} and event.value == "RELEASE":
            self._finish(context)
            return {"FINISHED"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._finish(context)
            return {"CANCELLED"}
        return {"PASS_THROUGH"}

    def _update(
        self, context: bpy.types.Context, image: bpy.types.Image, event: bpy.types.Event
    ) -> None:
        region = context.region
        start = anchor_for(image)
        assert region is not None
        assert start is not None
        end = _mouse_floor(region, image, event)
        coverage = _line_mask(context, image, start, end)
        bounds = select.mask_bbox(coverage)
        if bounds is None:
            preview.clear()
        else:
            x0, y0, x1, y1 = bounds
            buffer = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.float32)
            buffer[coverage[y0:y1, x0:x1]] = paint.brush_colors(context)[0]
            preview.set(image, bounds, buffer)
        overlay.tag_redraw(context)

    def _commit(
        self, context: bpy.types.Context, image: bpy.types.Image, event: bpy.types.Event
    ) -> None:
        region = context.region
        start = anchor_for(image)
        assert region is not None
        assert start is not None
        end = _mouse_floor(region, image, event)
        draw_line(context, image, start, end)
        set_anchor(image.name, end)
        self._update(context, image, event)

    def _finish(self, context: bpy.types.Context) -> None:
        preview.clear()
        overlay.tag_redraw(context)


_keymaps: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def _register_keymap() -> None:
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return
    keyconfig = window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="Image Paint", space_type="EMPTY")
    item = keymap.keymap_items.new(BLIX_OT_line_anchor.bl_idname, "LEFTMOUSE", "PRESS")
    _keymaps.append((keymap, item))
    for key in ("LEFT_SHIFT", "RIGHT_SHIFT"):
        item = keymap.keymap_items.new(BLIX_OT_brush_line.bl_idname, key, "PRESS", any=True)
        _keymaps.append((keymap, item))


def _unregister_keymap() -> None:
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()


_classes = (BLIX_OT_line_anchor, BLIX_OT_brush_line)


def register() -> None:
    global _anchor, _anchor_image
    _anchor = None
    _anchor_image = ""
    for cls in _classes:
        bpy.utils.register_class(cls)
    overlay.extra_draws.append(preview.draw)
    _register_keymap()


def unregister() -> None:
    _unregister_keymap()
    overlay.extra_draws.remove(preview.draw)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    preview.clear()
