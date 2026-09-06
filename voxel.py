"""Voxel bridge: slice a mesh object into stack layers, build a mesh from the stack."""

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import bmesh
import bpy
import numpy as np
from bpy_extras.io_utils import ImportHelper
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import layers, paint, palette, props, select, stacking

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

_MAX_SLICES = 512
_DEFAULT_COLOR = np.array([0.8, 0.8, 0.8], dtype=np.float32)
_CORNERS = ((0, 0), (1, 0), (1, 1), (0, 1))


def stack_voxels(canvas: bpy.types.Image) -> np.ndarray:
    """Visible slices bottom first as (z, y, x, rgba), each layer repeated by its height."""
    width, height = canvas.size
    slices = [
        select.read_pixels(layer.image)
        for layer in stacking.stack_layers(canvas)
        for _ in range(layer.height)
    ]
    if not slices:
        return np.zeros((0, height, width, 4), dtype=np.float32)
    return np.stack(slices)


def _swatches(context: bpy.types.Context) -> np.ndarray | None:
    active = palette.active(context)
    if active is None:
        return None
    swatches = palette.swatches(active)
    return swatches if len(swatches) else None


def _layers_from_slices(
    context: bpy.types.Context, canvas: bpy.types.Image, slices: Any, message: str
) -> None:
    layers.init_layers(canvas)
    with layers._undo_step(context, canvas, message):
        for z, pixels in enumerate(slices):
            if z:
                layers.add_layer(canvas, layers.next_layer_number(canvas))
            layer = layers.active_layer(canvas)
            layer.label = f"Z{z:03d}"
            select.write_pixels(layer.image, pixels)
        layers.composite(canvas)
    space = context.space_data
    if space is not None and space.type == "IMAGE_EDITOR":
        cast(bpy.types.SpaceImageEditor, space).image = canvas


def _exposed(filled: np.ndarray, axis: int, sign: int) -> np.ndarray:
    """Filled voxels whose neighbour along array axis in direction sign is empty or outside."""
    neighbour = np.zeros_like(filled)
    source: list[slice] = [slice(None)] * 3
    target: list[slice] = [slice(None)] * 3
    if sign > 0:
        target[axis], source[axis] = slice(None, -1), slice(1, None)
    else:
        target[axis], source[axis] = slice(1, None), slice(None, -1)
    neighbour[tuple(target)] = filled[tuple(source)]
    return filled & ~neighbour


def _surface(filled: np.ndarray) -> np.ndarray:
    surface = np.zeros_like(filled)
    for axis in range(3):
        for sign in (1, -1):
            surface |= _exposed(filled, axis, sign)
    return surface


def _shell(filled: np.ndarray, depth: int) -> np.ndarray:
    """Filled voxels within depth steps of the outside along the six directions."""
    core = filled
    for _ in range(depth):
        core = core & ~_surface(core)
    return filled & ~core


