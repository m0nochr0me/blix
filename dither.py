"""Ordered and error-diffusion dithering: gradient fill and pattern-masked brush, as paint tools."""

import math
from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np

from . import layers, mirror, overlay, paint, palette, patterns, props, select, undo

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

SNAP_ANGLE = math.pi / 4
CUSTOM_MAX = 256
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
GRADIENT_TAPS: dict[str, patterns.Taps | None] = {
    "PATTERN": None,
    "FLOYD_STEINBERG": patterns.FLOYD_STEINBERG,
    "ATKINSON": patterns.ATKINSON,
}

preview = select.Preview()


def custom_thresholds(image: bpy.types.Image) -> np.ndarray | None:
    width, height = image.size
    if not 0 < width <= CUSTOM_MAX or not 0 < height <= CUSTOM_MAX:
        return None
    return patterns.ranked(select.read_pixels(image)[:, :, :3] @ LUMA)


def pattern_thresholds(scene: bpy.types.Scene) -> np.ndarray | None:
    pattern = props.dither_pattern(scene)
    if pattern != "CUSTOM":
        return patterns.TILES[pattern]()
    image = props.dither_custom(scene)
    return None if image is None else custom_thresholds(image)


def _resolve_pattern(operator: bpy.types.Operator, scene: bpy.types.Scene) -> np.ndarray | None:
    thresholds = pattern_thresholds(scene)
    if thresholds is None:
        operator.report(
            {"ERROR"}, f"Custom pattern needs an image up to {CUSTOM_MAX}x{CUSTOM_MAX}"
        )
    return thresholds


def dither_region(
    rect: select.Rect,
    start: tuple[float, float],
    end: tuple[float, float],
    color_a: np.ndarray,
    color_b: np.ndarray,
    thresholds: np.ndarray | None,
    taps: patterns.Taps | None,
) -> np.ndarray:
    x0, y0, x1, y1 = rect
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dy * dy
    yy, xx = np.mgrid[y0:y1, x0:x1]
    if length_sq < 1e-9:
        t = np.zeros((y1 - y0, x1 - x0), dtype=np.float32)
    else:
        t = ((xx + 0.5 - start[0]) * dx + (yy + 0.5 - start[1]) * dy) / length_sq
        t = np.clip(t, 0.0, 1.0).astype(np.float32)
    if taps is not None:
        use_b = patterns.diffuse(t, taps)
    else:
        assert thresholds is not None
        use_b = t >= patterns.tile(rect, thresholds)
    return np.where(use_b[:, :, None], color_b, color_a).astype(np.float32)


