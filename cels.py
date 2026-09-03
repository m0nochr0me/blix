"""Cel animation: layers as frames, ranges keyed as Scene-hosted visibility tracks."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ExportHelper

from . import overlay, props, select

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

_PATH = re.compile(r"blix_cels\[(\d+)\](.*)")
Range = tuple[int, int]

MODE_ITEMS = (
    ("STRIP", "Strip", "All frames side by side in one image, first frame left"),
    ("SEQUENCE", "PNG Sequence", "One numbered file per frame"),
)


def track(scene: bpy.types.Scene, canvas: bpy.types.Image, layer: Any) -> Any:
    for item in props.cels(scene):
        if item.canvas == canvas and item.number == layer.number and item.copy == layer.copy:
            return item
    return None


def animated(scene: bpy.types.Scene, canvas: bpy.types.Image) -> bool:
    return any(item.canvas == canvas for item in props.cels(scene))


def on(canvas: bpy.types.Image, layer: Any) -> bool:
    """Animated visibility of layer at the current frame; True when the canvas has no tracks."""
    scene = bpy.context.scene
    if scene is None:
        return True
    item = track(scene, canvas, layer)
    return True if item is None else bool(item.visible)


def _channelbag(scene: bpy.types.Scene) -> Any:
    data = scene.animation_data
    if data is None or data.action is None or data.action_slot is None:
        return None
    action_layers = data.action.layers
    if not len(action_layers) or not len(action_layers[0].strips):
        return None
    return cast(Any, action_layers[0].strips[0]).channelbag(data.action_slot)


def _fcurve(scene: bpy.types.Scene, item: Any, name: str = "visible") -> Any:
    bag = _channelbag(scene)
    if bag is None:
        return None
    return bag.fcurves.find(item.path_from_id(name))


def _group(scene: bpy.types.Scene, item: Any, curve: Any) -> None:
    """File the curve under a dope sheet group named after the track's layer."""
    bag = _channelbag(scene)
    curve.group = bag.groups.get(item.name) or bag.groups.new(item.name)


def _rewrite(scene: bpy.types.Scene, item: Any, name: str, pairs: list[tuple[int, int]]) -> None:
    """Replace the keys of one track property with (frame, value) pairs, stepped for cel indices."""
    curve = _fcurve(scene, item, name)
    if curve is not None:
        _channelbag(scene).fcurves.remove(curve)
    if not pairs:
        return
    path = item.path_from_id(name)
    for frame, value in pairs:
        setattr(item, name, value)
        scene.keyframe_insert(path, frame=frame)
    curve = _fcurve(scene, item, name)
    if name == "cel":
        for point in curve.keyframe_points:
            point.interpolation = "CONSTANT"
        curve.update()
    _group(scene, item, curve)
    setattr(item, name, round(curve.evaluate(scene.frame_current)))


def _pairs(curve: Any) -> list[tuple[int, int]]:
    return [(round(point.co.x), round(point.co.y)) for point in curve.keyframe_points]


def _write_cel_key(scene: bpy.types.Scene, item: Any, frame: int, index: int) -> None:
    curve = _fcurve(scene, item, "cel")
    pairs = [] if curve is None else [pair for pair in _pairs(curve) if pair[0] != frame]
    _rewrite(scene, item, "cel", [*pairs, (frame, index)])


def slot_at(scene: bpy.types.Scene, canvas: bpy.types.Image, layer: Any, frame: int) -> int | None:
    """Index of the cel slot the layer shows at frame; None for a layer without cels."""
    count = len(layer.cels)
    if count == 0:
        return None
    item = track(scene, canvas, layer)
    if item is None:
        from . import layers

        slot = layers.current_slot(layer)
        return 0 if slot is None else list(layer.cels).index(slot)
    curve = _fcurve(scene, item, "cel")
    value = item.cel if curve is None else round(curve.evaluate(frame))
    return min(max(int(value), 0), count - 1)


def _add_track(scene: bpy.types.Scene, canvas: bpy.types.Image, layer: Any) -> Any:
    from . import layers

    item = props.cels(scene).add()
    item.name = f"{canvas.name}/{layers.layer_tag(layer)}"
    item.canvas = canvas
    item.number = layer.number
    item.copy = layer.copy
    return item


