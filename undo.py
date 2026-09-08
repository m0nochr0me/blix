"""Direct pixel writes registered with Blender's image undo stack."""

from typing import Any, cast

import bpy

SETTLE = 0.2

_pending: set[str] = set()
_stale: set[str] = set()


def record(context: bpy.types.Context, image: bpy.types.Image) -> None:
    """Store image pixels as an undo step, attributing pending canvas changes first."""
    from . import layers

    _pending.discard(image.name)
    _stale.discard(image.name)
    if len(layers.props.layers(image)):
        layers.sync_canvas(image)
    layers.push_history(image)
    with context.temp_override(edit_image=image):
        cast(Any, bpy.ops.image).invert()


def defer(image: bpy.types.Image) -> None:
    """Record once writes settle, so a slider-driven burst of recomposites pushes one step."""
    _pending.add(image.name)
    _stale.add(image.name)
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    bpy.app.timers.register(_flush, first_interval=SETTLE)


def forget(image: bpy.types.Image) -> None:
    """Drop a pending deferred record; the caller pushes its own undo step instead."""
    _pending.discard(image.name)


def mark(image: bpy.types.Image) -> None:
    """Flag pixels written with no image step, so the next native stroke records first."""
    _stale.add(image.name)


def stale(image: bpy.types.Image) -> bool:
    return image.name in _stale


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
    _stale.clear()


def unregister() -> None:
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    _pending.clear()
    _stale.clear()
