"""Nearest-neighbor transforms on the floating selection buffer."""

import math
from typing import TYPE_CHECKING

import bpy
import numpy as np

from . import overlay, select

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

SNAP_ANGLE = math.pi / 12


def rotate90_buffer(buffer: np.ndarray, turns: int) -> np.ndarray:
    return np.rot90(buffer, k=-turns).copy()


def flip_buffer(buffer: np.ndarray, horizontal: bool) -> np.ndarray:
    return buffer[:, ::-1].copy() if horizontal else buffer[::-1].copy()


def scale_buffer_nn(buffer: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    new_w, new_h = size
    height, width = buffer.shape[:2]
    ys = np.minimum(((np.arange(new_h) + 0.5) * height / new_h).astype(np.int64), height - 1)
    xs = np.minimum(((np.arange(new_w) + 0.5) * width / new_w).astype(np.int64), width - 1)
    return buffer[ys[:, None], xs[None, :]].copy()


def rotate_buffer_nn(buffer: np.ndarray, angle: float) -> np.ndarray:
    height, width = buffer.shape[:2]
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    new_w = max(1, math.ceil(abs(width * cos_a) + abs(height * sin_a)))
    new_h = max(1, math.ceil(abs(width * sin_a) + abs(height * cos_a)))
    out = np.zeros((new_h, new_w, 4), dtype=np.float32)
    yy, xx = np.mgrid[0:new_h, 0:new_w]
    dx = xx + 0.5 - new_w / 2
    dy = yy + 0.5 - new_h / 2
    sx = np.floor(cos_a * dx + sin_a * dy + width / 2).astype(np.int64)
    sy = np.floor(-sin_a * dx + cos_a * dy + height / 2).astype(np.int64)
    mask = (sx >= 0) & (sx < width) & (sy >= 0) & (sy < height)
    out[mask] = buffer[sy[mask], sx[mask]]
    return out


def centered_origin(rect: select.Rect, size: tuple[int, int]) -> tuple[int, int]:
    cx = (rect[0] + rect[2]) / 2
    cy = (rect[1] + rect[3]) / 2
    return (
        int(math.floor(cx - size[0] / 2 + 0.5)),
        int(math.floor(cy - size[1] / 2 + 0.5)),
    )


def _rect_center(rect: select.Rect) -> tuple[float, float]:
    return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)


def _rotated_quad(rect: select.Rect, angle: float) -> select.Quad:
    cx, cy = _rect_center(rect)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    quad: select.Quad = []
    for x, y in select.rect_quad(rect):
        dx, dy = x - cx, y - cy
        quad.append((cx + cos_a * dx - sin_a * dy, cy + sin_a * dx + cos_a * dy))
    return quad


def _commit_buffer(
    context: bpy.types.Context,
    image: bpy.types.Image,
    rect: select.Rect,
    buffer: np.ndarray,
    message: str,
) -> None:
    size = (buffer.shape[1], buffer.shape[0])
    origin = centered_origin(rect, size)
    select.apply_buffer(image, rect, buffer, origin)
    select.finish_float(context, image, select.clip_rect(image, origin, size), message)


class BLIX_OT_select_flip(bpy.types.Operator):
    """Flip selected pixels in place"""

    bl_idname = "blix.select_flip"
    bl_label = "Flip Selection"
    bl_options = {"REGISTER", "INTERNAL"}

    horizontal: bpy.props.BoolProperty(default=True)

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return select.can_float(context)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        rect = select.session.rect
        assert image is not None and rect is not None
        flipped = flip_buffer(select.lift(image, rect), self.horizontal)
        select.apply_buffer(image, rect, flipped, (rect[0], rect[1]))
        select.finish_float(context, image, rect, "Blix Flip Selection")
        return {"FINISHED"}