def _remove_track(scene: bpy.types.Scene, index: int) -> None:
    """Drop a track with its curves, then re-index curve paths of the tracks after it."""
    bag = _channelbag(scene)
    if bag is not None:
        prefix = f"blix_cels[{index}]."
        for curve in [curve for curve in bag.fcurves if curve.data_path.startswith(prefix)]:
            bag.fcurves.remove(curve)
        for curve in bag.fcurves:
            match = _PATH.match(curve.data_path)
            if match is not None and int(match.group(1)) > index:
                curve.data_path = f"blix_cels[{int(match.group(1)) - 1}]{match.group(2)}"
    props.cels(scene).remove(index)


def sync(scene: bpy.types.Scene, canvas: bpy.types.Image) -> None:
    """Keep one track per layer of an animated canvas: add missing ones, drop orphans."""
    if not animated(scene, canvas):
        return
    stack = list(props.layers(canvas))
    keys = {(layer.number, layer.copy) for layer in stack}
    items = props.cels(scene)
    for index in reversed(range(len(items))):
        item = items[index]
        if item.canvas == canvas and (item.number, item.copy) not in keys:
            _remove_track(scene, index)
    for layer in stack:
        if track(scene, canvas, layer) is None:
            _add_track(scene, canvas, layer)


def enable(scene: bpy.types.Scene, canvas: bpy.types.Image) -> None:
    for layer in props.layers(canvas):
        if track(scene, canvas, layer) is None:
            _add_track(scene, canvas, layer)


def disable(scene: bpy.types.Scene, canvas: bpy.types.Image) -> None:
    items = props.cels(scene)
    for index in reversed(range(len(items))):
        if items[index].canvas == canvas:
            _remove_track(scene, index)


def ranges(scene: bpy.types.Scene, item: Any) -> list[Range]:
    """On-ranges keyed on the track in frame order; empty when unkeyed or never on."""
    curve = _fcurve(scene, item)
    if curve is None:
        return []
    found: list[Range] = []
    first: int | None = None
    for point in curve.keyframe_points:
        frame = round(point.co.x)
        if point.co.y >= 0.5:
            first = frame if first is None else first
        elif first is not None:
            found.append((first, frame - 1))
            first = None
    if first is not None:
        found.append((first, scene.frame_end))
    return found


def range_at(scene: bpy.types.Scene, item: Any, frame: int) -> Range:
    """Range containing frame, else the next one after it, else the last one before it."""
    if _fcurve(scene, item) is None:
        return scene.frame_start, scene.frame_end
    found = ranges(scene, item)
    if not found:
        return scene.frame_start, scene.frame_start - 1
    for first, last in found:
        if frame <= last:
            return first, last
    return found[-1]


def _write_ranges(scene: bpy.types.Scene, item: Any, found: list[Range]) -> None:
    """Replace the track's keys with an off/on/off triple per range, merging touching ones."""
    curve = _fcurve(scene, item)
    if curve is not None:
        _channelbag(scene).fcurves.remove(curve)
    merged: list[Range] = []
    for first, last in sorted(found):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    path = item.path_from_id("visible")
    for first, last in merged:
        for frame, value in ((first - 1, False), (first, True), (last + 1, False)):
            item.visible = value
            scene.keyframe_insert(path, frame=frame)
    if merged:
        _group(scene, item, _fcurve(scene, item))
    item.visible = any(first <= scene.frame_current <= last for first, last in merged)


def set_range(scene: bpy.types.Scene, item: Any, first: int, last: int) -> None:
    """Key the track on from first to last inclusive and off elsewhere."""
    _write_ranges(scene, item, [(first, last)])


def boundaries(scene: bpy.types.Scene, canvas: bpy.types.Image) -> list[int]:
    """Frames where any track of the canvas changes value, sorted."""
    bag = _channelbag(scene)
    if bag is None:
        return []
    frames: set[int] = set()
    for index, item in enumerate(props.cels(scene)):
        if item.canvas != canvas:
            continue
        prefix = f"blix_cels[{index}]."
        for curve in bag.fcurves:
            if not curve.data_path.startswith(prefix):
                continue
            previous: int | None = None
            for point in curve.keyframe_points:
                value = round(point.co.y)
                if previous is not None and value != previous:
                    frames.add(round(point.co.x))
                previous = value
    return sorted(frames)


