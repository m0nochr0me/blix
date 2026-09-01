"""Pixel shape drawing: line, rectangle, ellipse and hexagon, as a paint-mode tool."""

import math
from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np

from . import guides, mirror, overlay, paint, props, select, undo

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

SNAP_ANGLE = math.pi / 4

KIND_ITEMS = (
    ("LINE", "Line", "Straight line between the drag endpoints"),
    ("RECT", "Rectangle", "Axis-aligned rectangle"),
    ("ELLIPSE", "Ellipse", "Ellipse inscribed in the drag rectangle"),
    ("HEXAGON", "Hexagon", "Hexagon inscribed in the drag rectangle"),
)

preview = select.Preview()

Point = tuple[float, float]


def border_of(solid: np.ndarray) -> np.ndarray:
    """Pixels of a filled shape that touch its edge, as a 1 px outline."""
    interior = solid.copy()
    interior[1:] &= solid[:-1]
    interior[:-1] &= solid[1:]
    interior[:, 1:] &= solid[:, :-1]
    interior[:, :-1] &= solid[:, 1:]
    interior[0] = False
    interior[-1] = False
    interior[:, 0] = False
    interior[:, -1] = False
    return solid & ~interior


def line_coverage(size: select.Size, start: tuple[int, int], end: tuple[int, int]) -> np.ndarray:
    width, height = size
    mask = np.zeros((height, width), dtype=bool)
    steps = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
    xs = np.rint(np.linspace(start[0], end[0], steps + 1)).astype(np.int64)
    ys = np.rint(np.linspace(start[1], end[1], steps + 1)).astype(np.int64)
    inside = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
    mask[ys[inside], xs[inside]] = True
    return mask


def hexagon_points(rect: select.Rect, pointy: bool) -> list[Point]:
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    qw, qh = (x1 - x0) / 4, (y1 - y0) / 4
    if pointy:
        return [(cx, y1), (x1, cy + qh), (x1, cy - qh), (cx, y0), (x0, cy - qh), (x0, cy + qh)]
    return [(x0, cy), (cx - qw, y1), (cx + qw, y1), (x1, cy), (cx + qw, y0), (cx - qw, y0)]


def shape_coverage(
    size: select.Size,
    kind: str,
    rect: select.Rect,
    endpoints: tuple[tuple[int, int], tuple[int, int]],
    filled: bool,
    pointy: bool,
) -> np.ndarray:
    if kind == "LINE":
        return line_coverage(size, *endpoints)
    if kind == "RECT":
        solid = select.rect_mask(size, rect)
    elif kind == "ELLIPSE":
        solid = select.ellipse_mask(size, rect)
    else:
        solid = select.polygon_mask(size, hexagon_points(rect, pointy))
    return solid if filled else border_of(solid)


