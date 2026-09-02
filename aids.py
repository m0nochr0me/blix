"""Layer painting aids: dim or hatch the layers below, outline the active layer's pixels."""

import math
from typing import Any, cast

import bpy
import gpu
import numpy as np

from . import layers, overlay, prefs, props, select

HATCH_SPACING = 8.0

Rect = tuple[float, float, float, float]


def _redraw(_self: Any, context: bpy.types.Context) -> None:
    overlay.tag_redraw(context)


def _hatch_points(rect: Rect, anchor: float) -> select.Quad:
    """Diagonal lines across rect, spaced in region pixels, anchored to the image origin."""
    x0, y0, x1, y1 = rect
    spacing = HATCH_SPACING * overlay.ui_scale()
    offset = anchor + math.floor((x0 - y1 - anchor) / spacing) * spacing
    points: select.Quad = []
    while offset <= x1 - y0:
        low = max(y0, x0 - offset)
        high = min(y1, x1 - offset)
        if low < high:
            points += [(low + offset, low), (high + offset, high)]
        offset += spacing
    return points


def _draw_layers(
    canvas: bpy.types.Image, layer: Any, corners: select.Quad, stroke: np.ndarray | None
) -> None:
    """Redraw the active layer, its in-progress stroke and the layers above at full brightness."""
    if layer.visible:
        texture = gpu.texture.from_image(layer.image)
        select.draw_texture_quad(corners, texture, False, layer.opacity)
    if stroke is not None:
        select.draw_texture_quad(corners, select.make_texture(stroke), True, layer.opacity)
    stack = list(props.layers(canvas))
    for upper in reversed(stack[: props.layers_index(canvas)]):
        if not upper.visible or upper.image is None:
            continue
        if tuple(upper.image.size) != tuple(canvas.size):
            continue
        texture = gpu.texture.from_image(upper.image)
        select.draw_texture_quad(corners, texture, False, upper.opacity)


def _draw(region: bpy.types.Region, image: bpy.types.Image) -> None:
    scene = bpy.context.scene
    if scene is None or len(props.layers(image)) == 0:
        return
    dim = props.layer_dim(scene)
    hatch = props.layer_hatch(scene)
    outline = props.layer_outline(scene)
    if not (dim or hatch or outline):
        return
    layer = layers.active_layer(image)
    if layer is None or layer.image is None or tuple(layer.image.size) != tuple(image.size):
        return
    affine = select._image_affine(region, image)
    if affine is None:
        return
    width, height = image.size
    sx, tx, sy, ty = affine
    rect: Rect = (tx, ty, width * sx + tx, height * sy + ty)
    stroke = layers.stroke_pixels(image)
    if dim:
        overlay.fill_rects([rect], prefs.color("dim_color"))
    if hatch:
        clipped: Rect = (
            max(rect[0], 0.0),
            max(rect[1], 0.0),
            min(rect[2], float(region.width)),
            min(rect[3], float(region.height)),
        )
        if clipped[0] < clipped[2] and clipped[1] < clipped[3]:
            overlay.draw_lines(_hatch_points(clipped, tx - ty), prefs.color("hatch_color"))
    if dim or hatch:
        corners = [(rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3])]
        _draw_layers(image, layer, corners, stroke)
    if not outline:
        return
    mask = select.read_pixels(layer.image)[:, :, 3] > 0.0
    if stroke is not None:
        mask |= stroke[:, :, 3] > 0.0
    segments = select._segment_points(affine, select.mask_outline(mask))
    overlay.draw_lines(segments, prefs.color("outline_color"))


def register() -> None:
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_layer_dim = bpy.props.BoolProperty(
        name="Dim Below",
        description="Darken the layers below the active layer",
        default=False,
        update=_redraw,
    )
    scene_cls.blix_layer_hatch = bpy.props.BoolProperty(
        name="Hatch Below",
        description="Draw diagonal hatching over the layers below the active layer",
        default=False,
        update=_redraw,
    )
    scene_cls.blix_layer_outline = bpy.props.BoolProperty(
        name="Outline",
        description="Outline the painted pixels of the active layer",
        default=False,
        update=_redraw,
    )
    overlay.under_draws.append(_draw)


def unregister() -> None:
    overlay.under_draws.remove(_draw)
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_layer_outline
    del scene_cls.blix_layer_hatch
    del scene_cls.blix_layer_dim
