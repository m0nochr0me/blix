"""RotSprite transforms on the floating selection buffer."""

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


def scale2x(buffer: np.ndarray) -> np.ndarray:
    """One EPX pass: doubles resolution, copies existing pixels only."""
    height, width = buffer.shape[:2]
    up = np.concatenate([buffer[:1], buffer[:-1]], axis=0)
    down = np.concatenate([buffer[1:], buffer[-1:]], axis=0)
    left = np.concatenate([buffer[:, :1], buffer[:, :-1]], axis=1)
    right = np.concatenate([buffer[:, 1:], buffer[:, -1:]], axis=1)
    ul = (up == left).all(axis=-1)
    ur = (up == right).all(axis=-1)
    dl = (down == left).all(axis=-1)
    dr = (down == right).all(axis=-1)
    out = np.empty((height * 2, width * 2, buffer.shape[2]), dtype=buffer.dtype)
    out[0::2, 0::2] = np.where((ul & ~dl & ~ur)[..., None], up, buffer)
    out[0::2, 1::2] = np.where((ur & ~ul & ~dr)[..., None], right, buffer)
    out[1::2, 0::2] = np.where((dl & ~dr & ~ul)[..., None], left, buffer)
    out[1::2, 1::2] = np.where((dr & ~ur & ~dl)[..., None], down, buffer)
    return out


def upscale8x(buffer: np.ndarray) -> np.ndarray:
    return scale2x(scale2x(scale2x(buffer)))


