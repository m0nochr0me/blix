"""POST_PIXEL overlay drawing for image editor: rulers and guides."""

import math

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

RULER_PX = 20
FONT_ID = 0
RULER_BG = (0.10, 0.10, 0.10, 0.92)
TICK_COLOR = (0.65, 0.65, 0.65, 1.0)
LABEL_COLOR = (0.75, 0.75, 0.75, 1.0)
GUIDE_COLOR = (0.15, 0.55, 1.0, 0.85)
_STEPS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000)

_handler = None


def ui_scale() -> float:
    return bpy.context.preferences.system.ui_scale


def ruler_px() -> int:
    return round(RULER_PX * ui_scale())


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
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                area.tag_redraw()


def _fill_rects(
    rects: list[tuple[float, float, float, float]],
    color: tuple[float, float, float, float],
) -> None:
    verts: list[tuple[float, float]] = []
    for x0, y0, x1, y1 in rects:
        verts += [(x0, y0), (x1, y0), (x0, y1), (x1, y0), (x1, y1), (x0, y1)]
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.uniform_float("color", color)
    batch_for_shader(shader, 'TRIS', {"pos": verts}).draw(shader)


def _draw_lines(
    points: list[tuple[float, float]], color: tuple[float, float, float, float]
) -> None:
    if not points:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.uniform_float("color", color)
    batch_for_shader(shader, 'LINES', {"pos": points}).draw(shader)


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


def _draw_guides(region: bpy.types.Region, image: bpy.types.Image) -> None:
    points: list[tuple[float, float]] = []
    for guide in image.blix_guides:
        if guide.orientation == 'VERTICAL':
            rx, _ = image_to_region(region, image, guide.position, 0.0)
            points += [(rx, 0.0), (rx, region.height)]
        else:
            _, ry = image_to_region(region, image, 0.0, guide.position)
            points += [(0.0, ry), (region.width, ry)]
    _draw_lines(points, GUIDE_COLOR)


def _draw_rulers(region: bpy.types.Region, image: bpy.types.Image) -> None:
    band = ruler_px()
    rw, rh = region.width, region.height
    _fill_rects([(0, rh - band, rw, rh), (0, 0, band, rh - band)], RULER_BG)

    origin = image_to_region(region, image, 0.0, 0.0)
    corner = image_to_region(region, image, image.size[0], image.size[1])
    ppp_x = max((corner[0] - origin[0]) / image.size[0], 1e-6)
    ppp_y = max((corner[1] - origin[1]) / image.size[1], 1e-6)
    left, bottom = region_to_image(region, image, 0.0, 0.0)
    right, top = region_to_image(region, image, rw, rh)

    scale = ui_scale()
    blf.size(FONT_ID, round(9 * scale))
    blf.color(FONT_ID, *LABEL_COLOR)
    ticks: list[tuple[float, float]] = []

    step = _step_for(ppp_x)
    minor = _minor_for(step, ppp_x)
    for value in range(math.floor(left / minor) * minor, int(right) + minor, minor):
        rx, _ = image_to_region(region, image, value, 0.0)
        if rx < band:
            continue
        major = value % step == 0
        ticks += [(rx, rh - band), (rx, rh - band + band * (0.55 if major else 0.28))]
        if major:
            blf.position(FONT_ID, rx + 3 * scale, rh - band * 0.38, 0)
            blf.draw(FONT_ID, str(value))

    step = _step_for(ppp_y)
    minor = _minor_for(step, ppp_y)
    blf.enable(FONT_ID, blf.ROTATION)
    blf.rotation(FONT_ID, math.pi / 2)
    for value in range(math.floor(bottom / minor) * minor, int(top) + minor, minor):
        _, ry = image_to_region(region, image, 0.0, value)
        if ry > rh - band:
            continue
        major = value % step == 0
        ticks += [(band, ry), (band - band * (0.55 if major else 0.28), ry)]
        if major:
            blf.position(FONT_ID, band * 0.38, ry + 3 * scale, 0)
            blf.draw(FONT_ID, str(value))
    blf.disable(FONT_ID, blf.ROTATION)

    _draw_lines(ticks, TICK_COLOR)


def _draw() -> None:
    context = bpy.context
    image = getattr(context.space_data, "image", None)
    if image is None or image.size[0] == 0 or image.size[1] == 0:
        return
    region = context.region
    scene = context.scene
    gpu.state.blend_set('ALPHA')
    if scene.blix_show_guides:
        _draw_guides(region, image)
    if scene.blix_show_rulers:
        _draw_rulers(region, image)
    gpu.state.blend_set('NONE')


def register() -> None:
    global _handler
    bpy.types.Scene.blix_show_rulers = bpy.props.BoolProperty(name="Rulers", default=True)
    bpy.types.Scene.blix_show_guides = bpy.props.BoolProperty(name="Guides", default=True)
    _handler = bpy.types.SpaceImageEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')


def unregister() -> None:
    global _handler
    if _handler is not None:
        bpy.types.SpaceImageEditor.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    del bpy.types.Scene.blix_show_guides
    del bpy.types.Scene.blix_show_rulers
