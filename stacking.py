"""Sprite stacking: axonometric stack preview overlay and horizontal-strip export."""

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import bpy
import gpu
import numpy as np
from bpy_extras.io_utils import ExportHelper
from mathutils import Matrix

from . import layers, overlay, props, select

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

PROJECTION_ITEMS = (
    ("ISOMETRIC", "Isometric", "True isometric, 35.26 degree elevation"),
    ("DIMETRIC", "Dimetric", "Classic 2:1 pixel-art dimetric, 30 degree elevation"),
    ("TRIMETRIC", "Trimetric", "20 degree elevation with a 30 degree yaw offset"),
)
_PROJECTIONS: dict[str, tuple[float, float]] = {
    "ISOMETRIC": (35.264, 0.0),
    "DIMETRIC": (30.0, 0.0),
    "TRIMETRIC": (20.0, 30.0),
}
Slice = tuple[gpu.types.GPUTexture, select.Quad]
Target = tuple[gpu.types.GPUTexture, gpu.types.GPUFrameBuffer]
_target: Target | None = None


def _redraw(_self: Any, context: bpy.types.Context) -> None:
    overlay.tag_redraw(context)


def stack_layers(canvas: bpy.types.Image) -> list[Any]:
    width, height = canvas.size
    return [
        layer
        for layer in reversed(list(props.layers(canvas)))
        if layer.visible and layer.image is not None and tuple(layer.image.size) == (width, height)
    ]


def total_units(canvas: bpy.types.Image) -> int:
    return sum(layer.height for layer in stack_layers(canvas))


def _slice_corners(
    size: tuple[int, int],
    theta: float,
    sin_e: float,
    cos_e: float,
    z: float,
    anchor: tuple[float, float],
) -> select.Quad:
    width, height = size
    cx, cy = width / 2, height / 2
    ax, ay = anchor
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    corners: select.Quad = []
    for x, y in ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height)):
        rx = (x - cx) * cos_t - (y - cy) * sin_t
        ry = (x - cx) * sin_t + (y - cy) * cos_t
        corners.append((ax + rx, ay + ry * sin_e + z * cos_e))
    return corners


def _slices(canvas: bpy.types.Image, included: list[Any], scene: bpy.types.Scene) -> list[Slice]:
    width, height = canvas.size
    elevation, yaw = _PROJECTIONS[props.stack_projection(scene)]
    theta = math.radians(props.stack_angle(scene) + yaw)
    sin_e = math.sin(math.radians(elevation))
    cos_e = math.cos(math.radians(elevation))
    total = sum(layer.height for layer in included)
    radius = math.hypot(width, height) / 2
    anchor = (1.25 * width + radius, height / 2 - (total - 1) * cos_e / 2)
    slices: list[Slice] = []
    z = 0
    for layer in included:
        texture = gpu.texture.from_image(layer.image)
        for _ in range(layer.height):
            corners = _slice_corners((width, height), theta, sin_e, cos_e, float(z), anchor)
            slices.append((texture, corners))
            z += 1
    return slices


def _to_region(affine: select.Affine, corners: select.Quad) -> select.Quad:
    sx, tx, sy, ty = affine
    return [(x * sx + tx, y * sy + ty) for x, y in corners]


def _target_for(width: int, height: int) -> Target:
    global _target
    if _target is None or (_target[0].width, _target[0].height) != (width, height):
        texture = gpu.types.GPUTexture((width, height), format="RGBA16F")
        _target = (texture, gpu.types.GPUFrameBuffer(color_slots=texture))
    return _target