class BLIX_OT_select_rotate90(bpy.types.Operator):
    """Rotate selected pixels by quarter turns around the selection center"""

    bl_idname = "blix.select_rotate90"
    bl_label = "Rotate Selection 90"
    bl_options = {"REGISTER", "INTERNAL"}

    turns: bpy.props.IntProperty(default=1, min=1, max=3)

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return select.can_float(context)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        rect = select.session.rect
        assert image is not None and rect is not None
        rotated = rotate90_buffer(select.lift(image, rect), self.turns)
        _commit_buffer(context, image, rect, rotated, "Blix Rotate Selection")
        return {"FINISHED"}


class BLIX_OT_select_scale(bpy.types.Operator):
    """Scale selected pixels; Enter or click confirms, Esc cancels"""

    bl_idname = "blix.select_scale"
    bl_label = "Scale Selection"
    bl_options = {"REGISTER", "INTERNAL"}

    _start_dist: float
    _factor: float

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return select.can_float(context)

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        rect = select.session.rect
        region = context.region
        assert image is not None and rect is not None and region is not None
        select.start_float(image, rect)
        cx, cy = _rect_center(rect)
        mx, my = select.mouse_pixel(region, image, event)
        self._start_dist = max(math.hypot(mx - cx, my - cy), 1e-3)
        self._factor = 1.0
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        overlay.tag_redraw(context)
        return {"RUNNING_MODAL"}

    def modal(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        rect = select.session.rect
        if image is None or rect is None or select.session.buffer is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            cx, cy = _rect_center(rect)
            mx, my = select.mouse_pixel(region, image, event)
            self._factor = max(math.hypot(mx - cx, my - cy) / self._start_dist, 0.01)
            half_w = (rect[2] - rect[0]) * self._factor / 2
            half_h = (rect[3] - rect[1]) * self._factor / 2
            select.session.preview_quad = [
                (cx - half_w, cy - half_h),
                (cx + half_w, cy - half_h),
                (cx + half_w, cy + half_h),
                (cx - half_w, cy + half_h),
            ]
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        if (event.type == "LEFTMOUSE" and event.value == "PRESS") or (
            event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS"
        ):
            buffer = select.session.buffer
            new_w = max(1, int(math.floor((rect[2] - rect[0]) * self._factor + 0.5)))
            new_h = max(1, int(math.floor((rect[3] - rect[1]) * self._factor + 0.5)))
            scaled = scale_buffer_nn(buffer, (new_w, new_h))
            _commit_buffer(context, image, rect, scaled, "Blix Scale Selection")
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            select.session.drop_float()
            overlay.tag_redraw(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


class BLIX_OT_select_rotate(bpy.types.Operator):
    """Rotate selected pixels freely; Ctrl snaps to 15 degrees, Enter or click confirms"""

    bl_idname = "blix.select_rotate"
    bl_label = "Rotate Selection"
    bl_options = {"REGISTER", "INTERNAL"}

    _start_angle: float
    _angle: float

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return select.can_float(context)

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        rect = select.session.rect
        region = context.region
        assert image is not None and rect is not None and region is not None
        select.start_float(image, rect)
        cx, cy = _rect_center(rect)
        mx, my = select.mouse_pixel(region, image, event)
        self._start_angle = math.atan2(my - cy, mx - cx)
        self._angle = 0.0
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        overlay.tag_redraw(context)
        return {"RUNNING_MODAL"}

    def modal(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        rect = select.session.rect
        if image is None or rect is None or select.session.buffer is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            cx, cy = _rect_center(rect)
            mx, my = select.mouse_pixel(region, image, event)
            angle = math.atan2(my - cy, mx - cx) - self._start_angle
            if event.ctrl:
                angle = round(angle / SNAP_ANGLE) * SNAP_ANGLE
            self._angle = angle
            select.session.preview_quad = _rotated_quad(rect, angle)
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        if (event.type == "LEFTMOUSE" and event.value == "PRESS") or (
            event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS"
        ):
            rotated = rotate_buffer_nn(select.session.buffer, self._angle)
            _commit_buffer(context, image, rect, rotated, "Blix Rotate Selection")
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            select.session.drop_float()
            overlay.tag_redraw(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


_classes = (
    BLIX_OT_select_flip,
    BLIX_OT_select_rotate90,
    BLIX_OT_select_scale,
    BLIX_OT_select_rotate,
)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
