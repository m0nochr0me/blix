"""Marquee pixel selection with floating-buffer move, as a paint-mode tool."""

import math
from typing import TYPE_CHECKING, Any, cast

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import overlay

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

ANTS_DARK = (0.0, 0.0, 0.0, 0.8)
ANTS_LIGHT = (1.0, 1.0, 1.0, 0.9)
SOURCE_DIM = (0.0, 0.0, 0.0, 0.45)

Rect = tuple[int, int, int, int]


class _Session:
    def __init__(self) -> None:
        self.image_name = ""
        self.rect: Rect | None = None
        self.drag_rect: Rect | None = None
        self.buffer: np.ndarray | None = None
        self.offset = (0, 0)
        self.texture: gpu.types.GPUTexture | None = None

    def reset(self) -> None:
        self.__init__()


_session = _Session()
_preview_shader: gpu.types.GPUShader | None = None


def _image(context: bpy.types.Context) -> bpy.types.Image | None:
    space = context.space_data
    if space is None or space.type != "IMAGE_EDITOR":
        return None
    return cast(bpy.types.SpaceImageEditor, space).image


def _read_pixels(image: bpy.types.Image) -> np.ndarray:
    width, height = image.size
    flat = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    return flat.reshape(height, width, 4)


def _write_pixels(image: bpy.types.Image, pixels: np.ndarray) -> None:
    image.pixels.foreach_set(np.ascontiguousarray(pixels).ravel())
    image.update()


def _lift(image: bpy.types.Image, rect: Rect) -> np.ndarray:
    x0, y0, x1, y1 = rect
    return _read_pixels(image)[y0:y1, x0:x1].copy()


def apply_move(
    image: bpy.types.Image, rect: Rect, buffer: np.ndarray, offset: tuple[int, int]
) -> None:
    pixels = _read_pixels(image)
    x0, y0, x1, y1 = rect
    pixels[y0:y1, x0:x1] = 0.0
    dx, dy = offset
    width, height = image.size
    dst_x0, dst_y0 = x0 + dx, y0 + dy
    cx0, cy0 = max(dst_x0, 0), max(dst_y0, 0)
    cx1, cy1 = min(x1 + dx, width), min(y1 + dy, height)
    if cx0 >= cx1 or cy0 >= cy1:
        _write_pixels(image, pixels)
        return
    sub = buffer[cy0 - dst_y0 : cy1 - dst_y0, cx0 - dst_x0 : cx1 - dst_x0]
    dst = pixels[cy0:cy1, cx0:cx1]
    src_a = sub[:, :, 3:4]
    dst_a = dst[:, :, 3:4]
    out_a = src_a + dst_a * (1.0 - src_a)
    safe_a = np.where(out_a == 0.0, 1.0, out_a)
    pixels[cy0:cy1, cx0:cx1, :3] = (
        sub[:, :, :3] * src_a + dst[:, :, :3] * dst_a * (1.0 - src_a)
    ) / safe_a
    pixels[cy0:cy1, cx0:cx1, 3:4] = out_a
    _write_pixels(image, pixels)


def _mouse_pixel(
    region: bpy.types.Region, image: bpy.types.Image, event: bpy.types.Event
) -> tuple[float, float]:
    return overlay.region_to_image(region, image, event.mouse_region_x, event.mouse_region_y)


def _get_preview_shader() -> gpu.types.GPUShader:
    global _preview_shader
    if _preview_shader is not None:
        return _preview_shader
    iface = gpu.types.GPUStageInterfaceInfo("blix_select_iface")
    iface.smooth("VEC2", "uv_interp")
    info = gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0, "VEC2", "pos")
    info.vertex_in(1, "VEC2", "uv")
    info.vertex_out(iface)
    info.sampler(0, "FLOAT_2D", "image")
    info.push_constant("MAT4", "ModelViewProjectionMatrix")
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source(
        "void main()"
        "{gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0); uv_interp = uv;}"
    )
    info.fragment_source(
        "void main()"
        "{ivec2 size = textureSize(image, 0);"
        "ivec2 texel = ivec2(clamp(uv_interp, 0.0, 0.99999) * vec2(size));"
        "fragColor = texelFetch(image, texel, 0);}"
    )
    _preview_shader = gpu.shader.create_from_info(info)
    return _preview_shader