def _curves(scene: bpy.types.Scene, canvas: bpy.types.Image) -> list[tuple[Any, Any]]:
    """(track, curve) pairs for every keyed property of the canvas's tracks."""
    bag = _channelbag(scene)
    if bag is None:
        return []
    found: list[tuple[Any, Any]] = []
    for index, item in enumerate(props.cels(scene)):
        if item.canvas != canvas:
            continue
        prefix = f"blix_cels[{index}]."
        found += [(item, curve) for curve in bag.fcurves if curve.data_path.startswith(prefix)]
    return found


def _shift(
    scene: bpy.types.Scene, canvas: bpy.types.Image, after: int, delta: int, keep_end: bool = False
) -> None:
    """Move keys past `after` by delta; keep_end leaves off keys at after+1 so cels end there."""
    for _item, curve in _curves(scene, canvas):
        visible = curve.data_path.endswith(".visible")
        for point in curve.keyframe_points:
            ends_here = keep_end and visible and point.co.x == after + 1 and point.co.y < 0.5
            if point.co.x > after and not ends_here:
                point.co.x += delta
                point.handle_left.x += delta
                point.handle_right.x += delta
        curve.update()


def _drop_frames(scene: bpy.types.Scene, canvas: bpy.types.Image, after: int, count: int) -> None:
    """Delete keys inside (after, after+count] and pull the later keys back by count."""
    for item, curve in _curves(scene, canvas):
        name = curve.data_path.rsplit(".", 1)[1]
        kept = [
            (frame, value) for frame, value in _pairs(curve) if not after < frame <= after + count
        ]
        _rewrite(
            scene,
            item,
            name,
            [(frame - count if frame > after else frame, value) for frame, value in kept],
        )


def _sanitize(scene: bpy.types.Scene, canvas: bpy.types.Image) -> None:
    """Step every cel-index key so hand-inserted keys never show in-between cels."""
    for _item, curve in _curves(scene, canvas):
        if not curve.data_path.endswith(".cel"):
            continue
        soft = [point for point in curve.keyframe_points if point.interpolation != "CONSTANT"]
        for point in soft:
            point.interpolation = "CONSTANT"
        if soft:
            curve.update()


def _swap_slots(scene: bpy.types.Scene, canvas: bpy.types.Image) -> None:
    """Point every layer with cels at the slot keyed for the current frame."""
    for layer in props.layers(canvas):
        index = slot_at(scene, canvas, layer, scene.frame_current)
        if index is None:
            continue
        target = layer.cels[index].image
        if target is None or target == layer.image:
            continue
        previous = layer.image
        layer.image = target
        _retarget_editors(canvas, target, previous)


def _retarget_editors(
    canvas: bpy.types.Image, image: bpy.types.Image, previous: bpy.types.Image | None = None
) -> None:
    """Point image editors showing previous (or any layer image of canvas) at image."""
    for manager in bpy.data.window_managers:
        for window in manager.windows:
            for area in window.screen.areas:
                if area.type != "IMAGE_EDITOR":
                    continue
                space = cast(bpy.types.SpaceImageEditor, area.spaces.active)
                shown = space.image
                if shown is None or shown in (canvas, image):
                    continue
                if previous is not None and shown != previous:
                    continue
                if props.canvas_of(shown) == canvas:
                    space.image = image


def _follow(canvas: bpy.types.Image) -> None:
    from . import layers

    stack = props.layers(canvas)
    index = next(
        (
            position
            for position, layer in enumerate(stack)
            if layer.cel and layer.image is not None and layers.shown(canvas, layer)
        ),
        None,
    )
    if index is None:
        return
    if index != props.layers_index(canvas):
        props.set_layers_index(canvas, index)
    _retarget_editors(canvas, stack[index].image)


