"""Marquee pixel selection with floating-buffer move, as a paint-mode tool."""

import math
from typing import TYPE_CHECKING, Any, cast

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import overlay, props, undo

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

ANTS_DARK = (0.0, 0.0, 0.0, 0.8)
ANTS_LIGHT = (1.0, 1.0, 1.0, 0.9)
SOURCE_DIM = (0.0, 0.0, 0.0, 0.45)
HANDLE_FILL = (1.0, 1.0, 1.0, 1.0)
HANDLE_HALF = 3.0
HANDLE_HIT = 6.0
ROTATE_REACH = 18.0
_SELECT_TOOLS = {"blix.select_box", "blix.select_ellipse"}

Rect = tuple[int, int, int, int]
Size = tuple[int, int]
Quad = list[tuple[float, float]]
Affine = tuple[float, float, float, float]


class _Session:
    def __init__(self) -> None:
        self.image_name = ""
        self.mask: np.ndarray | None = None
        self.rect: Rect | None = None
        self.outline: np.ndarray | None = None
        self.drag_outline: np.ndarray | None = None
        self.buffer: np.ndarray | None = None
        self.float_mask: np.ndarray | None = None
        self.float_outline: np.ndarray | None = None
        self.offset = (0, 0)
        self.preview_offset: tuple[int, int] | None = None
        self.preview_quad: Quad | None = None
        self.tex_quad: Quad | None = None
        self.texture: gpu.types.GPUTexture | None = None
        self.keep_source = False

    def reset(self) -> None:
        self.__init__()

    def set_mask(self, mask: np.ndarray | None) -> None:
        rect = mask_bbox(mask) if mask is not None else None
        self.mask = mask if rect is not None else None
        self.rect = rect
        self.outline = mask_outline(mask) if rect is not None and mask is not None else None

    def drop_float(self) -> None:
        self.buffer = None
        self.float_mask = None
        self.float_outline = None
        self.preview_offset = None
        self.texture = None
        self.preview_quad = None
        self.tex_quad = None
        self.keep_source = False


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