class BLIX_OT_draw_shape(bpy.types.Operator):
    """Draw a shape; Shift constrains, Alt draws from center, Ctrl uses the secondary color"""

    bl_idname = "blix.draw_shape"
    bl_label = "Draw Shape"
    bl_options = {"REGISTER", "INTERNAL"}

    _anchor: tuple[int, int]
    _color: np.ndarray
    _coverage: np.ndarray | None

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        if image is None or image.size[0] == 0 or image.size[1] == 0:
            return {"PASS_THROUGH"}
        region = context.region
        assert region is not None
        near_guide = guides.find_near(region, image, event.mouse_region_x, event.mouse_region_y)
        if event.ctrl and near_guide >= 0:
            return {"PASS_THROUGH"}
        x, y = select.mouse_pixel(region, image, event)
        self._anchor = (math.floor(x), math.floor(y))
        primary, secondary = paint.brush_colors(context)
        self._color = secondary if event.ctrl else primary
        self._coverage = None
        self._update(context, image, self._anchor, event)
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _geometry(
        self, cursor: tuple[int, int], event: bpy.types.Event
    ) -> tuple[select.Rect, tuple[tuple[int, int], tuple[int, int]]]:
        ax, ay = self._anchor
        dx, dy = cursor[0] - ax, cursor[1] - ay
        sx, sy = dx, dy
        if event.shift:
            angle = round(math.atan2(dy, dx) / SNAP_ANGLE) * SNAP_ANGLE
            length = math.hypot(dx, dy)
            sx, sy = round(math.cos(angle) * length), round(math.sin(angle) * length)
            span = max(abs(dx), abs(dy))
            dx = span if dx >= 0 else -span
            dy = span if dy >= 0 else -span
        if event.alt:
            rect = (ax - abs(dx), ay - abs(dy), ax + abs(dx) + 1, ay + abs(dy) + 1)
            return rect, ((ax - sx, ay - sy), (ax + sx, ay + sy))
        rect = (min(ax, ax + dx), min(ay, ay + dy), max(ax, ax + dx) + 1, max(ay, ay + dy) + 1)
        return rect, ((ax, ay), (ax + sx, ay + sy))

    def _update(
        self,
        context: bpy.types.Context,
        image: bpy.types.Image,
        cursor: tuple[int, int],
        event: bpy.types.Event,
    ) -> None:
        scene = context.scene
        assert scene is not None
        size = (image.size[0], image.size[1])
        rect, ends = self._geometry(cursor, event)
        coverage = shape_coverage(
            size,
            props.shape_kind(scene),
            rect,
            ends,
            props.shape_filled(scene),
            props.shape_hex_pointy(scene),
        )
        coverage = mirror.expand(coverage, *mirror.enabled(scene))
        if select.session.image_name == image.name and select.session.mask is not None:
            coverage &= select.session.mask
        self._coverage = coverage
        bounds = select.mask_bbox(coverage)
        if bounds is None:
            preview.clear()
        else:
            x0, y0, x1, y1 = bounds
            buffer = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.float32)
            buffer[coverage[y0:y1, x0:x1]] = self._color
            preview.set(image, bounds, buffer)
        overlay.tag_redraw(context)

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        if image is None:
            preview.clear()
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type in {"MOUSEMOVE", "LEFT_SHIFT", "LEFT_ALT", "RIGHT_SHIFT", "RIGHT_ALT"}:
            x, y = select.mouse_pixel(region, image, event)
            self._update(context, image, (math.floor(x), math.floor(y)), event)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._commit(context, image)
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            preview.clear()
            overlay.tag_redraw(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}

    def _commit(self, context: bpy.types.Context, image: bpy.types.Image) -> None:
        coverage = self._coverage
        preview.clear()
        if coverage is None or not coverage.any():
            overlay.tag_redraw(context)
            return
        pixels = select.read_pixels(image)
        pixels[coverage] = self._color
        undo.record(context, image)
        select.write_pixels(image, pixels)
        undo.record(context, image)
        overlay.tag_redraw(context)


class BLIX_TOOL_shape(bpy.types.WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = "blix.shape_tool"
    bl_label = "Blix Shape"
    bl_description = "Draw lines, rectangles, ellipses and hexagons"
    bl_icon = "ops.gpencil.primitive_box"
    bl_widget = None
    bl_keymap = (
        ("blix.draw_shape", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
        ("blix.draw_shape", {"type": "LEFTMOUSE", "value": "PRESS", "ctrl": True}, None),
    )


_classes = (BLIX_OT_draw_shape,)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_shape_kind = bpy.props.EnumProperty(
        name="Shape", items=KIND_ITEMS, default="RECT"
    )
    scene_cls.blix_shape_filled = bpy.props.BoolProperty(
        name="Filled", description="Fill the shape instead of drawing a 1 px outline", default=False
    )
    scene_cls.blix_shape_hex_pointy = bpy.props.BoolProperty(
        name="Pointy Top",
        description="Hexagon with a vertex on top instead of a flat edge",
        default=True,
    )
    bpy.utils.register_tool(BLIX_TOOL_shape, after="blix.select_brush")
    overlay.extra_draws.append(preview.draw)


def unregister() -> None:
    overlay.extra_draws.remove(preview.draw)
    bpy.utils.unregister_tool(BLIX_TOOL_shape)
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_shape_hex_pointy
    del scene_cls.blix_shape_filled
    del scene_cls.blix_shape_kind
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    preview.clear()
