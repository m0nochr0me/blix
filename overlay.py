"""POST_PIXEL overlay drawing for image editor: rulers and guides."""

import math
from collections.abc import Callable
from typing import Any, cast

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import prefs, props

FONT_ID = 0
PIXEL_GRID_ALPHA = 0.25
_STEPS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000)

ExtraDraw = Callable[[bpy.types.Region, bpy.types.Image], None]
under_draws: list[ExtraDraw] = []
extra_draws: list[ExtraDraw] = []

_handler = None


def ui_scale() -> float:
    preferences = bpy.context.preferences
    assert preferences is not None
    return preferences.system.ui_scale


def ruler_px() -> int:
    return round(prefs.ruler_size() * ui_scale())


def ruler_offsets(area: bpy.types.Area | None, region: bpy.types.Region) -> tuple[int, int]:
    left = 0
    top = 0
    if area is None:
        return left, top
    for other in area.regions:
        if other.type not in {"TOOLS", "HEADER", "TOOL_HEADER"}:
            continue
        if other.width <= 1 or other.height <= 1:
            continue
        outside = (
            other.x >= region.x + region.width
            or other.x + other.width <= region.x
            or other.y >= region.y + region.height
            or other.y + other.height <= region.y
        )
        if outside:
            continue
        if other.type == "TOOLS":
            left = max(left, other.x + other.width - region.x)
        elif other.y > region.y + region.height // 2:
            top = max(top, region.y + region.height - other.y)
    return left, top


def image_to_region(
    region: bpy.types.Region, image: bpy.types.Image, x: float, y: float
) -> tuple[int, int]:
    width, height = image.size
    return region.view2d.view_to_region(x / width, y / height, clip=False)


def region_to_image(
    region: bpy.types.Region, image: bpy.types.Image, rx: float, ry: float
) -> tuple[float, float]:
    u, v = region.view2d.region_to_view(rx, ry)
    return u * image.size[0], v * image.size[1]


def tag_redraw(context: bpy.types.Context) -> None:
    window_manager = context.window_manager
    assert window_manager is not None
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.tag_redraw()


def fill_rects(
    rects: list[tuple[float, float, float, float]],
    color: tuple[float, float, float, float],
) -> None:
    verts: list[tuple[float, float]] = []
    for x0, y0, x1, y1 in rects:
        verts += [(x0, y0), (x1, y0), (x0, y1), (x1, y0), (x1, y1), (x0, y1)]
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": verts}).draw(shader)


