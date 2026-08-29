"""Mirror symmetry for Blix drawing tools: reflect writes across the image center axes."""

from typing import Any, cast

import bpy
import numpy as np

from . import overlay, prefs, props

Point = tuple[float, float]


def enabled(scene: bpy.types.Scene) -> tuple[bool, bool]:
    return props.mirror_h(scene), props.mirror_v(scene)


def expand(coverage: np.ndarray, horizontal: bool, vertical: bool) -> np.ndarray:
    """Coverage ORed with its mirrored copies; both axes give four quadrant copies."""
    out = coverage
    if horizontal:
        out = out | out[:, ::-1]
    if vertical:
        out = out | out[::-1]
    return out


def centers(point: Point, size: tuple[int, int], horizontal: bool, vertical: bool) -> list[Point]:
    width, height = size
    points = [point]
    if horizontal:
        points += [(width - x, y) for x, y in points]
    if vertical:
        points += [(x, height - y) for x, y in points]
    return points


def _draw_axes(region: bpy.types.Region, image: bpy.types.Image) -> None:
    scene = bpy.context.scene
    if scene is None:
        return
    horizontal, vertical = enabled(scene)
    if not horizontal and not vertical:
        return
    width, height = image.size
    origin = overlay.image_to_region(region, image, 0.0, 0.0)
    corner = overlay.image_to_region(region, image, width, height)
    points: list[Point] = []
    if horizontal:
        rx, _ = overlay.image_to_region(region, image, width / 2, 0.0)
        points += [(rx, origin[1]), (rx, corner[1])]
    if vertical:
        _, ry = overlay.image_to_region(region, image, 0.0, height / 2)
        points += [(origin[0], ry), (corner[0], ry)]
    overlay.draw_lines(points, prefs.color("mirror_color"))


def register() -> None:
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_mirror_h = bpy.props.BoolProperty(
        name="Mirror H",
        description="Mirror Blix tool strokes left to right across the vertical center axis",
        default=False,
    )
    scene_cls.blix_mirror_v = bpy.props.BoolProperty(
        name="Mirror V",
        description="Mirror Blix tool strokes top to bottom across the horizontal center axis",
        default=False,
    )
    overlay.extra_draws.append(_draw_axes)


def unregister() -> None:
    overlay.extra_draws.remove(_draw_axes)
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_mirror_v
    del scene_cls.blix_mirror_h