def _make_texture(buffer: np.ndarray) -> gpu.types.GPUTexture:
    height, width = buffer.shape[:2]
    flat = cast(Any, np.ascontiguousarray(buffer).ravel())
    data = gpu.types.Buffer("FLOAT", width * height * 4, flat)
    return gpu.types.GPUTexture((width, height), format="RGBA32F", data=data)


def _rect_corners(
    region: bpy.types.Region, image: bpy.types.Image, rect: Rect
) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = rect
    return [
        overlay.image_to_region(region, image, x0, y0),
        overlay.image_to_region(region, image, x1, y0),
        overlay.image_to_region(region, image, x1, y1),
        overlay.image_to_region(region, image, x0, y1),
    ]


def _loop_points(corners: list[tuple[float, float]]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for index in range(4):
        points += [corners[index], corners[(index + 1) % 4]]
    return points


def _dash_points(
    corners: list[tuple[float, float]], dash: float = 5.0
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for index in range(4):
        sx, sy = corners[index]
        ex, ey = corners[(index + 1) % 4]
        length = math.hypot(ex - sx, ey - sy)
        if length < 1e-3:
            continue
        ux, uy = (ex - sx) / length, (ey - sy) / length
        pos = 0.0
        while pos < length:
            end = min(pos + dash, length)
            points += [(sx + ux * pos, sy + uy * pos), (sx + ux * end, sy + uy * end)]
            pos += dash * 2
    return points


def _draw_ants(region: bpy.types.Region, image: bpy.types.Image, rect: Rect) -> None:
    corners = _rect_corners(region, image, rect)
    overlay.draw_lines(_loop_points(corners), ANTS_DARK)
    overlay.draw_lines(_dash_points(corners), ANTS_LIGHT)


def _draw_buffer(region: bpy.types.Region, image: bpy.types.Image, rect: Rect) -> None:
    if _session.texture is None:
        return
    corners = _rect_corners(region, image, rect)
    quad = [corners[0], corners[1], corners[2], corners[0], corners[2], corners[3]]
    uvs = [(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)]
    shader = _get_preview_shader()
    batch = batch_for_shader(shader, "TRIS", {"pos": quad, "uv": uvs})
    shader.uniform_sampler("image", _session.texture)
    matrix = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    shader.uniform_float("ModelViewProjectionMatrix", cast(Any, matrix))
    batch.draw(shader)


def _draw_selection(region: bpy.types.Region, image: bpy.types.Image) -> None:
    if _session.image_name != image.name:
        return
    if _session.buffer is not None and _session.rect is not None:
        x0, y0, x1, y1 = _session.rect
        dx, dy = _session.offset
        overlay.fill_rects([_rect_bounds(region, image, _session.rect)], SOURCE_DIM)
        dest = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        _draw_buffer(region, image, dest)
        _draw_ants(region, image, dest)
        return
    rect = _session.drag_rect or _session.rect
    if rect is not None:
        _draw_ants(region, image, rect)


def _rect_bounds(
    region: bpy.types.Region, image: bpy.types.Image, rect: Rect
) -> tuple[float, float, float, float]:
    corners = _rect_corners(region, image, rect)
    return (corners[0][0], corners[0][1], corners[2][0], corners[2][1])


class BLIX_OT_select_marquee(bpy.types.Operator):
    """Drag to select pixels; click inside selection to move it, click outside to clear"""

    bl_idname = "blix.select_marquee"
    bl_label = "Select Box"
    bl_options = {"REGISTER", "INTERNAL"}

    _anchor: tuple[int, int]

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = _image(context)
        if image is None or image.size[0] == 0 or image.size[1] == 0:
            return {"PASS_THROUGH"}
        region = context.region
        assert region is not None
        x, y = _mouse_pixel(region, image, event)
        px, py = math.floor(x), math.floor(y)

        if _session.image_name == image.name and _session.rect is not None:
            x0, y0, x1, y1 = _session.rect
            if x0 <= px < x1 and y0 <= py < y1:
                return cast(Any, bpy.ops).blix.select_move("INVOKE_DEFAULT", drag=True)

        _session.reset()
        _session.image_name = image.name
        self._anchor = (px, py)
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = _image(context)
        if image is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            x, y = _mouse_pixel(region, image, event)
            _session.drag_rect = self._normalized(image, math.floor(x), math.floor(y))
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            x, y = _mouse_pixel(region, image, event)
            px, py = math.floor(x), math.floor(y)
            _session.rect = None if (px, py) == self._anchor else self._normalized(image, px, py)
            _session.drag_rect = None
            overlay.tag_redraw(context)
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            _session.drag_rect = None
            _session.rect = None
            overlay.tag_redraw(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}

    def _normalized(self, image: bpy.types.Image, px: int, py: int) -> Rect:
        ax, ay = self._anchor
        width, height = image.size
        x0, x1 = min(ax, px), max(ax, px) + 1
        y0, y1 = min(ay, py), max(ay, py) + 1
        return (max(x0, 0), max(y0, 0), min(x1, width), min(y1, height))


class BLIX_OT_select_move(bpy.types.Operator):
    """Move selected pixels; Enter or click confirms, arrows nudge, Esc cancels"""

    bl_idname = "blix.select_move"
    bl_label = "Move Selection"
    bl_options = {"REGISTER", "INTERNAL"}

    drag: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE", "HIDDEN"})

    _start: tuple[float, float]
    _nudge: list[int]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        image = _image(context)
        return image is not None and _session.rect is not None and _session.image_name == image.name

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = _image(context)
        assert image is not None and _session.rect is not None
        region = context.region
        assert region is not None
        _session.buffer = _lift(image, _session.rect)
        _session.texture = _make_texture(_session.buffer)
        _session.offset = (0, 0)
        self._start = _mouse_pixel(region, image, event)
        self._nudge = [0, 0]
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        overlay.tag_redraw(context)
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = _image(context)
        if image is None or _session.buffer is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            x, y = _mouse_pixel(region, image, event)
            _session.offset = (
                round(x - self._start[0]) + self._nudge[0],
                round(y - self._start[1]) + self._nudge[1],
            )
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        nudges = {
            "LEFT_ARROW": (-1, 0),
            "RIGHT_ARROW": (1, 0),
            "DOWN_ARROW": (0, -1),
            "UP_ARROW": (0, 1),
        }
        if event.type in nudges and event.value == "PRESS":
            dx, dy = nudges[event.type]
            self._nudge[0] += dx
            self._nudge[1] += dy
            _session.offset = (_session.offset[0] + dx, _session.offset[1] + dy)
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        confirm = (
            (self.drag and event.type == "LEFTMOUSE" and event.value == "RELEASE")
            or (not self.drag and event.type == "LEFTMOUSE" and event.value == "PRESS")
            or (event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS")
        )
        if confirm:
            self._commit(context, image)
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            _session.buffer = None
            _session.texture = None
            overlay.tag_redraw(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}

    def _commit(self, context: bpy.types.Context, image: bpy.types.Image) -> None:
        assert _session.rect is not None and _session.buffer is not None
        rect = _session.rect
        offset = _session.offset
        apply_move(image, rect, _session.buffer, offset)
        width, height = image.size
        x0, y0, x1, y1 = rect
        dx, dy = offset
        nx0, ny0 = max(x0 + dx, 0), max(y0 + dy, 0)
        nx1, ny1 = min(x1 + dx, width), min(y1 + dy, height)
        _session.rect = (nx0, ny0, nx1, ny1) if nx0 < nx1 and ny0 < ny1 else None
        _session.buffer = None
        _session.texture = None
        cast(Any, bpy.ops.ed).undo_push(message="Blix Move Selection")
        overlay.tag_redraw(context)


class BLIX_OT_select_clear(bpy.types.Operator):
    """Clear selection"""

    bl_idname = "blix.select_clear"
    bl_label = "Clear Selection"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        if _session.buffer is not None:
            return {"PASS_THROUGH"}
        _session.reset()
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_TOOL_select(bpy.types.WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = "blix.select_box"
    bl_label = "Blix Select"
    bl_description = "Select and move pixel regions"
    bl_icon = "ops.generic.select_box"
    bl_widget = None
    bl_keymap = (
        ("blix.select_marquee", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
        ("blix.select_move", {"type": "G", "value": "PRESS"}, None),
        ("blix.select_clear", {"type": "ESC", "value": "PRESS"}, None),
    )


_classes = (BLIX_OT_select_marquee, BLIX_OT_select_move, BLIX_OT_select_clear)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_tool(BLIX_TOOL_select, separator=True)
    overlay.extra_draws.append(_draw_selection)


def unregister() -> None:
    overlay.extra_draws.remove(_draw_selection)
    bpy.utils.unregister_tool(BLIX_TOOL_select)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    _session.reset()