def _apply(scene: bpy.types.Scene, canvas: bpy.types.Image) -> None:
    from . import layers

    layers.sync_canvas(canvas)
    _swap_slots(scene, canvas)
    if props.cel_follow(scene):
        _follow(canvas)
    layers._heal(canvas)


def refresh(scene: bpy.types.Scene) -> None:
    """Recomposite every animated canvas of the scene for the current frame, no undo step."""
    canvases = {
        item.canvas.name: item.canvas for item in props.cels(scene) if item.canvas is not None
    }
    for canvas in canvases.values():
        if len(props.layers(canvas)):
            _sanitize(scene, canvas)
            _apply(scene, canvas)
    if canvases:
        overlay.tag_redraw(bpy.context)


@persistent
def _frame_changed(scene: bpy.types.Scene, *_args: Any) -> None:
    refresh(scene)


def _track_changed(self: bpy.types.PropertyGroup, context: bpy.types.Context) -> None:
    scene = cast(bpy.types.Scene, self.id_data)
    canvas = cast(Any, self).canvas
    if canvas is not None and len(props.layers(canvas)):
        _apply(scene, canvas)
        overlay.tag_redraw(context)


def _current_range(self: bpy.types.PropertyGroup) -> Range:
    scene = cast(bpy.types.Scene, self.id_data)
    return range_at(scene, self, scene.frame_current)


def _replace_range(self: bpy.types.PropertyGroup, edited: Range) -> None:
    """Swap the range at the current frame for edited, keeping the track's other ranges."""
    scene = cast(bpy.types.Scene, self.id_data)
    current = _current_range(self)
    others = [found for found in ranges(scene, self) if found != current]
    _write_ranges(scene, self, [*others, edited])
    refresh(scene)


def _get_start(self: bpy.types.PropertyGroup) -> int:
    return _current_range(self)[0]


def _set_start(self: bpy.types.PropertyGroup, value: int) -> None:
    _first, last = _current_range(self)
    _replace_range(self, (value, max(last, value)))


def _get_end(self: bpy.types.PropertyGroup) -> int:
    return _current_range(self)[1]


def _set_end(self: bpy.types.PropertyGroup, value: int) -> None:
    first, _last = _current_range(self)
    _replace_range(self, (min(first, value), value))


class BlixCel(bpy.types.PropertyGroup):
    canvas: bpy.props.PointerProperty(type=bpy.types.Image)
    number: bpy.props.IntProperty(name="Number", min=0)
    copy: bpy.props.IntProperty(name="Copy", min=0)
    visible: bpy.props.BoolProperty(name="Visible", default=True, update=_track_changed)
    cel: bpy.props.IntProperty(
        name="Cel",
        description="Cel slot the layer shows at this frame",
        min=0,
        update=_track_changed,
    )
    frame_start: bpy.props.IntProperty(
        name="Start",
        description="First frame the layer is shown on",
        options=set(),
        get=_get_start,
        set=_set_start,
    )
    frame_end: bpy.props.IntProperty(
        name="End",
        description="Last frame the layer is shown on",
        options=set(),
        get=_get_end,
        set=_set_end,
    )


class _CelOperator(bpy.types.Operator):
    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        return canvas is not None and scene is not None and animated(scene, canvas)


class BLIX_OT_cels_enable(bpy.types.Operator):
    """Animate the layers of this canvas in the current scene, one visibility track per layer"""

    bl_idname = "blix.cels_enable"
    bl_label = "Animate Layers"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        return canvas is not None and scene is not None and not animated(scene, canvas)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        enable(scene, canvas)
        return {"FINISHED"}


