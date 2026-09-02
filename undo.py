"""Direct pixel writes registered with Blender's image undo stack."""

from typing import Any, cast

import bpy
import numpy as np

SETTLE = 0.2

_pending: set[str] = set()


def record(
    context: bpy.types.Context, image: bpy.types.Image, written: np.ndarray | None = None
) -> None:
    """Store image pixels as an undo step; written marks pixels a write may have left unchanged."""
    from . import layers

    _pending.discard(image.name)
    if len(layers.props.layers(image)):
        layers.sync_canvas(image, written)
    layers.push_history(image)
    with context.temp_override(edit_image=image):
        cast(Any, bpy.ops.image).invert()


def defer(image: bpy.types.Image) -> None:
    """Record once writes settle, so a slider-driven burst of recomposites pushes one step."""
    _pending.add(image.name)
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    bpy.app.timers.register(_flush, first_interval=SETTLE)


def _flush() -> float | None:
    window = bpy.context.window
    if window is not None and len(window.modal_operators):
        return SETTLE
    for name in sorted(_pending):
        image = bpy.data.images.get(name)
        if image is not None:
            record(bpy.context, image)
    _pending.clear()
    return None


def register() -> None:
    _pending.clear()


def unregister() -> None:
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    _pending.clear()