def mask_bbox(mask: np.ndarray) -> Rect | None:
    rows = np.flatnonzero(np.any(mask, axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(np.any(mask, axis=0))
    return (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)


def mask_outline(mask: np.ndarray) -> np.ndarray:
    """Boundary unit segments in image space, shape (N, 2, 2)."""
    height, width = mask.shape
    columns = np.zeros((height, width + 2), dtype=bool)
    columns[:, 1:-1] = mask
    vy, vx = np.nonzero(columns[:, 1:] != columns[:, :-1])
    rows = np.zeros((height + 2, width), dtype=bool)
    rows[1:-1] = mask
    hy, hx = np.nonzero(rows[1:] != rows[:-1])
    starts = np.concatenate([np.stack([vx, vy], axis=1), np.stack([hx, hy], axis=1)]).astype(
        np.float32
    )
    ends = starts.copy()
    ends[: vx.size, 1] += 1.0
    ends[vx.size :, 0] += 1.0
    return np.stack([starts, ends], axis=1)


def mask_row_rects(mask: np.ndarray) -> list[tuple[float, float, float, float]]:
    """Contiguous per-row runs of a mask as image-space rectangles."""
    height, width = mask.shape
    padded = np.zeros((height, width + 2), dtype=bool)
    padded[:, 1:-1] = mask
    ys, xs = np.nonzero(padded[:, 1:] != padded[:, :-1])
    rows = ys[0::2].tolist()
    starts = xs[0::2].tolist()
    ends = xs[1::2].tolist()
    return [(x0, y, x1, y + 1) for y, x0, x1 in zip(rows, starts, ends, strict=True)]


def rect_mask(size: Size, rect: Rect) -> np.ndarray:
    width, height = size
    mask = np.zeros((height, width), dtype=bool)
    box = clip_rect(size, rect)
    if box is None:
        return mask
    mask[box[1] : box[3], box[0] : box[2]] = True
    return mask


def ellipse_mask(size: Size, rect: Rect) -> np.ndarray:
    """Ellipse inscribed in an unbounded rect, rasterized inside the image."""
    width, height = size
    x0, y0, x1, y1 = rect
    mask = np.zeros((height, width), dtype=bool)
    box = clip_rect(size, rect)
    if box is None:
        return mask
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = max((x1 - x0) / 2, 0.5), max((y1 - y0) / 2, 0.5)
    bx0, by0, bx1, by1 = box
    yy, xx = np.mgrid[by0:by1, bx0:bx1]
    mask[by0:by1, bx0:bx1] = ((xx + 0.5 - cx) / rx) ** 2 + ((yy + 0.5 - cy) / ry) ** 2 <= 1.0
    return mask


def shape_mask(size: Size, rect: Rect, shape: str) -> np.ndarray:
    return ellipse_mask(size, rect) if shape == "ELLIPSE" else rect_mask(size, rect)


def combine_mask(base: np.ndarray | None, shape: np.ndarray, mode: str) -> np.ndarray:
    if base is None or mode == "SET":
        return shape
    if mode == "ADD":
        return base | shape
    return base & ~shape


def place_mask(image: bpy.types.Image, sub: np.ndarray, origin: tuple[int, int]) -> np.ndarray:
    width, height = image.size
    mask = np.zeros((height, width), dtype=bool)
    ox, oy = origin
    box = clip_rect((width, height), (ox, oy, ox + sub.shape[1], oy + sub.shape[0]))
    if box is None:
        return mask
    x0, y0, x1, y1 = box
    mask[y0:y1, x0:x1] = sub[y0 - oy : y1 - oy, x0 - ox : x1 - ox]
    return mask


def lift(image: bpy.types.Image, mask: np.ndarray, rect: Rect) -> tuple[np.ndarray, np.ndarray]:
    x0, y0, x1, y1 = rect
    buffer = read_pixels(pixel_target(image))[y0:y1, x0:x1].copy()
    sub = mask[y0:y1, x0:x1].copy()
    buffer[~sub] = 0.0
    return buffer, sub


def apply_buffer(
    image: bpy.types.Image, clear_mask: np.ndarray, buffer: np.ndarray, origin: tuple[int, int]
) -> None:
    target = pixel_target(image)
    pixels = read_pixels(target)
    pixels[clear_mask] = 0.0
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


def clip_rect(size: Size, rect: Rect) -> Rect | None:
    """Intersection of an unbounded canvas rect with the image, None if outside."""
    width, height = size
    x0, y0 = max(rect[0], 0), max(rect[1], 0)
    x1, y1 = min(rect[2], width), min(rect[3], height)
    if x0 >= x1 or y0 >= y1:
        return None
    return (x0, y0, x1, y1)


def finish_float(
    context: bpy.types.Context, image: bpy.types.Image, new_mask: np.ndarray | None
) -> None:
    from . import layers

    session.drop_float()
    session.set_mask(new_mask)
    if len(props.layers(image)):
        layers.composite(image)
    undo.record(context, image)
    overlay.tag_redraw(context)


def start_float(image: bpy.types.Image, mask: np.ndarray, rect: Rect) -> None:
    session.buffer, session.float_mask = lift(image, mask, rect)
    session.float_outline = mask_outline(session.float_mask)
    session.texture = make_texture(session.buffer)
    session.preview_offset = None
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


class Preview:
    """Textured overlay of a pending pixel edit, drawn over the image."""

    def __init__(self) -> None:
        self.texture: gpu.types.GPUTexture | None = None
        self.rect: Rect | None = None
        self.image_name = ""

    def set(self, image: bpy.types.Image, rect: Rect, buffer: np.ndarray) -> None:
        self.texture = make_texture(buffer)
        self.rect = rect
        self.image_name = image.name

    def clear(self) -> None:
        self.texture = None
        self.rect = None
        self.image_name = ""

    def draw(self, region: bpy.types.Region, image: bpy.types.Image) -> None:
        if self.texture is None or self.rect is None or self.image_name != image.name:
            return
        corners = [overlay.image_to_region(region, image, x, y) for x, y in rect_quad(self.rect)]
        draw_texture_quad(corners, self.texture)


def _to_region(region: bpy.types.Region, image: bpy.types.Image, quad: Quad) -> Quad:
    return [overlay.image_to_region(region, image, x, y) for x, y in quad]


def _image_affine(region: bpy.types.Region, image: bpy.types.Image) -> Affine | None:
    """Image pixel to region pixel as (scale x, offset x, scale y, offset y)."""
    u0, v0 = region.view2d.region_to_view(0.0, 0.0)
    u1, v1 = region.view2d.region_to_view(region.width, region.height)
    width, height = image.size
    if u1 == u0 or v1 == v0:
        return None
    sx = region.width / ((u1 - u0) * width)
    sy = region.height / ((v1 - v0) * height)
    return (sx, -u0 * width * sx, sy, -v0 * height * sy)


def _segment_points(affine: Affine, segments: np.ndarray) -> Quad:
    sx, tx, sy, ty = affine
    points = segments.reshape(-1, 2)
    xs = points[:, 0] * sx + tx
    ys = points[:, 1] * sy + ty
    return list(zip(xs.tolist(), ys.tolist(), strict=True))


def _region_rects(
    affine: Affine, rects: list[tuple[float, float, float, float]]
) -> list[tuple[float, float, float, float]]:
    sx, tx, sy, ty = affine
    return [(x0 * sx + tx, y0 * sy + ty, x1 * sx + tx, y1 * sy + ty) for x0, y0, x1, y1 in rects]


def _loop_points(corners: Quad) -> Quad:
    points: Quad = []
    for index in range(4):
        points += [corners[index], corners[(index + 1) % 4]]
    return points


def _dash_points(segments: Quad, dash: float = 5.0) -> Quad:
    points: Quad = []
    for index in range(0, len(segments) - 1, 2):
        sx, sy = segments[index]
        ex, ey = segments[index + 1]
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


def _draw_ants(segments: Quad) -> None:
    overlay.draw_lines(segments, ANTS_DARK)
    overlay.draw_lines(_dash_points(segments), ANTS_LIGHT)


def rect_handles(rect: Rect) -> list[tuple[int, int, float, float]]:
    """Handle id (hx, hy in -1..1) and image-space position for 8 bbox handles."""
    x0, y0, x1, y1 = rect
    xs = ((-1, float(x0)), (0, (x0 + x1) / 2), (1, float(x1)))
    ys = ((-1, float(y0)), (0, (y0 + y1) / 2), (1, float(y1)))
    return [(hx, hy, x, y) for hx, x in xs for hy, y in ys if hx or hy]


def hit_handle(
    region: bpy.types.Region, image: bpy.types.Image, rect: Rect, rx: float, ry: float
) -> tuple[int, int] | None:
    reach = HANDLE_HIT * overlay.ui_scale()
    for hx, hy, x, y in rect_handles(rect):
        px, py = overlay.image_to_region(region, image, x, y)
        if abs(rx - px) <= reach and abs(ry - py) <= reach:
            return hx, hy
    return None


def hit_rotate(
    region: bpy.types.Region, image: bpy.types.Image, rect: Rect, rx: float, ry: float
) -> bool:
    reach = ROTATE_REACH * overlay.ui_scale()
    for x, y in rect_quad(rect):
        px, py = overlay.image_to_region(region, image, x, y)
        if math.hypot(rx - px, ry - py) <= reach:
            return True
    return False


def _quad_handles(corners: Quad) -> Quad:
    mids = [
        ((x + nx) / 2, (y + ny) / 2)
        for (x, y), (nx, ny) in zip(corners, corners[1:] + corners[:1], strict=True)
    ]
    return list(corners) + mids


def _draw_handles(points: Quad) -> None:
    scale = overlay.ui_scale()
    half = HANDLE_HALF * scale
    border = half + scale
    overlay.fill_rects(
        [(x - border, y - border, x + border, y + border) for x, y in points], ANTS_DARK
    )
    overlay.fill_rects([(x - half, y - half, x + half, y + half) for x, y in points], HANDLE_FILL)


def _select_tool_active(context: bpy.types.Context) -> bool:
    workspace = context.workspace
    if workspace is None:
        return False
    tool = workspace.tools.from_space_image_mode("PAINT", create=False)
    return tool is not None and tool.idname in _SELECT_TOOLS


def draw_texture_quad(corners: Quad, texture: gpu.types.GPUTexture) -> None:
    quad = [corners[0], corners[1], corners[2], corners[0], corners[2], corners[3]]
    uvs = [(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)]
    shader = _get_preview_shader()
    batch = batch_for_shader(shader, "TRIS", {"pos": quad, "uv": uvs})
    shader.uniform_sampler("image", texture)
    matrix = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    shader.uniform_float("ModelViewProjectionMatrix", cast(Any, matrix))
    batch.draw(shader)


def _float_ants(affine: Affine, corners: Quad) -> Quad:
    if session.float_outline is None or session.preview_offset is None or session.rect is None:
        return _loop_points(corners)
    offset = np.array(
        [session.rect[0] + session.preview_offset[0], session.rect[1] + session.preview_offset[1]],
        dtype=np.float32,
    )
    return _segment_points(affine, session.float_outline + offset)


def _draw_selection(region: bpy.types.Region, image: bpy.types.Image) -> None:
    if session.image_name != image.name:
        return
    affine = _image_affine(region, image)
    if affine is None:
        return
    if session.buffer is not None and session.mask is not None and session.preview_quad is not None:
        if not session.keep_source:
            overlay.fill_rects(_region_rects(affine, mask_row_rects(session.mask)), SOURCE_DIM)
        corners = _to_region(region, image, session.preview_quad)
        if session.texture is not None:
            tex_corners = (
                _to_region(region, image, session.tex_quad)
                if session.tex_quad is not None
                else corners
            )
            draw_texture_quad(tex_corners, session.texture)
        _draw_ants(_float_ants(affine, corners))
        _draw_handles(_quad_handles(corners))
        return
    outline = session.drag_outline if session.drag_outline is not None else session.outline
    if outline is not None:
        _draw_ants(_segment_points(affine, outline))
    if (
        session.rect is not None
        and session.drag_outline is None
        and _select_tool_active(bpy.context)
    ):
        _draw_handles(
            [
                overlay.image_to_region(region, image, x, y)
                for _, _, x, y in rect_handles(session.rect)
            ]
        )


class BLIX_OT_select_marquee(bpy.types.Operator):
    """Drag to select pixels; Shift adds, Ctrl subtracts, click inside selection to move it"""

    bl_idname = "blix.select_marquee"
    bl_label = "Select Box"
    bl_options = {"REGISTER", "INTERNAL"}

    shape: bpy.props.EnumProperty(
        items=(("BOX", "Box", ""), ("ELLIPSE", "Ellipse", "")),
        default="BOX",
        options={"SKIP_SAVE", "HIDDEN"},
    )
    mode: bpy.props.EnumProperty(
        items=(
            ("DEFAULT", "Default", ""),
            ("SET", "Set", ""),
            ("ADD", "Add", ""),
            ("SUB", "Subtract", ""),
        ),
        default="DEFAULT",
        options={"SKIP_SAVE", "HIDDEN"},
    )

    _anchor: tuple[int, int]
    _base: np.ndarray | None
    _mode: str

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        from . import guides

        image = edit_image(context)
        if image is None or image.size[0] == 0 or image.size[1] == 0:
            return {"PASS_THROUGH"}
        region = context.region
        assert region is not None
        near_guide = guides.find_near(region, image, event.mouse_region_x, event.mouse_region_y)
        if event.ctrl and near_guide >= 0:
            return {"PASS_THROUGH"}
        x, y = mouse_pixel(region, image, event)
        px, py = math.floor(x), math.floor(y)

        scene = context.scene
        mode = self.mode
        if mode == "DEFAULT":
            mode = props.select_mode(scene) if scene is not None else "SET"
        current = session.mask if session.image_name == image.name else None

        if mode == "SET" and current is not None:
            height, width = current.shape
            inside = 0 <= px < width and 0 <= py < height and current[py, px]
            if can_float(context) and session.rect is not None:
                rx, ry = event.mouse_region_x, event.mouse_region_y
                handle = hit_handle(region, image, session.rect, rx, ry)
                if handle is not None:
                    return cast(Any, bpy.ops).blix.select_scale(
                        "INVOKE_DEFAULT", drag=True, handle_x=handle[0], handle_y=handle[1]
                    )
                if not inside and hit_rotate(region, image, session.rect, rx, ry):
                    return cast(Any, bpy.ops).blix.select_rotate("INVOKE_DEFAULT", drag=True)
            if inside:
                return cast(Any, bpy.ops).blix.select_move("INVOKE_DEFAULT", drag=True)

        session.reset()
        session.image_name = image.name
        self._base = None if mode == "SET" else current
        self._mode = mode
        self._anchor = (px, py)
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _combined(self, image: bpy.types.Image, px: int, py: int) -> np.ndarray:
        rect = self._normalized(px, py)
        size = (image.size[0], image.size[1])
        return combine_mask(self._base, shape_mask(size, rect, self.shape), self._mode)

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = edit_image(context)
        if image is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None

        if event.type == "MOUSEMOVE":
            x, y = mouse_pixel(region, image, event)
            session.drag_outline = mask_outline(self._combined(image, math.floor(x), math.floor(y)))
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            x, y = mouse_pixel(region, image, event)
            px, py = math.floor(x), math.floor(y)
            session.drag_outline = None
            if (px, py) == self._anchor:
                session.set_mask(self._base)
            else:
                session.set_mask(self._combined(image, px, py))
            overlay.tag_redraw(context)
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            session.drag_outline = None
            session.set_mask(self._base)
            overlay.tag_redraw(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}

    def _normalized(self, px: int, py: int) -> Rect:
        """Drag rect on an unbounded canvas; clipping happens when rasterizing."""
        ax, ay = self._anchor
        return (min(ax, px), min(ay, py), max(ax, px) + 1, max(ay, py) + 1)


class BLIX_OT_select_move(bpy.types.Operator):
    """Move selected pixels; Enter or click confirms, arrows nudge, Esc cancels"""

    bl_idname = "blix.select_move"
    bl_label = "Move Selection"
    bl_options = {"REGISTER", "INTERNAL"}

    drag: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE", "HIDDEN"})
    duplicate: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE", "HIDDEN"})

    _start: tuple[float, float]
    _nudge: list[int]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if can_float(context):
            return True
        image = edit_image(context)
        return image is not None and session.buffer is not None and session.image_name == image.name

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = edit_image(context)
        assert image is not None and session.rect is not None
        region = context.region
        assert region is not None
        if session.buffer is None:
            assert session.mask is not None
            start_float(image, session.mask, session.rect)
            session.offset = (0, 0)
            session.preview_offset = (0, 0)
            session.keep_source = self.duplicate
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
        session.preview_offset = (dx, dy)
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
        assert session.mask is not None and session.float_mask is not None
        dx, dy = session.offset
        origin = (session.rect[0] + dx, session.rect[1] + dy)
        clear = np.zeros_like(session.mask) if session.keep_source else session.mask
        undo.record(context, image)
        apply_buffer(image, clear, session.buffer, origin)
        finish_float(context, image, place_mask(image, session.float_mask, origin))


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


def _tool_keymap(shape: str) -> tuple[Any, ...]:
    marquee = "blix.select_marquee"
    return (
        (marquee, {"type": "LEFTMOUSE", "value": "PRESS"}, {"properties": [("shape", shape)]}),
        (
            marquee,
            {"type": "LEFTMOUSE", "value": "PRESS", "shift": True},
            {"properties": [("shape", shape), ("mode", "ADD")]},
        ),
        (
            marquee,
            {"type": "LEFTMOUSE", "value": "PRESS", "ctrl": True},
            {"properties": [("shape", shape), ("mode", "SUB")]},
        ),
        ("blix.select_move", {"type": "G", "value": "PRESS"}, None),
        ("blix.select_rotate", {"type": "R", "value": "PRESS"}, None),
        ("blix.select_scale", {"type": "S", "value": "PRESS"}, None),
        (
            "blix.select_move",
            {"type": "D", "value": "PRESS", "shift": True},
            {"properties": [("duplicate", True)]},
        ),
        ("blix.select_copy", {"type": "C", "value": "PRESS", "ctrl": True}, None),
        ("blix.select_paste", {"type": "V", "value": "PRESS", "ctrl": True}, None),
        ("blix.select_delete", {"type": "X", "value": "PRESS"}, None),
        ("blix.select_delete", {"type": "DEL", "value": "PRESS"}, None),
        ("blix.select_clear", {"type": "ESC", "value": "PRESS"}, None),
    )


class BLIX_TOOL_select(bpy.types.WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = "blix.select_box"
    bl_label = "Blix Select Box"
    bl_description = "Select, move and transform rectangular pixel regions"
    bl_icon = "ops.generic.select_box"
    bl_widget = None
    bl_keymap = _tool_keymap("BOX")


class BLIX_TOOL_select_ellipse(bpy.types.WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = "blix.select_ellipse"
    bl_label = "Blix Select Ellipse"
    bl_description = "Select, move and transform elliptical pixel regions"
    bl_icon = "ops.generic.select_circle"
    bl_widget = None
    bl_keymap = _tool_keymap("ELLIPSE")


_classes = (BLIX_OT_select_marquee, BLIX_OT_select_move, BLIX_OT_select_clear)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_select_mode = bpy.props.EnumProperty(
        name="Mode",
        description="How a new marquee combines with the current selection",
        items=(
            ("SET", "Set", "Replace the selection", "SELECT_SET", 0),
            ("ADD", "Add", "Add to the selection", "SELECT_EXTEND", 1),
            ("SUB", "Subtract", "Subtract from the selection", "SELECT_SUBTRACT", 2),
        ),
        default="SET",
    )
    bpy.utils.register_tool(BLIX_TOOL_select, separator=True)
    bpy.utils.register_tool(BLIX_TOOL_select_ellipse, after="blix.select_box")
    overlay.extra_draws.append(_draw_selection)


def unregister() -> None:
    overlay.extra_draws.remove(_draw_selection)
    bpy.utils.unregister_tool(BLIX_TOOL_select_ellipse)
    bpy.utils.unregister_tool(BLIX_TOOL_select)
    del cast(Any, bpy.types.Scene).blix_select_mode
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    session.reset()