def _draw_pixelated(slices: list[Slice], affine: select.Affine, resolution: float) -> None:
    """Rasterize slices at `resolution` texels per canvas pixel, then blit with nearest sampling."""
    cell = 1 / resolution
    xs = [x for _, corners in slices for x, _ in corners]
    ys = [y for _, corners in slices for _, y in corners]
    x0, y0 = math.floor(min(xs) / cell) * cell, math.floor(min(ys) / cell) * cell
    width, height = math.ceil((max(xs) - x0) / cell), math.ceil((max(ys) - y0) / cell)
    x1, y1 = x0 + width * cell, y0 + height * cell
    texture, framebuffer = _target_for(width, height)
    projection = Matrix(
        (
            (2 / (x1 - x0), 0, 0, -1 - 2 * x0 / (x1 - x0)),
            (0, 2 / (y1 - y0), 0, -1 - 2 * y0 / (y1 - y0)),
            (0, 0, -1, 0),
            (0, 0, 0, 1),
        )
    )
    depth_test = gpu.state.depth_test_get()
    gpu.state.depth_test_set("NONE")
    matrix_stack = cast(Any, gpu.matrix)
    with cast(Any, framebuffer.bind()):
        framebuffer.clear(color=(0.0, 0.0, 0.0, 0.0))
        with matrix_stack.push_pop(), matrix_stack.push_pop_projection():
            gpu.matrix.load_identity()
            gpu.matrix.load_projection_matrix(projection)
            for slice_texture, corners in slices:
                select.draw_texture_quad(corners, slice_texture, False)
    gpu.state.depth_test_set(cast(Any, depth_test))
    quad = _to_region(affine, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    gpu.state.blend_set("ALPHA_PREMULT")
    select.draw_texture_quad(quad, texture, False)
    gpu.state.blend_set("ALPHA")


def _draw_stack(region: bpy.types.Region, image: bpy.types.Image) -> None:
    scene = bpy.context.scene
    if scene is None or not props.stack_preview(scene):
        return
    canvas = image if len(props.layers(image)) else props.canvas_of(image)
    if canvas is None or tuple(image.size) != tuple(canvas.size):
        return
    included = stack_layers(canvas)
    if not included:
        return
    affine = select._image_affine(region, image)
    if affine is None:
        return
    slices = _slices(canvas, included, scene)
    if props.stack_pixelate(scene):
        _draw_pixelated(slices, affine, props.stack_resolution(scene))
        return
    for texture, corners in slices:
        select.draw_texture_quad(_to_region(affine, corners), texture, False)


def build_strip(canvas: bpy.types.Image) -> np.ndarray | None:
    included = stack_layers(canvas)
    if not included:
        return None
    width, height = canvas.size
    total = sum(layer.height for layer in included)
    strip = np.zeros((height, width * total, 4), dtype=np.float32)
    cell = 0
    for layer in included:
        pixels = select.read_pixels(layer.image)
        for _ in range(layer.height):
            strip[:, cell * width : (cell + 1) * width] = pixels
            cell += 1
    return strip


def save_strip(strip: np.ndarray, filepath: str) -> None:
    height, width = strip.shape[:2]
    image = bpy.data.images.new(".blix_stack_export", width, height, alpha=True)
    try:
        select.write_pixels(image, strip)
        image.filepath_raw = filepath
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


class BLIX_OT_stack_export(bpy.types.Operator, ExportHelper):
    """Export visible layers as a horizontal sprite-stack strip, bottom slice first"""

    bl_idname = "blix.stack_export"
    bl_label = "Export Sprite Stack"
    bl_options = {"REGISTER"}

    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(default="*.png", options={"HIDDEN"})

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return layers.resolve_canvas(context) is not None

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        canvas = layers.resolve_canvas(context)
        assert canvas is not None
        cast(Any, self).filepath = f"{canvas.name}_stack.png"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = layers.resolve_canvas(context)
        assert canvas is not None
        layers.sync_canvas(canvas)
        strip = build_strip(canvas)
        if strip is None:
            self.report({"ERROR"}, "No visible layers")
            return {"CANCELLED"}
        filepath = cast(Any, self).filepath
        try:
            save_strip(strip, filepath)
        except (RuntimeError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        slices = strip.shape[1] // canvas.size[0]
        self.report({"INFO"}, f"{slices} slices to {Path(filepath).name}")
        return {"FINISHED"}


_classes = (BLIX_OT_stack_export,)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_stack_preview = bpy.props.BoolProperty(
        name="Preview",
        description="Draw the layer stack beside the canvas in the chosen projection",
        default=False,
        update=_redraw,
    )
    scene_cls.blix_stack_pixelate = bpy.props.BoolProperty(
        name="Pixelate",
        description="Rasterize the stack preview at canvas pixel resolution",
        default=False,
        update=_redraw,
    )
    scene_cls.blix_stack_resolution = bpy.props.FloatProperty(
        name="Resolution",
        description="Texels per canvas pixel in the pixelated preview, 1 matches the canvas",
        default=1.0,
        min=0.1,
        max=4.0,
        step=10,
        precision=2,
        update=_redraw,
    )
    scene_cls.blix_stack_projection = bpy.props.EnumProperty(
        name="Projection", items=PROJECTION_ITEMS, default="DIMETRIC", update=_redraw
    )
    scene_cls.blix_stack_angle = bpy.props.FloatProperty(
        name="Angle",
        description="Stack view rotation in degrees",
        default=45.0,
        min=0.0,
        max=360.0,
        update=_redraw,
    )
    overlay.extra_draws.append(_draw_stack)


def unregister() -> None:
    global _target
    overlay.extra_draws.remove(_draw_stack)
    _target = None
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_stack_angle
    del scene_cls.blix_stack_projection
    del scene_cls.blix_stack_resolution
    del scene_cls.blix_stack_pixelate
    del scene_cls.blix_stack_preview
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