class BLIX_OT_cels_disable(_CelOperator):
    """Remove the layer animation tracks of this canvas from the current scene"""

    bl_idname = "blix.cels_disable"
    bl_label = "Stop Animating"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        disable(scene, canvas)
        layers._heal(canvas)
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_OT_cels_layout(_CelOperator):
    """Give every cel layer a consecutive frame range and fit the scene range to them"""

    bl_idname = "blix.cels_layout"
    bl_label = "Layout Cels"
    bl_options = {"REGISTER", "UNDO"}

    start: bpy.props.IntProperty(name="Start Frame", default=1)
    hold: bpy.props.IntProperty(name="Frames per Cel", default=1, min=1)
    top_first: bpy.props.BoolProperty(
        name="Top Layer First", description="Order cels from the top of the stack", default=False
    )

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        assert context.scene is not None and context.window_manager is not None
        self.start = context.scene.frame_start
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        cel_layers = [layer for layer in props.layers(canvas) if layer.cel]
        if not cel_layers:
            self.report({"ERROR"}, "No cel layers")
            return {"CANCELLED"}
        if not self.top_first:
            cel_layers.reverse()
        sync(scene, canvas)
        frame = self.start
        for layer in cel_layers:
            set_range(scene, track(scene, canvas, layer), frame, frame + self.hold - 1)
            frame += self.hold
        scene.frame_start = self.start
        scene.frame_end = frame - 1
        refresh(scene)
        return {"FINISHED"}


class BLIX_OT_cel_insert(_CelOperator):
    """Add a new cel after the active one with the same length, shifting later cels"""

    bl_idname = "blix.cel_insert"
    bl_label = "Insert Cel"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        from . import layers

        if not super().poll(context):
            return False
        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        layer = layers.active_layer(canvas)
        return layer is not None and layer.cel and track(scene, canvas, layer) is not None

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        layer = layers.active_layer(canvas)
        first, last = range_at(scene, track(scene, canvas, layer), scene.frame_current)
        hold = max(last - first + 1, 1)
        with layers._undo_step(context, canvas, "Blix Insert Cel"):
            layers.add_layer(canvas, layers.next_layer_number(canvas))
            sync(scene, canvas)
            _shift(scene, canvas, last, hold, keep_end=True)
            added = track(scene, canvas, layers.active_layer(canvas))
            set_range(scene, added, last + 1, last + hold)
            scene.frame_end += hold
        scene.frame_set(last + 1)
        return {"FINISHED"}


class _SlotOperator(_CelOperator):
    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        from . import layers

        if not super().poll(context):
            return False
        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        layer = layers.active_layer(canvas)
        if layer is None or layer.image is None or track(scene, canvas, layer) is None:
            return False
        return cls.slots_ok(len(layer.cels))

    @classmethod
    def slots_ok(cls, count: int) -> bool:
        return True


class BLIX_OT_slot_add(_SlotOperator):
    """Start a new cel for the active layer at the current frame, blank or copied"""

    bl_idname = "blix.slot_add"
    bl_label = "Add Cel"
    bl_options = {"REGISTER", "INTERNAL"}

    duplicate: bpy.props.BoolProperty(default=False)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        layer = layers.active_layer(canvas)
        item = track(scene, canvas, layer)
        with layers._undo_step(context, canvas, "Blix Add Cel"):
            fresh = len(layer.cels) == 0
            pixels = select.read_pixels(layer.image) if self.duplicate else None
            layers.add_slot(canvas, layer, pixels)
            if fresh:
                _write_cel_key(scene, item, min(scene.frame_start, scene.frame_current - 1), 0)
            _write_cel_key(scene, item, scene.frame_current, len(layer.cels) - 1)
        refresh(scene)
        return {"FINISHED"}


class BLIX_OT_slot_remove(_SlotOperator):
    """Delete the cel the active layer shows at the current frame"""

    bl_idname = "blix.slot_remove"
    bl_label = "Delete Cel"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def slots_ok(cls, count: int) -> bool:
        return count > 1

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        layer = layers.active_layer(canvas)
        item = track(scene, canvas, layer)
        index = slot_at(scene, canvas, layer, scene.frame_current)
        assert index is not None
        with layers._undo_step(context, canvas, "Blix Delete Cel"):
            curve = _fcurve(scene, item, "cel")
            if curve is not None:
                kept = [(frame, value) for frame, value in _pairs(curve) if value != index]
                _rewrite(
                    scene,
                    item,
                    "cel",
                    [(frame, value - 1 if value > index else value) for frame, value in kept],
                )
            removed = layer.cels[index].image
            layer.cels.remove(index)
            if layer.image == removed:
                layer.image = layer.cels[min(index, len(layer.cels) - 1)].image
        refresh(scene)
        return {"FINISHED"}