def dither_stamp(
    pixels: np.ndarray,
    center: tuple[float, float],
    radius: float,
    color: np.ndarray,
    density: float,
    thresholds: np.ndarray,
    clip: select.Rect,
    clip_mask: np.ndarray | None = None,
    written: np.ndarray | None = None,
) -> None:
    cx, cy = center
    x0 = max(int(math.floor(cx - radius)), clip[0])
    y0 = max(int(math.floor(cy - radius)), clip[1])
    x1 = min(int(math.ceil(cx + radius)), clip[2])
    y1 = min(int(math.ceil(cy + radius)), clip[3])
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    inside = (xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2 <= radius * radius
    mask = inside & (patterns.tile((x0, y0, x1, y1), thresholds) < density)
    if clip_mask is not None:
        mask &= clip_mask[y0:y1, x0:x1]
    region = pixels[y0:y1, x0:x1]
    region[mask] = color
    pixels[y0:y1, x0:x1] = region
    if written is not None:
        written[y0:y1, x0:x1] |= mask


def _apply_buffer(
    pixels: np.ndarray, rect: select.Rect, buffer: np.ndarray, clip_mask: np.ndarray | None
) -> np.ndarray:
    """Write opaque buffer pixels into rect; returns the full-size mask of written pixels."""
    region = pixels[rect[1] : rect[3], rect[0] : rect[2]]
    write = buffer[:, :, 3] > 0.0
    if clip_mask is not None:
        write &= clip_mask[rect[1] : rect[3], rect[0] : rect[2]]
    region[write] = buffer[write]
    written = np.zeros(pixels.shape[:2], dtype=bool)
    written[rect[1] : rect[3], rect[0] : rect[2]] = write
    return written


def _gradient_colors(context: bpy.types.Context) -> tuple[np.ndarray, np.ndarray]:
    color_a, color_b = paint.brush_colors(context)
    scene = context.scene
    assert scene is not None
    if props.dither_transparent(scene):
        return color_a, paint.TRANSPARENT
    return color_a, color_b


def _target_clip(image: bpy.types.Image) -> tuple[select.Rect, np.ndarray | None]:
    if select.session.image_name == image.name and select.session.rect is not None:
        return select.session.rect, select.session.mask
    return (0, 0, image.size[0], image.size[1]), None


class BLIX_OT_dither_gradient(bpy.types.Operator):
    """Drag a dithered gradient between brush colors; Ctrl snaps direction, Esc cancels"""

    bl_idname = "blix.dither_gradient"
    bl_label = "Dither Gradient"
    bl_options = {"REGISTER", "INTERNAL"}

    _start: tuple[float, float]
    _end: tuple[float, float]
    _thresholds: np.ndarray | None
    _taps: patterns.Taps | None

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        if image is None or image.size[0] == 0 or image.size[1] == 0:
            return {"PASS_THROUGH"}
        scene = context.scene
        assert scene is not None
        self._taps = GRADIENT_TAPS[props.dither_gradient(scene)]
        self._thresholds = None
        if self._taps is None:
            self._thresholds = _resolve_pattern(self, scene)
            if self._thresholds is None:
                return {"CANCELLED"}
        region = context.region
        assert region is not None
        self._start = select.mouse_pixel(region, image, event)
        self._end = self._start
        self._update_preview(context, image)
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _buffer(self, context: bpy.types.Context, rect: select.Rect) -> np.ndarray:
        color_a, color_b = _gradient_colors(context)
        return dither_region(
            rect, self._start, self._end, color_a, color_b, self._thresholds, self._taps
        )

    def _update_preview(self, context: bpy.types.Context, image: bpy.types.Image) -> None:
        rect, clip_mask = _target_clip(image)
        buffer = self._buffer(context, rect)
        if clip_mask is not None:
            buffer[~clip_mask[rect[1] : rect[3], rect[0] : rect[2]]] = 0.0
        preview.set(image, rect, buffer)
        overlay.tag_redraw(context)

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        if image is None:
            preview.clear()
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            end = select.mouse_pixel(region, image, event)
            if event.ctrl:
                dx, dy = end[0] - self._start[0], end[1] - self._start[1]
                angle = round(math.atan2(dy, dx) / SNAP_ANGLE) * SNAP_ANGLE
                length = math.hypot(dx, dy)
                end = (
                    self._start[0] + math.cos(angle) * length,
                    self._start[1] + math.sin(angle) * length,
                )
            self._end = end
            self._update_preview(context, image)
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
        rect, clip_mask = _target_clip(image)
        pixels = select.read_pixels(image)
        written = _apply_buffer(pixels, rect, self._buffer(context, rect), clip_mask)
        undo.record(context, image)
        select.write_pixels(image, pixels)
        preview.clear()
        layers.record_write(context, image, written)
        overlay.tag_redraw(context)


class BLIX_OT_dither_stroke(bpy.types.Operator):
    """Paint with a pattern-masked brush; Ctrl paints the secondary color, Esc cancels"""

    bl_idname = "blix.dither_stroke"
    bl_label = "Dither Stroke"
    bl_options = {"REGISTER", "INTERNAL"}

    _snapshot: np.ndarray
    _written: np.ndarray
    _last: tuple[float, float]
    _color: np.ndarray
    _thresholds: np.ndarray

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        if image is None or image.size[0] == 0 or image.size[1] == 0:
            return {"PASS_THROUGH"}
        scene = context.scene
        assert scene is not None
        thresholds = _resolve_pattern(self, scene)
        if thresholds is None:
            return {"CANCELLED"}
        self._thresholds = thresholds
        region = context.region
        assert region is not None
        self._snapshot = select.read_pixels(image).copy()
        self._written = np.zeros(self._snapshot.shape[:2], dtype=bool)
        primary, secondary = paint.brush_colors(context)
        if event.ctrl:
            self._color = paint.TRANSPARENT if props.dither_transparent(scene) else secondary
        else:
            self._color = primary
        self._last = select.mouse_pixel(region, image, event)
        undo.record(context, image)
        self._stamp_to(context, image, self._last)
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _stamp_to(
        self, context: bpy.types.Context, image: bpy.types.Image, point: tuple[float, float]
    ) -> None:
        scene = context.scene
        assert scene is not None
        radius = props.dither_brush_size(scene) / 2
        density = props.dither_density(scene)
        clip, clip_mask = _target_clip(image)
        size = (image.size[0], image.size[1])
        axes = mirror.enabled(scene)
        pixels = select.read_pixels(image)
        distance = math.hypot(point[0] - self._last[0], point[1] - self._last[1])
        steps = max(int(distance / max(radius / 2, 0.5)), 1)
        for step in range(1, steps + 1):
            factor = step / steps
            center = (
                self._last[0] + (point[0] - self._last[0]) * factor,
                self._last[1] + (point[1] - self._last[1]) * factor,
            )
            for stamp in mirror.centers(center, size, *axes):
                dither_stamp(
                    pixels,
                    stamp,
                    radius,
                    self._color,
                    density,
                    self._thresholds,
                    clip,
                    clip_mask,
                    self._written,
                )
        select.write_pixels(image, pixels)
        self._last = point
        overlay.tag_redraw(context)

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        if image is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            self._stamp_to(context, image, select.mouse_pixel(region, image, event))
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            layers.record_write(context, image, self._written)
            overlay.tag_redraw(context)
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            select.write_pixels(image, self._snapshot)
            overlay.tag_redraw(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


class BLIX_TOOL_dither_gradient(bpy.types.WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = "blix.dither_gradient_tool"
    bl_label = "Blix Dither Gradient"
    bl_description = "Dithered gradient between brush colors"
    bl_icon = "ops.paint.weight_gradient"
    bl_widget = None
    bl_keymap = (
        ("blix.dither_gradient", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
        palette.POPUP_KEYMAP_ITEM,
    )


class BLIX_TOOL_dither_brush(bpy.types.WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = "blix.dither_brush_tool"
    bl_label = "Blix Dither Brush"
    bl_description = "Paint with a pattern-masked brush"
    bl_icon = "brush.draw"
    bl_widget = None
    bl_keymap = (
        ("blix.dither_stroke", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
        ("blix.dither_stroke", {"type": "LEFTMOUSE", "value": "PRESS", "ctrl": True}, None),
        palette.POPUP_KEYMAP_ITEM,
    )


_classes = (BLIX_OT_dither_gradient, BLIX_OT_dither_stroke)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_dither_pattern = bpy.props.EnumProperty(
        name="Pattern",
        items=(
            ("BAYER2", "Bayer 2x2", ""),
            ("BAYER4", "Bayer 4x4", ""),
            ("BAYER8", "Bayer 8x8", ""),
            ("BLUE", "Blue Noise", ""),
            None,
            ("LINES_H", "Horizontal Lines", ""),
            ("LINES_V", "Vertical Lines", ""),
            ("DIAGONAL", "Diagonal Lines", ""),
            ("ANTIDIAGONAL", "Antidiagonal Lines", ""),
            None,
            ("DOTS", "Dots", ""),
            ("DOTS_OFFSET", "Offset Dots", ""),
            None,
            ("CUSTOM", "Custom", "Tile from an image; dark pixels paint first"),
        ),
        default="BAYER4",
    )
    scene_cls.blix_dither_custom = bpy.props.PointerProperty(
        type=bpy.types.Image, name="Tile", description="Image used as the custom dither tile"
    )
    scene_cls.blix_dither_gradient = bpy.props.EnumProperty(
        name="Gradient",
        items=(
            ("PATTERN", "Pattern", "Ordered dither with the selected pattern"),
            ("FLOYD_STEINBERG", "Floyd-Steinberg", "Error diffusion"),
            ("ATKINSON", "Atkinson", "Error diffusion, lighter with more contrast"),
        ),
        default="PATTERN",
    )
    scene_cls.blix_dither_density = bpy.props.FloatProperty(
        name="Density", default=0.5, min=0.0, max=1.0
    )
    scene_cls.blix_dither_brush_size = bpy.props.IntProperty(
        name="Brush Size", default=8, min=1, max=256
    )
    scene_cls.blix_dither_transparent = bpy.props.BoolProperty(
        name="Transparent",
        description="Dither to transparency instead of background color",
        default=True,
    )
    bpy.utils.register_tool(BLIX_TOOL_dither_gradient)
    bpy.utils.register_tool(BLIX_TOOL_dither_brush, after="blix.dither_gradient_tool")
    overlay.extra_draws.append(preview.draw)


def unregister() -> None:
    overlay.extra_draws.remove(preview.draw)
    bpy.utils.unregister_tool(BLIX_TOOL_dither_brush)
    bpy.utils.unregister_tool(BLIX_TOOL_dither_gradient)
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_dither_transparent
    del scene_cls.blix_dither_brush_size
    del scene_cls.blix_dither_density
    del scene_cls.blix_dither_gradient
    del scene_cls.blix_dither_custom
    del scene_cls.blix_dither_pattern
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    preview.clear()