def clip_line(
    px: float, py: float, dx: float, dy: float, width: float, height: float
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    t_min, t_max = -math.inf, math.inf
    for start, delta, extent in ((px, dx, width), (py, dy, height)):
        if abs(delta) < 1e-9:
            if start < 0 or start > extent:
                return None
            continue
        t0, t1 = -start / delta, (extent - start) / delta
        t_min = max(t_min, min(t0, t1))
        t_max = min(t_max, max(t0, t1))
    if t_min > t_max:
        return None
    return (px + dx * t_min, py + dy * t_min), (px + dx * t_max, py + dy * t_max)


def draw_lines(points: list[tuple[float, float]], color: tuple[float, float, float, float]) -> None:
    if not points:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.uniform_float("color", color)
    batch_for_shader(shader, "LINES", {"pos": points}).draw(shader)


def _step_for(px_per_pixel: float) -> int:
    target = 60.0 * ui_scale()
    for step in _STEPS:
        if step * px_per_pixel >= target:
            return step
    return _STEPS[-1]


def _minor_for(step: int, px_per_pixel: float) -> int:
    minor = step // 5 if step >= 5 else max(step // 2, 1)
    if minor == 0 or minor * px_per_pixel < 4.0 * ui_scale():
        return step
    return minor


def _grid_points(
    region: bpy.types.Region, image: bpy.types.Image, spacing: int
) -> list[tuple[float, float]]:
    width, height = image.size
    origin = image_to_region(region, image, 0.0, 0.0)
    corner = image_to_region(region, image, width, height)
    left, bottom = region_to_image(region, image, 0.0, 0.0)
    right, top = region_to_image(region, image, region.width, region.height)
    points: list[tuple[float, float]] = []
    first = max(0, math.floor(left / spacing) * spacing)
    for value in range(first, min(width, math.ceil(right)) + 1, spacing):
        rx, _ = image_to_region(region, image, value, 0.0)
        points += [(rx, origin[1]), (rx, corner[1])]
    first = max(0, math.floor((height - top) / spacing) * spacing)
    for value in range(first, min(height, math.ceil(height - bottom)) + 1, spacing):
        _, ry = image_to_region(region, image, 0.0, height - value)
        points += [(origin[0], ry), (corner[0], ry)]
    return points


def _draw_grid(region: bpy.types.Region, image: bpy.types.Image, scene: bpy.types.Scene) -> None:
    origin = image_to_region(region, image, 0.0, 0.0)
    corner = image_to_region(region, image, image.size[0], image.size[1])
    ppp = (corner[0] - origin[0]) / image.size[0]
    scale = ui_scale()
    grid_color = prefs.color("grid_color")
    if props.show_pixel_grid(scene):
        fade = min((ppp - 4.0 * scale) / (4.0 * scale), 1.0)
        if fade > 0.0:
            color = (*grid_color[:3], PIXEL_GRID_ALPHA * fade)
            draw_lines(_grid_points(region, image, 1), color)
    divisions = props.grid_divisions(scene)
    if divisions > 0 and divisions * ppp >= 8.0 * scale:
        draw_lines(_grid_points(region, image, divisions), grid_color)


def _draw_guides(region: bpy.types.Region, image: bpy.types.Image) -> None:
    points: list[tuple[float, float]] = []
    height = image.size[1]
    for guide in props.guides(image):
        if guide.orientation == "VERTICAL":
            rx, _ = image_to_region(region, image, guide.position, 0.0)
            points += [(rx, 0.0), (rx, region.height)]
        elif guide.orientation == "HORIZONTAL":
            _, ry = image_to_region(region, image, 0.0, height - guide.position)
            points += [(0.0, ry), (region.width, ry)]
        else:
            rx, ry = image_to_region(region, image, guide.position, height - guide.position_y)
            segment = clip_line(
                rx, ry, math.cos(guide.angle), math.sin(guide.angle), region.width, region.height
            )
            if segment is not None:
                points += segment
    draw_lines(points, prefs.color("guide_color"))


def _draw_rulers(
    region: bpy.types.Region, image: bpy.types.Image, offsets: tuple[int, int]
) -> None:
    band = ruler_px()
    left_off, top_off = offsets
    rw, rh = region.width, region.height
    band_top = rh - top_off
    band_base = band_top - band
    band_right = left_off + band
    fill_rects(
        [(left_off, band_base, rw, band_top), (left_off, 0, band_right, band_base)],
        prefs.color("ruler_background"),
    )

    origin = image_to_region(region, image, 0.0, 0.0)
    corner = image_to_region(region, image, image.size[0], image.size[1])
    ppp_x = max((corner[0] - origin[0]) / image.size[0], 1e-6)
    ppp_y = max((corner[1] - origin[1]) / image.size[1], 1e-6)
    left, bottom = region_to_image(region, image, 0.0, 0.0)
    right, top = region_to_image(region, image, rw, rh)
    height = image.size[1]

    scale = ui_scale()
    text_color = prefs.color("ruler_text")
    blf.size(FONT_ID, round(9 * scale))
    blf.color(FONT_ID, *text_color)
    ticks: list[tuple[float, float]] = []

    step = _step_for(ppp_x)
    minor = _minor_for(step, ppp_x)
    for value in range(math.floor(left / minor) * minor, int(right) + minor, minor):
        rx, _ = image_to_region(region, image, value, 0.0)
        if rx < band_right:
            continue
        major = value % step == 0
        ticks += [(rx, band_base), (rx, band_base + band * (0.55 if major else 0.28))]
        if major:
            blf.position(FONT_ID, rx + 3 * scale, band_top - band * 0.38, 0)
            blf.draw(FONT_ID, str(value))

    step = _step_for(ppp_y)
    minor = _minor_for(step, ppp_y)
    blf.enable(FONT_ID, blf.ROTATION)
    blf.rotation(FONT_ID, math.pi / 2)
    first = math.floor((height - top) / minor) * minor
    for value in range(first, int(height - bottom) + minor, minor):
        _, ry = image_to_region(region, image, 0.0, height - value)
        if ry > band_base:
            continue
        major = value % step == 0
        ticks += [(band_right, ry), (band_right - band * (0.55 if major else 0.28), ry)]
        if major:
            blf.position(FONT_ID, left_off + band * 0.38, ry + 3 * scale, 0)
            blf.draw(FONT_ID, str(value))
    blf.disable(FONT_ID, blf.ROTATION)

    draw_lines(ticks, text_color)


def _draw() -> None:
    context = bpy.context
    image = getattr(context.space_data, "image", None)
    region = context.region
    scene = context.scene
    if image is None or region is None or scene is None:
        return
    if image.size[0] == 0 or image.size[1] == 0:
        return
    gpu.state.blend_set("ALPHA")
    for draw_fn in under_draws:
        draw_fn(region, image)
    _draw_grid(region, image, scene)
    if props.show_guides(scene):
        _draw_guides(region, image)
    for draw_fn in extra_draws:
        draw_fn(region, image)
    if props.show_rulers(scene):
        _draw_rulers(region, image, ruler_offsets(context.area, region))
    gpu.state.blend_set("NONE")


def _disable_native_grid() -> None:
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type != "IMAGE_EDITOR":
                continue
            for space in area.spaces:
                if space.type == "IMAGE_EDITOR":
                    editor = cast(bpy.types.SpaceImageEditor, space)
                    editor.uv_editor.show_grid_over_image = False


def _pixel_grid_update(self: Any, context: bpy.types.Context) -> None:
    if self.blix_show_pixel_grid:
        _disable_native_grid()


def _sync_grid_once() -> None:
    _disable_native_grid()


def register() -> None:
    global _handler
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_show_rulers = bpy.props.BoolProperty(name="Rulers", default=True)
    scene_cls.blix_show_guides = bpy.props.BoolProperty(name="Guides", default=True)
    scene_cls.blix_show_pixel_grid = bpy.props.BoolProperty(
        name="Pixel Grid",
        description="Draw grid line on every pixel boundary",
        default=True,
        update=_pixel_grid_update,
    )
    scene_cls.blix_grid_divisions = bpy.props.IntProperty(
        name="Divisions",
        description="Pixels between Blix major grid lines, 0 disables",
        default=10,
        min=0,
        soft_max=64,
    )
    bpy.app.timers.register(_sync_grid_once)
    _handler = bpy.types.SpaceImageEditor.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")


def unregister() -> None:
    global _handler
    if _handler is not None:
        bpy.types.SpaceImageEditor.draw_handler_remove(_handler, "WINDOW")
        _handler = None
    if bpy.app.timers.is_registered(_sync_grid_once):
        bpy.app.timers.unregister(_sync_grid_once)
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_grid_divisions
    del scene_cls.blix_show_pixel_grid
    del scene_cls.blix_show_guides
    del scene_cls.blix_show_rulers
