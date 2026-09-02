"""Brush colors from the paint settings, as sRGB-encoded opaque RGBA arrays."""

from typing import Any, cast

import bpy
import numpy as np

from . import select


def srgb_encode(color: np.ndarray) -> np.ndarray:
    return np.where(
        color <= 0.0031308, color * 12.92, 1.055 * np.power(color, 1.0 / 2.4) - 0.055
    ).astype(np.float32)


def srgb_decode(color: np.ndarray) -> np.ndarray:
    return np.where(color <= 0.04045, color / 12.92, np.power((color + 0.055) / 1.055, 2.4)).astype(
        np.float32
    )


def _image_paint(context: bpy.types.Context) -> Any | None:
    tool_settings = context.tool_settings
    return tool_settings.image_paint if tool_settings is not None else None


def _color_owner(context: bpy.types.Context) -> Any | None:
    paint = _image_paint(context)
    if paint is None:
        return None
    unified = cast(Any, paint).unified_paint_settings
    if unified is not None and unified.use_unified_color:
        return unified
    return paint.brush


def brush_colors(context: bpy.types.Context) -> tuple[np.ndarray, np.ndarray]:
    primary = np.array([0.0, 0.0, 0.0], dtype=np.float32)
    secondary = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    owner = _color_owner(context)
    if owner is not None:
        primary = np.array(owner.color, dtype=np.float32)
        secondary = np.array(owner.secondary_color, dtype=np.float32)
    opaque_a = np.ones(4, dtype=np.float32)
    opaque_a[:3] = srgb_encode(primary)
    opaque_b = np.ones(4, dtype=np.float32)
    opaque_b[:3] = srgb_encode(secondary)
    return opaque_a, opaque_b


class StandIn:
    """Brush color nudged one sRGB step so a stroke over matching composite pixels still diffs."""

    def __init__(self, color: list[float], painted: np.ndarray, wanted: np.ndarray) -> None:
        self.color = color
        self.painted = painted
        self.wanted = wanted
        self.mask: np.ndarray | None = None


def stand_in(context: bpy.types.Context, pixels: np.ndarray) -> StandIn | None:
    """Nudge the brush to a byte color absent from pixels; None when every neighbor is present."""
    paint = _image_paint(context)
    brush = paint.brush if paint is not None else None
    owner = _color_owner(context)
    if brush is None or owner is None or _paints_at_release(brush):
        return None
    color = list(owner.color)
    wanted = np.rint(srgb_encode(np.array(color, dtype=np.float32)) * 255.0).astype(np.int32)
    present = np.rint(pixels[:, :, :3] * 255.0).astype(np.int32)
    for channel in range(3):
        for step in (1, -1):
            painted = wanted.copy()
            painted[channel] += step
            if painted[channel] < 0 or painted[channel] > 255:
                continue
            if np.all(present == painted, axis=2).any():
                continue
            owner.color = srgb_decode(painted.astype(np.float32) / 255.0).tolist()
            return StandIn(color, painted, wanted)
    return None


def restore_color(context: bpy.types.Context, stand_in: StandIn) -> None:
    owner = _color_owner(context)
    if owner is not None:
        owner.color = stand_in.color


def _paints_at_release(brush: bpy.types.Brush) -> bool:
    """Strokes applied on mouse release, after the stand-in swap hook, cannot use a stand-in."""
    return brush.image_brush_type == "FILL" or brush.stroke_method in {"LINE", "CURVE"}


def fill_mask(
    context: bpy.types.Context, image: bpy.types.Image, event: bpy.types.Event
) -> np.ndarray | None:
    """Pixels the native bucket fill floods from the press point, replicating Blender's flood."""
    paint = _image_paint(context)
    brush = paint.brush if paint is not None else None
    region = context.region
    if brush is None or region is None or brush.image_brush_type != "FILL":
        return None
    if brush.color_type != "COLOR":
        return None
    x, y = select.mouse_pixel(region, image, event)
    width, height = image.size
    if not (0 <= x < width and 0 <= y < height):
        return None
    seed = (int(x), int(y))
    colors = select.read_pixels(image)
    if not image.is_float:
        colors[:, :, :3] *= colors[:, :, 3:4]
    delta = colors - colors[seed[1], seed[0]]
    threshold = np.float32(brush.fill_threshold)
    threshold_sq = threshold * threshold * np.float32(3)
    within = np.sum(delta * delta, axis=2, dtype=np.float32) <= threshold_sq
    mask = select.flood_mask(within, seed, diagonal=True)
    if select.session.image_name == image.name and select.session.mask is not None:
        mask &= select.session.mask
    return mask