class BLIX_OT_frames_insert(_CelOperator):
    """Insert frames after the current one, shifting every later key of this canvas"""

    bl_idname = "blix.frames_insert"
    bl_label = "Insert Frame"
    bl_options = {"REGISTER", "UNDO"}

    count: bpy.props.IntProperty(name="Frames", default=1, min=1)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        _shift(scene, canvas, scene.frame_current, self.count)
        scene.frame_end += self.count
        refresh(scene)
        return {"FINISHED"}


class BLIX_OT_frames_remove(_CelOperator):
    """Remove frames after the current one; cels starting inside them are dropped"""

    bl_idname = "blix.frames_remove"
    bl_label = "Remove Frame"
    bl_options = {"REGISTER", "UNDO"}

    count: bpy.props.IntProperty(name="Frames", default=1, min=1)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        _drop_frames(scene, canvas, scene.frame_current, self.count)
        scene.frame_end = max(scene.frame_end - self.count, scene.frame_start)
        refresh(scene)
        return {"FINISHED"}


class BLIX_OT_cel_jump(_CelOperator):
    """Jump to the previous or next frame where a cel changes"""

    bl_idname = "blix.cel_jump"
    bl_label = "Jump to Cel"
    bl_options = {"REGISTER", "INTERNAL"}

    forward: bpy.props.BoolProperty(default=True)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        frame = scene.frame_current
        found = boundaries(scene, canvas)
        targets = (
            [f for f in found if f > frame] if self.forward else [f for f in found if f < frame]
        )
        if not targets:
            return {"CANCELLED"}
        scene.frame_set(targets[0] if self.forward else targets[-1])
        return {"FINISHED"}


class BLIX_OT_cels_export(_CelOperator, ExportHelper):
    """Export the composite of every frame in the scene range"""

    bl_idname = "blix.cels_export"
    bl_label = "Export Animation"
    bl_options = {"REGISTER"}

    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(default="*.png", options={"HIDDEN"})
    mode: bpy.props.EnumProperty(name="Mode", items=MODE_ITEMS, default="STRIP")

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        assert canvas is not None
        cast(Any, self).filepath = f"{canvas.name}_anim.png"
        return cast("set[OperatorReturnItems]", ExportHelper.invoke(self, context, event))

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers, stacking

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        layers.sync_canvas(canvas)
        path = Path(cast(Any, self).filepath)
        current = scene.frame_current
        frames = range(scene.frame_start, scene.frame_end + 1)
        strip: list[np.ndarray] = []
        try:
            for frame in frames:
                scene.frame_set(frame)
                pixels = layers.composite_pixels(canvas)
                if self.mode == "STRIP":
                    strip.append(pixels)
                    continue
                stacking.save_strip(
                    pixels, str(path.with_name(f"{path.stem}_{frame:04d}{path.suffix}"))
                )
            if strip:
                stacking.save_strip(np.concatenate(strip, axis=1), str(path))
        except (RuntimeError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        finally:
            scene.frame_set(current)
        self.report({"INFO"}, f"{len(frames)} frames to {path.name}")
        return {"FINISHED"}


_classes = (
    BlixCel,
    BLIX_OT_cels_enable,
    BLIX_OT_cels_disable,
    BLIX_OT_cels_layout,
    BLIX_OT_cel_insert,
    BLIX_OT_slot_add,
    BLIX_OT_slot_remove,
    BLIX_OT_frames_insert,
    BLIX_OT_frames_remove,
    BLIX_OT_cel_jump,
    BLIX_OT_cels_export,
)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_cels = bpy.props.CollectionProperty(type=BlixCel)
    scene_cls.blix_cel_follow = bpy.props.BoolProperty(
        name="Follow Frame",
        description="Make the cel shown at the current frame the active layer",
        default=True,
    )
    bpy.app.handlers.frame_change_post.append(_frame_changed)


def unregister() -> None:
    bpy.app.handlers.frame_change_post.remove(_frame_changed)
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_cel_follow
    del scene_cls.blix_cels
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
