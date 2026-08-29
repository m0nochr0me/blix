"""Brush colors from the paint settings, as sRGB-encoded opaque RGBA arrays."""

from typing import Any, cast

import bpy
import numpy as np


def srgb_encode(color: np.ndarray) -> np.ndarray:
    return np.where(
        color <= 0.0031308, color * 12.92, 1.055 * np.power(color, 1.0 / 2.4) - 0.055
    ).astype(np.float32)


def srgb_decode(color: np.ndarray) -> np.ndarray:
    return np.where(color <= 0.04045, color / 12.92, np.power((color + 0.055) / 1.055, 2.4)).astype(
        np.float32
    )


def brush_colors(context: bpy.types.Context) -> tuple[np.ndarray, np.ndarray]:
    primary = np.array([0.0, 0.0, 0.0], dtype=np.float32)
    secondary = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    tool_settings = context.tool_settings
    paint = tool_settings.image_paint if tool_settings is not None else None
    if paint is not None:
        brush = paint.brush
        unified = cast(Any, paint).unified_paint_settings
        if unified is not None and unified.use_unified_color:
            primary = np.array(unified.color, dtype=np.float32)
            secondary = np.array(unified.secondary_color, dtype=np.float32)
        elif brush is not None:
            primary = np.array(brush.color, dtype=np.float32)
            secondary = np.array(brush.secondary_color, dtype=np.float32)
    opaque_a = np.ones(4, dtype=np.float32)
    opaque_a[:3] = srgb_encode(primary)
    opaque_b = np.ones(4, dtype=np.float32)
    opaque_b[:3] = srgb_encode(secondary)
    return opaque_a, opaque_b
