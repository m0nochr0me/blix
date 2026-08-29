"""Marquee pixel selection with floating-buffer move, as a paint-mode tool."""

import math
from typing import TYPE_CHECKING, Any, cast

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import overlay, props

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

ANTS_DARK = (0.0, 0.0, 0.0, 0.8)
ANTS_LIGHT = (1.0, 1.0, 1.0, 0.9)
SOURCE_DIM = (0.0, 0.0, 0.0, 0.45)

Rect = tuple[int, int, int, int]
Quad = list[tuple[float, float]]


class _Session:
    def __init__(self) -> None:
        self.image_name = ""
        self.rect: Rect | None = None
        self.drag_rect: Rect | None = None
        self.buffer: np.ndarray | None = None
        self.offset = (0, 0)
        self.preview_quad: Quad | None = None
        self.texture: gpu.types.GPUTexture | None = None

    def reset(self) -> None:
        self.__init__()

    def drop_float(self) -> None:
        self.buffer = None
        self.texture = None
        self.preview_quad = None


session = _Session()
_preview_shader: gpu.types.GPUShader | None = None


def edit_image(context: bpy.types.Context) -> bpy.types.Image | None:
    space = context.space_data
    if space is None or space.type != "IMAGE_EDITOR":
        return None
    return cast(bpy.types.SpaceImageEditor, space).image


def can_float(context: bpy.types.Context) -> bool:
    image = edit_image(context)
    return (
        image is not None
        and session.rect is not None
        and session.image_name == image.name
        and session.buffer is None
        and not target_locked(image)
    )


def pixel_target(image: bpy.types.Image) -> bpy.types.Image:
    from . import layers

    if len(props.layers(image)) == 0:
        return image
    layers.sync_canvas(image)
    layer = layers.active_layer(image)
    if layer is None or layer.image is None:
        return image
    return layer.image


def target_locked(image: bpy.types.Image) -> bool:
    from . import layers

    if len(props.layers(image)) == 0:
        return False
    layer = layers.active_layer(image)
    return layer is None or layer.image is None or layer.lock


def read_pixels(image: bpy.types.Image) -> np.ndarray:
    width, height = image.size
    flat = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    return flat.reshape(height, width, 4)


def write_pixels(image: bpy.types.Image, pixels: np.ndarray) -> None:
    image.pixels.foreach_set(np.ascontiguousarray(pixels).ravel())
    image.update()


def lift(image: bpy.types.Image, rect: Rect) -> np.ndarray:
    x0, y0, x1, y1 = rect
    return read_pixels(pixel_target(image))[y0:y1, x0:x1].copy()


def apply_buffer(
    image: bpy.types.Image, clear_rect: Rect, buffer: np.ndarray, origin: tuple[int, int]
) -> None:
    target = pixel_target(image)
    pixels = read_pixels(target)
    x0, y0, x1, y1 = clear_rect
    pixels[y0:y1, x0:x1] = 0.0
    buf_h, buf_w = buffer.shape[:2]
    ox, oy = origin
    width, height = target.size
    cx0, cy0 = max(ox, 0), max(oy, 0)
    cx1, cy1 = min(ox + buf_w, width), min(oy + buf_h, height)
    if cx0 >= cx1 or cy0 >= cy1:
        write_pixels(target, pixels)
        return
    sub = buffer[cy0 - oy : cy1 - oy, cx0 - ox : cx1 - ox]
    dst = pixels[cy0:cy1, cx0:cx1]
    src_a = sub[:, :, 3:4]
    dst_a = dst[:, :, 3:4]
    out_a = src_a + dst_a * (1.0 - src_a)
    safe_a = np.where(out_a == 0.0, 1.0, out_a)
    pixels[cy0:cy1, cx0:cx1, :3] = (
        sub[:, :, :3] * src_a + dst[:, :, :3] * dst_a * (1.0 - src_a)
    ) / safe_a
    pixels[cy0:cy1, cx0:cx1, 3:4] = out_a
    write_pixels(target, pixels)


def clip_rect(
    image: bpy.types.Image, origin: tuple[int, int], size: tuple[int, int]
) -> Rect | None:
    width, height = image.size
    x0, y0 = max(origin[0], 0), max(origin[1], 0)
    x1, y1 = min(origin[0] + size[0], width), min(origin[1] + size[1], height)
    if x0 >= x1 or y0 >= y1:
        return None
    return (x0, y0, x1, y1)


def finish_float(
    context: bpy.types.Context, image: bpy.types.Image, new_rect: Rect | None, message: str
) -> None:
    from . import layers

    session.rect = new_rect
    session.drop_float()
    if len(props.layers(image)):
        layers.composite(image)
    cast(Any, bpy.ops.ed).undo_push(message=message)
    overlay.tag_redraw(context)


def start_float(image: bpy.types.Image, rect: Rect) -> None:
    session.buffer = lift(image, rect)
    session.texture = make_texture(session.buffer)
    session.preview_quad = rect_quad(rect)


