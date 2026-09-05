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
_noise_shader: gpu.types.GPUShader | None = None


def _redraw(_self: Any, context: bpy.types.Context) -> None:
    overlay.tag_redraw(context)


def stack_layers(canvas: bpy.types.Image) -> list[Any]:
    width, height = canvas.size
    return [
        layer
        for layer in reversed(list(props.layers(canvas)))
        if layers.shown(canvas, layer)
        and layer.image is not None
        and tuple(layer.image.size) == (width, height)
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
    scale = props.stack_scale(scene)
    size = (width * scale, height * scale)
    elevation, yaw = _PROJECTIONS[props.stack_projection(scene)]
    theta = math.radians(props.stack_angle(scene) + yaw)
    sin_e = math.sin(math.radians(elevation))
    cos_e = math.cos(math.radians(elevation))
    total = sum(layer.height for layer in included)
    step = 1 if props.stack_scale_layers(scene) else scale
    radius = math.hypot(*size) / 2
    anchor = (1.25 * width + radius, height / 2 - (total - 1) * step * cos_e / 2)
    slices: list[Slice] = []
    z = 0
    for layer in included:
        texture = gpu.texture.from_image(layer.image)
        for _ in range(layer.height):
            corners = _slice_corners(size, theta, sin_e, cos_e, float(z * step), anchor)
            slices.append((texture, corners))
            z += 1
    return slices


def _to_region(affine: select.Affine, corners: select.Quad) -> select.Quad:
    sx, tx, sy, ty = affine
    return [(x * sx + tx, y * sy + ty) for x, y in corners]


def _hash(x: np.ndarray) -> np.ndarray:
    x = x ^ (x >> 16)
    x = x * np.uint32(0x7FEB352D)
    x = x ^ (x >> 15)
    x = x * np.uint32(0x846CA68B)
    return x ^ (x >> 16)


def noise(size: tuple[int, int], z: int, grain: int) -> np.ndarray:
    """Uniform noise in [-1, 1] per grain cell of slice z, same hash as the preview shader."""
    width, height = size
    ys = (np.arange(height, dtype=np.uint32) // grain)[:, None]
    xs = (np.arange(width, dtype=np.uint32) // grain)[None, :]
    seed = _hash(np.array([z], dtype=np.uint32))
    h = _hash(_hash(seed + ys) + xs)
    return (h >> 8).astype(np.float32) / np.float32(16777215.0) * 2 - 1


def apply_noise(pixels: np.ndarray, z: int, strength: float, grain: int) -> np.ndarray:
    height, width = pixels.shape[:2]
    delta = noise((width, height), z, grain) * strength * (pixels[..., 3] > 0)
    out = pixels.copy()
    out[..., :3] = np.clip(pixels[..., :3] + delta[..., None], 0, 1)
    return out


def _get_noise_shader() -> gpu.types.GPUShader:
    global _noise_shader
    if _noise_shader is not None:
        return _noise_shader
    iface = gpu.types.GPUStageInterfaceInfo("blix_stack_noise_iface")
    iface.smooth("VEC2", "uv_interp")
    info = gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0, "VEC2", "pos")
    info.vertex_in(1, "VEC2", "uv")
    info.vertex_out(iface)
    info.sampler(0, "FLOAT_2D", "image")
    info.push_constant("MAT4", "ModelViewProjectionMatrix")
    info.push_constant("FLOAT", "strength")
    info.push_constant("INT", "grain")
    info.push_constant("INT", "slice_index")
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source(
        "void main()"
        "{gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0); uv_interp = uv;}"
    )
    info.fragment_source(
        "vec3 srgb_to_linear(vec3 c)"
        "{return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c));}"
        "vec3 linear_to_srgb(vec3 c)"
        "{return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c));}"
        "uint hash(uint x)"
        "{x ^= x >> 16u; x *= 0x7feb352du; x ^= x >> 15u; x *= 0x846ca68bu; return x ^ (x >> 16u);}"
        "void main()"
        "{ivec2 size = textureSize(image, 0);"
        "ivec2 texel = ivec2(clamp(uv_interp, 0.0, 0.99999) * vec2(size));"
        "vec4 color = texelFetch(image, texel, 0);"
        "if (color.a <= 0.0) discard;"
        "uvec2 cell = uvec2(texel / grain);"
        "uint h = hash(hash(hash(uint(slice_index)) + cell.y) + cell.x);"
        "float delta = (float(h >> 8u) / 16777215.0 * 2.0 - 1.0) * strength;"
        "vec3 rgb = clamp(linear_to_srgb(color.rgb) + delta, 0.0, 1.0);"
        "fragColor = vec4(srgb_to_linear(rgb), color.a);}"
    )
    _noise_shader = gpu.shader.create_from_info(info)
    return _noise_shader


def _draw_slice(
    corners: select.Quad, texture: gpu.types.GPUTexture, z: int, scene: bpy.types.Scene
) -> None:
    strength = props.stack_noise(scene)
    if strength <= 0:
        select.draw_texture_quad(corners, texture, False)
        return
    shader = _get_noise_shader()
    shader.uniform_float("strength", strength)
    shader.uniform_int("grain", props.stack_grain(scene))
    shader.uniform_int("slice_index", z)
    select.draw_quad(shader, corners, texture)


def _target_for(width: int, height: int) -> Target:
    global _target
    if _target is None or (_target[0].width, _target[0].height) != (width, height):
        texture = gpu.types.GPUTexture((width, height), format="RGBA16F")
        _target = (texture, gpu.types.GPUFrameBuffer(color_slots=texture))
    return _target


def _draw_pixelated(
    slices: list[Slice], affine: select.Affine, resolution: float, scene: bpy.types.Scene
) -> None:
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
            for z, (slice_texture, corners) in enumerate(slices):
                _draw_slice(corners, slice_texture, z, scene)
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
        _draw_pixelated(slices, affine, props.stack_resolution(scene), scene)
        return
    for z, (texture, corners) in enumerate(slices):
        _draw_slice(_to_region(affine, corners), texture, z, scene)


def build_strip(
    canvas: bpy.types.Image, scale: int = 1, strength: float = 0.0, grain: int = 1
) -> np.ndarray | None:
    """Horizontal strip of visible slices, bottom first, noised by strength, upscaled by scale."""
    included = stack_layers(canvas)
    if not included:
        return None
    width, height = canvas.size[0] * scale, canvas.size[1] * scale
    total = sum(layer.height for layer in included)
    strip = np.zeros((height, width * total, 4), dtype=np.float32)
    cell = 0
    for layer in included:
        pixels = select.read_pixels(layer.image)
        for _ in range(layer.height):
            noised = apply_noise(pixels, cell, strength, grain) if strength > 0 else pixels
            strip[:, cell * width : (cell + 1) * width] = noised.repeat(scale, axis=0).repeat(
                scale, axis=1
            )
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
    apply_scale: bpy.props.BoolProperty(
        name="Apply Scale",
        description="Upscale exported slices by the stack scale",
        default=True,
    )
    apply_noise: bpy.props.BoolProperty(
        name="Apply Noise",
        description="Add the stack noise to exported slices",
        default=True,
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return layers.resolve_canvas(context) is not None

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        canvas = layers.resolve_canvas(context)
        assert canvas is not None
        cast(Any, self).filepath = f"{canvas.name}_stack.png"
        return cast("set[OperatorReturnItems]", ExportHelper.invoke(self, context, event))

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = layers.resolve_canvas(context)
        assert canvas is not None
        layers.sync_canvas(canvas)
        scene = context.scene
        assert scene is not None
        scale = props.stack_scale(scene) if self.apply_scale else 1
        strength = props.stack_noise(scene) if self.apply_noise else 0.0
        strip = build_strip(canvas, scale, strength, props.stack_grain(scene))
        if strip is None:
            self.report({"ERROR"}, "No visible layers")
            return {"CANCELLED"}
        filepath = cast(Any, self).filepath
        try:
            save_strip(strip, filepath)
        except (RuntimeError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        slices = strip.shape[1] // (canvas.size[0] * scale)
        self.report({"INFO"}, f"{slices} slices at x{scale} to {Path(filepath).name}")
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
    scene_cls.blix_stack_scale = bpy.props.IntProperty(
        name="Scale",
        description="Pixel scale of the stack preview and exported slices, x1 to x16",
        default=1,
        min=1,
        max=16,
        update=_redraw,
    )
    scene_cls.blix_stack_scale_layers = bpy.props.BoolProperty(
        name="Scale Layers",
        description="Scale layers before stacking, one pixel per height unit; off scales the stack",
        default=False,
        update=_redraw,
    )
    scene_cls.blix_stack_noise = bpy.props.FloatProperty(
        name="Noise",
        description="Uniform brightness noise per slice, in color units; 0 disables",
        default=0.0,
        min=0.0,
        max=0.5,
        step=1,
        precision=3,
        subtype="FACTOR",
        update=_redraw,
    )
    scene_cls.blix_stack_grain = bpy.props.IntProperty(
        name="Grain",
        description="Noise cell size in canvas pixels",
        default=1,
        min=1,
        max=16,
        update=_redraw,
    )
    overlay.extra_draws.append(_draw_stack)


def unregister() -> None:
    global _target, _noise_shader
    overlay.extra_draws.remove(_draw_stack)
    _target = None
    _noise_shader = None
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_stack_grain
    del scene_cls.blix_stack_noise
    del scene_cls.blix_stack_scale_layers
    del scene_cls.blix_stack_scale
    del scene_cls.blix_stack_angle
    del scene_cls.blix_stack_projection
    del scene_cls.blix_stack_resolution
    del scene_cls.blix_stack_pixelate
    del scene_cls.blix_stack_preview
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