def scale_rotsprite(up8: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    new_w, new_h = size
    height, width = up8.shape[0] // 8, up8.shape[1] // 8
    ys = np.minimum(
        ((np.arange(new_h) + 0.5) * height * 8 / new_h).astype(np.int64), height * 8 - 1
    )
    xs = np.minimum(((np.arange(new_w) + 0.5) * width * 8 / new_w).astype(np.int64), width * 8 - 1)
    return up8[ys[:, None], xs[None, :]].copy()


def rotate_rotsprite(up8: np.ndarray, angle: float) -> np.ndarray:
    height, width = up8.shape[0] // 8, up8.shape[1] // 8
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    new_w = max(1, math.ceil(abs(width * cos_a) + abs(height * sin_a)))
    new_h = max(1, math.ceil(abs(width * sin_a) + abs(height * cos_a)))
    out = np.zeros((new_h, new_w, up8.shape[2]), dtype=up8.dtype)
    yy, xx = np.mgrid[0:new_h, 0:new_w]
    dx = xx + 0.5 - new_w / 2
    dy = yy + 0.5 - new_h / 2
    sx = np.floor((cos_a * dx + sin_a * dy + width / 2) * 8).astype(np.int64)
    sy = np.floor((-sin_a * dx + cos_a * dy + height / 2) * 8).astype(np.int64)
    inside = (sx >= 0) & (sx < width * 8) & (sy >= 0) & (sy < height * 8)
    out[inside] = up8[sy[inside], sx[inside]]
    return out


def stack_float(buffer: np.ndarray, float_mask: np.ndarray) -> np.ndarray:
    return np.dstack([buffer, float_mask.astype(buffer.dtype)])


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


def _preview_buffer(buffer: np.ndarray, origin: tuple[int, int]) -> None:
    ox, oy = origin
    select.session.texture = select.make_texture(buffer)
    select.session.tex_quad = select.rect_quad((ox, oy, ox + buffer.shape[1], oy + buffer.shape[0]))


def _confirm_event(event: bpy.types.Event, drag: bool) -> bool:
    if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
        return True
    if drag:
        return event.type == "LEFTMOUSE" and event.value == "RELEASE"
    return event.type == "LEFTMOUSE" and event.value == "PRESS"


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
        mask = select.session.mask
        assert image is not None and rect is not None and mask is not None
        buffer, sub = select.lift(image, mask, rect)
        flipped = flip_buffer(buffer, self.horizontal)
        flipped_sub = flip_buffer(sub, self.horizontal)
        select.commit_float(context, image, flipped, flipped_sub, (rect[0], rect[1]))
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
        mask = select.session.mask
        assert image is not None and rect is not None and mask is not None
        buffer, sub = select.lift(image, mask, rect)
        rotated = rotate90_buffer(buffer, self.turns)
        origin = centered_origin(rect, (rotated.shape[1], rotated.shape[0]))
        select.commit_float(context, image, rotated, rotate90_buffer(sub, self.turns), origin)
        return {"FINISHED"}


class BLIX_OT_select_scale(bpy.types.Operator):
    """Scale selected pixels; Shift locks aspect ratio, Enter or click confirms, Esc cancels"""

    bl_idname = "blix.select_scale"
    bl_label = "Scale Selection"
    bl_options = {"REGISTER", "INTERNAL"}

    drag: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE", "HIDDEN"})
    handle_x: bpy.props.IntProperty(default=0, min=-1, max=1, options={"SKIP_SAVE", "HIDDEN"})
    handle_y: bpy.props.IntProperty(default=0, min=-1, max=1, options={"SKIP_SAVE", "HIDDEN"})

    _start_dist: float
    _size: tuple[int, int]
    _origin: tuple[int, int]
    _moved: bool
    _stacked: np.ndarray
    _up8: np.ndarray

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return select.can_float(context)

    def _resampled(self) -> np.ndarray:
        height, width = self._stacked.shape[:2]
        if self._size == (width, height):
            return self._stacked
        return scale_rotsprite(self._up8, self._size)

    def _ratio_rect(self, rect: select.Rect, dragged: select.Rect) -> select.Rect:
        """Snap dragged rect to source aspect: opposite corner/edge anchored, free axis centered."""
        x0, y0, x1, y1 = dragged
        orig_w, orig_h = rect[2] - rect[0], rect[3] - rect[1]
        fx = (x1 - x0) / orig_w
        fy = (y1 - y0) / orig_h
        if self.handle_x and self.handle_y:
            factor = fx if abs(fx - 1) >= abs(fy - 1) else fy
        else:
            factor = fx if self.handle_x else fy
        new_w = max(1, int(math.floor(orig_w * factor + 0.5)))
        new_h = max(1, int(math.floor(orig_h * factor + 0.5)))
        if self.handle_x > 0:
            x1 = x0 + new_w
        elif self.handle_x < 0:
            x0 = x1 - new_w
        else:
            x0 = int(math.floor((rect[0] + rect[2]) / 2 - new_w / 2 + 0.5))
            x1 = x0 + new_w
        if self.handle_y > 0:
            y1 = y0 + new_h
        elif self.handle_y < 0:
            y0 = y1 - new_h
        else:
            y0 = int(math.floor((rect[1] + rect[3]) / 2 - new_h / 2 + 0.5))
            y1 = y0 + new_h
        return (x0, y0, x1, y1)

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        rect = select.session.rect
        mask = select.session.mask
        region = context.region
        assert image is not None and rect is not None and mask is not None and region is not None
        select.start_float(image, mask, rect)
        assert select.session.buffer is not None and select.session.float_mask is not None
        self._stacked = stack_float(select.session.buffer, select.session.float_mask)
        self._up8 = upscale8x(self._stacked)
        self._size = (rect[2] - rect[0], rect[3] - rect[1])
        self._origin = (rect[0], rect[1])
        self._moved = False
        if not (self.handle_x or self.handle_y):
            cx, cy = _rect_center(rect)
            mx, my = select.mouse_pixel(region, image, event)
            self._start_dist = max(math.hypot(mx - cx, my - cy), 1e-3)
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        overlay.tag_redraw(context)
        return {"RUNNING_MODAL"}

    def _update(self, rect: select.Rect, mx: float, my: float, ratio: bool) -> None:
        if self.handle_x or self.handle_y:
            x0, y0, x1, y1 = rect
            if self.handle_x > 0:
                x1 = max(round(mx), rect[0] + 1)
            elif self.handle_x < 0:
                x0 = min(round(mx), rect[2] - 1)
            if self.handle_y > 0:
                y1 = max(round(my), rect[1] + 1)
            elif self.handle_y < 0:
                y0 = min(round(my), rect[3] - 1)
            if ratio:
                x0, y0, x1, y1 = self._ratio_rect(rect, (x0, y0, x1, y1))
            self._size = (x1 - x0, y1 - y0)
            self._origin = (x0, y0)
        else:
            cx, cy = _rect_center(rect)
            factor = max(math.hypot(mx - cx, my - cy) / self._start_dist, 0.01)
            self._size = (
                max(1, int(math.floor((rect[2] - rect[0]) * factor + 0.5))),
                max(1, int(math.floor((rect[3] - rect[1]) * factor + 0.5))),
            )
            self._origin = centered_origin(rect, self._size)

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        rect = select.session.rect
        if image is None or rect is None or select.session.buffer is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            self._moved = True
            mx, my = select.mouse_pixel(region, image, event)
            self._update(rect, mx, my, event.shift)
            _preview_buffer(self._resampled()[:, :, :4], self._origin)
            select.session.preview_quad = select.rect_quad(
                (
                    self._origin[0],
                    self._origin[1],
                    self._origin[0] + self._size[0],
                    self._origin[1] + self._size[1],
                )
            )
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        if _confirm_event(event, self.drag):
            if self.drag and not self._moved:
                select.session.drop_float()
                overlay.tag_redraw(context)
                return {"CANCELLED"}
            scaled = self._resampled()
            select.commit_float(
                context, image, scaled[:, :, :4], scaled[:, :, 4] > 0.5, self._origin
            )
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

    drag: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE", "HIDDEN"})

    _start_angle: float
    _angle: float
    _moved: bool
    _stacked: np.ndarray
    _up8: np.ndarray

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return select.can_float(context)

    def _rotated(self) -> np.ndarray:
        if self._angle == 0.0:
            return self._stacked
        return rotate_rotsprite(self._up8, self._angle)

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        rect = select.session.rect
        mask = select.session.mask
        region = context.region
        assert image is not None and rect is not None and mask is not None and region is not None
        select.start_float(image, mask, rect)
        assert select.session.buffer is not None and select.session.float_mask is not None
        self._stacked = stack_float(select.session.buffer, select.session.float_mask)
        self._up8 = upscale8x(self._stacked)
        cx, cy = _rect_center(rect)
        mx, my = select.mouse_pixel(region, image, event)
        self._start_angle = math.atan2(my - cy, mx - cx)
        self._angle = 0.0
        self._moved = False
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        overlay.tag_redraw(context)
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        rect = select.session.rect
        if image is None or rect is None or select.session.buffer is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            self._moved = True
            cx, cy = _rect_center(rect)
            mx, my = select.mouse_pixel(region, image, event)
            angle = math.atan2(my - cy, mx - cx) - self._start_angle
            if event.ctrl:
                angle = round(angle / SNAP_ANGLE) * SNAP_ANGLE
            self._angle = angle
            rotated = self._rotated()
            origin = centered_origin(rect, (rotated.shape[1], rotated.shape[0]))
            _preview_buffer(rotated[:, :, :4], origin)
            select.session.preview_quad = _rotated_quad(rect, angle)
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        if _confirm_event(event, self.drag):
            if self.drag and not self._moved:
                select.session.drop_float()
                overlay.tag_redraw(context)
                return {"CANCELLED"}
            rotated = self._rotated()
            origin = centered_origin(rect, (rotated.shape[1], rotated.shape[0]))
            select.commit_float(context, image, rotated[:, :, :4], rotated[:, :, 4] > 0.5, origin)
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