def _material_color(material: bpy.types.Material | None) -> np.ndarray:
    if material is None:
        return _DEFAULT_COLOR
    tree = material.node_tree
    if tree is not None:
        bsdf = next((node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None and not bsdf.inputs["Base Color"].is_linked:
            value = cast(Any, bsdf.inputs["Base Color"]).default_value
            return np.array(value[:3], dtype=np.float32)
    return np.array(material.diffuse_color[:3], dtype=np.float32)


def face_colors(obj: bpy.types.Object, mesh: bpy.types.Mesh) -> np.ndarray:
    """sRGB colour per polygon from its material slot; empty slots fall back to grey."""
    slots = [_material_color(slot.material) for slot in obj.material_slots] or [_DEFAULT_COLOR]
    indices = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("material_index", indices)
    linear = np.array(slots, dtype=np.float32)[np.clip(indices, 0, len(slots) - 1)]
    return paint.srgb_encode(linear)


def _world_mesh(
    obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph
) -> tuple[np.ndarray, list[list[int]], bpy.types.Mesh]:
    mesh = cast(bpy.types.Mesh, obj.evaluated_get(depsgraph).data)
    count = len(mesh.vertices)
    coords = np.empty(count * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coords)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    world = coords.reshape(count, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    return world, [list(polygon.vertices) for polygon in mesh.polygons], mesh


def _triangles(mesh: bpy.types.Mesh, world: np.ndarray) -> np.ndarray:
    count = len(mesh.loop_triangles)
    indices = np.empty(count * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", indices)
    return world[indices.reshape(count, 3)]


def _edge(a: np.ndarray, b: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """Edge function of a->b at the pixel centres, computed from the lower endpoint for parity."""
    if (b[0], b[1]) < (a[0], a[1]):
        return -_edge(b, a, px, py)
    return (b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0])


def _covers(w: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Half-open fill rule: a pixel centre on the edge belongs to one of the two triangles."""
    owns = b[1] < a[1] or (b[1] == a[1] and b[0] > a[0])
    return (w > 0) | ((w == 0) & owns)


def _crossings(
    triangles: np.ndarray, xs: np.ndarray, ys: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Column index, z and winding step of every triangle crossed by the +Z pixel-column rays."""
    columns: list[np.ndarray] = []
    heights: list[np.ndarray] = []
    steps: list[np.ndarray] = []
    for p0, p1, p2 in triangles:
        area = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
        if area == 0:
            continue
        step = -1 if area > 0 else 1
        if area < 0:
            p1, p2, area = p2, p1, -area
        low, high = np.minimum(np.minimum(p0, p1), p2), np.maximum(np.maximum(p0, p1), p2)
        i0, i1 = np.searchsorted(xs, low[0]), np.searchsorted(xs, high[0], side="right")
        j0, j1 = np.searchsorted(ys, low[1]), np.searchsorted(ys, high[1], side="right")
        if i0 >= i1 or j0 >= j1:
            continue
        px, py = xs[i0:i1][None, :], ys[j0:j1][:, None]
        w0, w1, w2 = _edge(p1, p2, px, py), _edge(p2, p0, px, py), _edge(p0, p1, px, py)
        inside = _covers(w0, p1, p2) & _covers(w1, p2, p0) & _covers(w2, p0, p1)
        jj, ii = np.nonzero(inside)
        if jj.size == 0:
            continue
        z = (w0[jj, ii] * p0[2] + w1[jj, ii] * p1[2] + w2[jj, ii] * p2[2]) / area
        columns.append((jj + j0) * xs.size + ii + i0)
        heights.append(z)
        steps.append(np.full(z.size, step, dtype=np.int32))
    if not columns:
        empty = np.zeros(0, dtype=np.int64)
        return empty, empty.astype(np.float64), empty.astype(np.int32)
    return np.concatenate(columns), np.concatenate(heights), np.concatenate(steps)


def _occupancy(triangles: np.ndarray, xs: np.ndarray, ys: np.ndarray, zs: np.ndarray) -> Any:
    """Voxels inside the surface: nonzero winding depth between crossings of each column ray."""
    columns, heights, steps = _crossings(triangles, xs, ys)
    order = np.lexsort((heights, columns))
    columns, heights, steps = columns[order], heights[order], steps[order]
    total = np.cumsum(steps)
    depth = total - (total - steps)[np.searchsorted(columns, columns)]
    active = (depth[:-1] != 0) & (columns[:-1] == columns[1:])
    k0 = np.searchsorted(zs, heights[:-1][active])
    k1 = np.searchsorted(zs, heights[1:][active])
    diff = np.zeros((zs.size + 1, xs.size * ys.size), dtype=np.int16)
    np.add.at(diff, (k0, columns[:-1][active]), 1)
    np.add.at(diff, (k1, columns[:-1][active]), -1)
    inside = np.cumsum(diff, axis=0)[:-1] > 0
    return inside.reshape(zs.size, ys.size, xs.size)


def _voxel_colors(
    tree: BVHTree,
    colors: np.ndarray,
    filled: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
    zs: np.ndarray,
    depth: int,
) -> np.ndarray:
    """Shell voxels take the nearest face colour; interior voxels inherit from the voxel above."""
    rgb = np.zeros((*filled.shape, 3), dtype=np.float32)
    shell = _shell(filled, depth)
    for k, j, i in zip(*np.nonzero(shell), strict=True):
        _location, _normal, index, _distance = tree.find_nearest(Vector((xs[i], ys[j], zs[k])))
        if index is not None:
            rgb[k, j, i] = colors[index]
    interior = filled & ~shell
    for k in range(filled.shape[0] - 2, -1, -1):
        rgb[k][interior[k]] = rgb[k + 1][interior[k]]
    return rgb


def _snap(rgb: np.ndarray, filled: np.ndarray, swatches: np.ndarray) -> None:
    unique, inverse = np.unique(rgb[filled].reshape(-1, 3), axis=0, return_inverse=True)
    rgb[filled] = swatches[palette.nearest(unique, swatches)][inverse.reshape(-1)]


def slice_mesh(
    obj: bpy.types.Object,
    depsgraph: bpy.types.Depsgraph,
    resolution: int,
    depth: int,
    swatches: np.ndarray | None,
) -> np.ndarray:
    """Cubic-voxel slices (z, y, x, rgba) of the mesh, longest XY side fitted to resolution."""
    world, polygons, mesh = _world_mesh(obj, depsgraph)
    if not polygons:
        raise ValueError("Mesh has no faces")
    low, high = world.min(axis=0), world.max(axis=0)
    voxel = max(high[0] - low[0], high[1] - low[1]) / resolution
    if voxel <= 0:
        raise ValueError("Mesh has no XY extent")
    slices = max(1, math.ceil((high[2] - low[2]) / voxel - 1e-3))
    if slices > _MAX_SLICES:
        raise ValueError(f"{slices} slices exceed the {_MAX_SLICES} limit, lower the resolution")
    tree = BVHTree.FromPolygons(world.tolist(), polygons)
    centre = (low + high) / 2
    offsets = (np.arange(resolution) + 0.5 - resolution / 2) * voxel
    xs, ys = centre[0] + offsets, centre[1] + offsets
    zs = low[2] + (np.arange(slices) + 0.5) * voxel
    filled = _occupancy(_triangles(mesh, world), xs, ys, zs)
    rgb = _voxel_colors(tree, face_colors(obj, mesh), filled, xs, ys, zs, depth)
    if swatches is not None and filled.any():
        _snap(rgb, filled, swatches)
    out = np.zeros((slices, resolution, resolution, 4), dtype=np.float32)
    out[..., :3] = rgb
    out[..., 3] = filled
    return out


def color_index(
    voxels: np.ndarray, filled: np.ndarray, swatches: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray]:
    """Atlas colours (sRGB rows) and per-voxel row index; a loaded palette is the atlas."""
    bytes_ = np.rint(voxels[..., :3] * 255).astype(np.uint32)
    packed = bytes_[..., 0] << 16 | bytes_[..., 1] << 8 | bytes_[..., 2]
    unique, inverse = np.unique(packed[filled], return_inverse=True)
    channels = np.stack([unique >> 16 & 0xFF, unique >> 8 & 0xFF, unique & 0xFF], axis=1)
    colors = channels.astype(np.float32) / 255
    index = np.zeros(filled.shape, dtype=np.int64)
    if swatches is None:
        index[filled] = inverse
        return colors, index
    index[filled] = palette.nearest(colors, swatches)[inverse]
    return swatches, index


def quads(filled: np.ndarray, index: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Exposed voxel faces: shared integer corners (x, y, z), quads per face, colour per face."""
    corners: list[np.ndarray] = []
    colors: list[np.ndarray] = []
    for spatial in range(3):
        b, c = (spatial + 1) % 3, (spatial + 2) % 3
        for sign in (1, -1):
            k, j, i = np.nonzero(_exposed(filled, 2 - spatial, sign))
            if k.size == 0:
                continue
            offsets = np.zeros((4, 3), dtype=np.int64)
            offsets[:, spatial] = sign > 0
            order = _CORNERS if sign > 0 else _CORNERS[::-1]
            for n, (ob, oc) in enumerate(order):
                offsets[n, b], offsets[n, c] = ob, oc
            corners.append(np.stack([i, j, k], axis=1)[:, None, :] + offsets[None, :, :])
            colors.append(index[k, j, i])
    all_corners = np.concatenate(corners)
    verts, inverse = np.unique(all_corners.reshape(-1, 3), axis=0, return_inverse=True)
    return verts, inverse.reshape(-1, 4), np.concatenate(colors)


def build_mesh(
    name: str, filled: np.ndarray, index: np.ndarray, atlas_size: int, scale: float
) -> bpy.types.Mesh:
    """Shell mesh of the voxels, same-colour coplanar faces merged, UVs on the colour's texel."""
    verts, faces, colors = quads(filled, index)
    _depth, height, width = filled.shape
    positions = (verts - np.array([width / 2, height / 2, 0.0])) * scale
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions.tolist(), [], faces.tolist())
    uv_layer = mesh.uv_layers.new(name="UVMap")
    assert uv_layer is not None
    u = np.repeat((colors + 0.5) / atlas_size, 4)
    uvs = np.stack([u, np.full(u.size, 0.5)], axis=1).astype(np.float32)
    uv_layer.data.foreach_set("uv", uvs.ravel())
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.dissolve_limit(
        bm,
        angle_limit=math.radians(0.1),
        verts=bm.verts[:],
        edges=bm.edges[:],
        delimit={"UV"},
    )
    bm.to_mesh(mesh)
    bm.free()
    mesh.validate()
    return mesh


def _atlas_image(name: str, colors: np.ndarray) -> bpy.types.Image:
    image = bpy.data.images.new(name, len(colors), 1, alpha=True)
    pixels = np.ones((1, len(colors), 4), dtype=np.float32)
    pixels[0, :, :3] = colors
    select.write_pixels(image, pixels)
    image.pack()
    return image


def _palette_material(name: str, atlas: bpy.types.Image) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    tree = material.node_tree
    assert tree is not None
    bsdf = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
    texture = cast(Any, tree.nodes.new("ShaderNodeTexImage"))
    texture.image = atlas
    texture.interpolation = "Closest"
    texture.location = (bsdf.location.x - 300, bsdf.location.y)
    tree.links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
    return material


class BLIX_OT_mesh_slice(bpy.types.Operator):
    """Slice the source mesh into sprite stack layers on a new canvas"""

    bl_idname = "blix.mesh_slice"
    bl_label = "Slice Mesh"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        scene = context.scene
        if scene is None:
            return False
        obj = props.voxel_object(scene)
        return obj is not None and obj.type == "MESH"

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        scene = context.scene
        assert scene is not None
        obj = props.voxel_object(scene)
        assert obj is not None
        resolution = props.voxel_resolution(scene)
        depth = props.voxel_depth(scene)
        swatches = _swatches(context)
        try:
            depsgraph = context.evaluated_depsgraph_get()
            slices = slice_mesh(obj, depsgraph, resolution, depth, swatches)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        canvas = bpy.data.images.new(f"{obj.name}_stack", resolution, resolution, alpha=True)
        _layers_from_slices(context, canvas, slices, "Blix Slice Mesh")
        note = "" if swatches is not None else ", no palette so material colours kept"
        self.report({"INFO"}, f"{len(slices)} slices at {resolution} px{note}")
        return {"FINISHED"}


class BLIX_OT_mesh_build(bpy.types.Operator):
    """Build a shell mesh from the visible layer stack, UV-mapped to a palette texture"""

    bl_idname = "blix.mesh_build"
    bl_label = "Build Mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return layers.resolve_canvas(context) is not None

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None
        assert scene is not None
        layers.sync_canvas(canvas)
        voxels = stack_voxels(canvas)
        filled = voxels[..., 3] > 0
        if not filled.any():
            self.report({"ERROR"}, "No visible pixels")
            return {"CANCELLED"}
        colors, index = color_index(voxels, filled, _swatches(context))
        atlas = _atlas_image(f"{canvas.name}_palette", colors)
        scale = props.voxel_scale(scene)
        mesh = build_mesh(f"{canvas.name}_mesh", filled, index, len(colors), scale)
        mesh.materials.append(_palette_material(f"{canvas.name}_palette", atlas))
        obj = bpy.data.objects.new(f"{canvas.name}_mesh", mesh)
        collection = context.collection or scene.collection
        collection.objects.link(obj)
        view_layer = context.view_layer
        if view_layer is not None:
            view_layer.objects.active = obj
        self.report({"INFO"}, f"{len(mesh.polygons)} faces, {len(colors)} colours")
        return {"FINISHED"}


class BLIX_OT_stack_import(bpy.types.Operator, ImportHelper):
    """Load a horizontal sprite-stack strip as layers on a new canvas, bottom slice first"""

    bl_idname = "blix.stack_import"
    bl_label = "Import Sprite Stack"
    bl_options = {"REGISTER"}

    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(default="*.png", options={"HIDDEN"})
    slice_width: bpy.props.IntProperty(
        name="Slice Width",
        description="Width of one slice in pixels, 0 uses the strip height",
        default=0,
        min=0,
    )

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        path = Path(cast(Any, self).filepath)
        try:
            strip = bpy.data.images.load(str(path))
        except RuntimeError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        try:
            pixels = select.read_pixels(strip)
        finally:
            bpy.data.images.remove(strip)
        height, width = pixels.shape[:2]
        slice_width = self.slice_width or height
        if slice_width > width or width % slice_width:
            self.report({"ERROR"}, f"Strip width {width} is not a multiple of {slice_width}")
            return {"CANCELLED"}
        slices = np.split(pixels, width // slice_width, axis=1)
        canvas = bpy.data.images.new(path.stem, slice_width, height, alpha=True)
        _layers_from_slices(context, canvas, slices, "Blix Import Stack")
        self.report({"INFO"}, f"{len(slices)} slices from {path.name}")
        return {"FINISHED"}


def _is_mesh(_self: Any, obj: bpy.types.Object) -> bool:
    return obj.type == "MESH"


_classes = (BLIX_OT_mesh_slice, BLIX_OT_mesh_build, BLIX_OT_stack_import)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_voxel_object = bpy.props.PointerProperty(
        type=bpy.types.Object,
        name="Mesh",
        description="Mesh object to slice into stack layers",
        poll=_is_mesh,
    )
    scene_cls.blix_voxel_resolution = bpy.props.IntProperty(
        name="Resolution",
        description="Canvas size in pixels, the longest XY side of the mesh fits it",
        default=32,
        min=2,
        max=256,
    )
    scene_cls.blix_voxel_depth = bpy.props.IntProperty(
        name="Paint Depth",
        description="Pixels inward from the surface that take the face colour",
        default=1,
        min=1,
        soft_max=8,
        max=64,
    )
    scene_cls.blix_voxel_scale = bpy.props.FloatProperty(
        name="Voxel Size",
        description="Edge length of one canvas pixel in the built mesh",
        default=0.1,
        min=0.0001,
        soft_max=10.0,
        precision=3,
        unit="LENGTH",
    )


def unregister() -> None:
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_voxel_scale
    del scene_cls.blix_voxel_depth
    del scene_cls.blix_voxel_resolution
    del scene_cls.blix_voxel_object
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
