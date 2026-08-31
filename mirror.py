"""Mirror symmetry for Blix drawing tools: reflect writes across the image center axes."""

from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np

from . import overlay, prefs, props, select, undo

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

Point = tuple[float, float]
POLL = 0.03


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


def flips(horizontal: bool, vertical: bool) -> list[tuple[bool, bool]]:
    """Non-identity flip combinations; both axes add the diagonal quadrant."""
    combos = []
    if horizontal:
        combos.append((True, False))
    if vertical:
        combos.append((False, True))
    if horizontal and vertical:
        combos.append((True, True))
    return combos


def _flip(array: np.ndarray, horizontal: bool, vertical: bool) -> np.ndarray:
    out = array[:, ::-1] if horizontal else array
    return out[::-1] if vertical else out


def apply(
    painted: np.ndarray, base: np.ndarray, mine: np.ndarray, horizontal: bool, vertical: bool
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Copy pixels changed since base onto their mirrored positions; mine tracks own writes."""
    changed = np.any(painted != base, axis=2) & ~mine
    if not changed.any():
        return painted, mine, False
    out = painted.copy()
    written = mine.copy()
    touched = False
    for horizontal_flip, vertical_flip in flips(horizontal, vertical):
        target = _flip(changed, horizontal_flip, vertical_flip) & ~changed
        if not target.any():
            continue
        source = _flip(painted, horizontal_flip, vertical_flip)
        out[target] = source[target]
        written |= target
        touched = True
    return out, written, touched


class _Watch:
    def __init__(self, image_name: str, base: np.ndarray, axes: tuple[bool, bool]) -> None:
        self.image_name = image_name
        self.base = base
        self.mine = np.zeros(base.shape[:2], dtype=bool)
        self.axes = axes
        self.dirty = False


_watch: _Watch | None = None


def _painting() -> bool:
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return False
    for window in window_manager.windows:
        for operator in window.modal_operators:
            if "image_paint" in operator.bl_idname:
                return True
    return False


def busy() -> bool:
    """A native stroke is running or mirror writes are still settling."""
    return _watch is not None or _painting()


def _tick() -> float | None:
    global _watch
    watch = _watch
    if watch is None:
        return None
    image = bpy.data.images.get(watch.image_name)
    if image is None:
        _watch = None
        return None
    pixels = select.read_pixels(image)
    if pixels.shape != watch.base.shape:
        _watch = None
        return None
    painted, watch.mine, touched = apply(pixels, watch.base, watch.mine, *watch.axes)
    if touched:
        select.write_pixels(image, painted)
        watch.dirty = True
        overlay.tag_redraw(bpy.context)
    if _painting():
        return POLL
    _watch = None
    if watch.dirty:
        _bracket(bpy.context, image, painted, watch)
    return None


def _bracket(
    context: bpy.types.Context, image: bpy.types.Image, final: np.ndarray, watch: _Watch
) -> None:
    """Replay mirror writes between undo records so undoing the stroke also clears them."""
    unmirrored = final.copy()
    unmirrored[watch.mine] = watch.base[watch.mine]
    select.write_pixels(image, unmirrored)
    undo.record(context, image)
    select.write_pixels(image, final)
    undo.record(context, image)


class BLIX_OT_mirror_paint(bpy.types.Operator):
    """Mirror the native brush stroke that follows across the enabled axes"""

    bl_idname = "blix.mirror_paint"
    bl_label = "Mirror Paint Stroke"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        scene = context.scene
        if scene is None or not any(enabled(scene)):
            return False
        image = select.edit_image(context)
        return image is not None and image.size[0] > 0 and image.size[1] > 0

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        global _watch
        image = select.edit_image(context)
        scene = context.scene
        assert image is not None
        assert scene is not None
        if bpy.app.timers.is_registered(_tick):
            bpy.app.timers.unregister(_tick)
        if _watch is not None:
            _tick()
        _watch = _Watch(image.name, select.read_pixels(image), enabled(scene))
        bpy.app.timers.register(_tick, first_interval=POLL)
        return {"PASS_THROUGH"}


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


_keymaps: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def _register_keymap() -> None:
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return
    keyconfig = window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="Image Paint", space_type="EMPTY")
    item = keymap.keymap_items.new(BLIX_OT_mirror_paint.bl_idname, "LEFTMOUSE", "PRESS", any=True)
    _keymaps.append((keymap, item))


def _unregister_keymap() -> None:
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()


def register() -> None:
    global _watch
    _watch = None
    bpy.utils.register_class(BLIX_OT_mirror_paint)
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_mirror_h = bpy.props.BoolProperty(
        name="Mirror H",
        description="Mirror strokes left to right across the vertical center axis",
        default=False,
    )
    scene_cls.blix_mirror_v = bpy.props.BoolProperty(
        name="Mirror V",
        description="Mirror strokes top to bottom across the horizontal center axis",
        default=False,
    )
    overlay.extra_draws.append(_draw_axes)
    _register_keymap()


def unregister() -> None:
    global _watch
    _unregister_keymap()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _watch = None
    overlay.extra_draws.remove(_draw_axes)
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_mirror_v
    del scene_cls.blix_mirror_h
    bpy.utils.unregister_class(BLIX_OT_mirror_paint)