def rect_quad(rect: Rect) -> Quad:
    x0, y0, x1, y1 = rect
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def mouse_pixel(
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


def make_texture(buffer: np.ndarray) -> gpu.types.GPUTexture:
    height, width = buffer.shape[:2]
    flat = cast(Any, np.ascontiguousarray(buffer).ravel())
    data = gpu.types.Buffer("FLOAT", width * height * 4, flat)
    return gpu.types.GPUTexture((width, height), format="RGBA32F", data=data)


def _to_region(region: bpy.types.Region, image: bpy.types.Image, quad: Quad) -> Quad:
    return [overlay.image_to_region(region, image, x, y) for x, y in quad]


def _loop_points(corners: Quad) -> Quad:
    points: Quad = []
    for index in range(4):
        points += [corners[index], corners[(index + 1) % 4]]
    return points


def _dash_points(corners: Quad, dash: float = 5.0) -> Quad:
    points: Quad = []
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


def _draw_ants(corners: Quad) -> None:
    overlay.draw_lines(_loop_points(corners), ANTS_DARK)
    overlay.draw_lines(_dash_points(corners), ANTS_LIGHT)


def draw_texture_quad(corners: Quad, texture: gpu.types.GPUTexture) -> None:
    quad = [corners[0], corners[1], corners[2], corners[0], corners[2], corners[3]]
    uvs = [(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)]
    shader = _get_preview_shader()
    batch = batch_for_shader(shader, "TRIS", {"pos": quad, "uv": uvs})
    shader.uniform_sampler("image", texture)
    matrix = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    shader.uniform_float("ModelViewProjectionMatrix", cast(Any, matrix))
    batch.draw(shader)


def _draw_selection(region: bpy.types.Region, image: bpy.types.Image) -> None:
    if session.image_name != image.name:
        return
    if session.buffer is not None and session.rect is not None and session.preview_quad is not None:
        source = _to_region(region, image, rect_quad(session.rect))
        overlay.fill_rects([(source[0][0], source[0][1], source[2][0], source[2][1])], SOURCE_DIM)
        corners = _to_region(region, image, session.preview_quad)
        if session.texture is not None:
            draw_texture_quad(corners, session.texture)
        _draw_ants(corners)
        return
    rect = session.drag_rect or session.rect
    if rect is not None:
        _draw_ants(_to_region(region, image, rect_quad(rect)))


class BLIX_OT_select_marquee(bpy.types.Operator):
    """Drag to select pixels; click inside selection to move it, click outside to clear"""

    bl_idname = "blix.select_marquee"
    bl_label = "Select Box"
    bl_options = {"REGISTER", "INTERNAL"}

    _anchor: tuple[int, int]

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = edit_image(context)
        if image is None or image.size[0] == 0 or image.size[1] == 0:
            return {"PASS_THROUGH"}
        region = context.region
        assert region is not None
        x, y = mouse_pixel(region, image, event)
        px, py = math.floor(x), math.floor(y)

        if session.image_name == image.name and session.rect is not None:
            x0, y0, x1, y1 = session.rect
            if x0 <= px < x1 and y0 <= py < y1:
                return cast(Any, bpy.ops).blix.select_move("INVOKE_DEFAULT", drag=True)

        session.reset()
        session.image_name = image.name
        self._anchor = (px, py)
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = edit_image(context)
        if image is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            x, y = mouse_pixel(region, image, event)
            session.drag_rect = self._normalized(image, math.floor(x), math.floor(y))
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            x, y = mouse_pixel(region, image, event)
            px, py = math.floor(x), math.floor(y)
            session.rect = None if (px, py) == self._anchor else self._normalized(image, px, py)
            session.drag_rect = None
            overlay.tag_redraw(context)
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            session.drag_rect = None
            session.rect = None
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
        return can_float(context)

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = edit_image(context)
        assert image is not None and session.rect is not None
        region = context.region
        assert region is not None
        start_float(image, session.rect)
        session.offset = (0, 0)
        self._start = mouse_pixel(region, image, event)
        self._nudge = [0, 0]
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        overlay.tag_redraw(context)
        return {"RUNNING_MODAL"}

    def _update_quad(self) -> None:
        assert session.rect is not None
        x0, y0, x1, y1 = session.rect
        dx, dy = session.offset
        session.preview_quad = rect_quad((x0 + dx, y0 + dy, x1 + dx, y1 + dy))

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = edit_image(context)
        if image is None or session.buffer is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            x, y = mouse_pixel(region, image, event)
            session.offset = (
                round(x - self._start[0]) + self._nudge[0],
                round(y - self._start[1]) + self._nudge[1],
            )
            self._update_quad()
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
            session.offset = (session.offset[0] + dx, session.offset[1] + dy)
            self._update_quad()
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
            session.drop_float()
            overlay.tag_redraw(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}

    def _commit(self, context: bpy.types.Context, image: bpy.types.Image) -> None:
        assert session.rect is not None and session.buffer is not None
        x0, y0, x1, y1 = session.rect
        dx, dy = session.offset
        origin = (x0 + dx, y0 + dy)
        apply_buffer(image, session.rect, session.buffer, origin)
        new_rect = clip_rect(image, origin, (x1 - x0, y1 - y0))
        finish_float(context, image, new_rect, "Blix Move Selection")


class BLIX_OT_select_clear(bpy.types.Operator):
    """Clear selection"""

    bl_idname = "blix.select_clear"
    bl_label = "Clear Selection"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        if session.buffer is not None:
            return {"PASS_THROUGH"}
        session.reset()
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_TOOL_select(bpy.types.WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = "blix.select_box"
    bl_label = "Blix Select"
    bl_description = "Select, move and transform pixel regions"
    bl_icon = "ops.generic.select_box"
    bl_widget = None
    bl_keymap = (
        ("blix.select_marquee", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
        ("blix.select_move", {"type": "G", "value": "PRESS"}, None),
        ("blix.select_rotate", {"type": "R", "value": "PRESS"}, None),
        ("blix.select_scale", {"type": "S", "value": "PRESS"}, None),
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
    session.reset()
