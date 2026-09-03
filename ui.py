"""Image editor sidebar panels."""

from typing import Any, cast

import bpy

from . import cels, layers, props


def _space(context: bpy.types.Context) -> bpy.types.SpaceImageEditor:
    return cast(bpy.types.SpaceImageEditor, context.space_data)


class BLIX_PT_grid(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Pixel Grid"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        assert layout is not None
        assert scene is not None
        col = layout.column()
        col.prop(scene, "blix_show_pixel_grid")
        col.prop(scene, "blix_grid_divisions", text="Major Every")


class BLIX_UL_guides(bpy.types.UIList):
    def draw_item(
        self,
        context: bpy.types.Context | None,
        layout: bpy.types.UILayout,
        data: Any | None,
        item: Any | None,
        icon: int | None,
        active_data: Any,
        active_property: str | None,
        index: int | None = 0,
        flt_flag: int | None = 0,
    ) -> None:
        assert item is not None
        row = layout.row(align=True)
        axis = "Y" if item.orientation == "HORIZONTAL" else "X"
        slot = index if index is not None else 0
        same_axis = props.guides(cast(bpy.types.Image, data))[:slot]
        number = sum(1 for other in same_axis if other.orientation == item.orientation) + 1
        row.prop(item, "position", text=f"{axis}{number}", emboss=False)


class BLIX_PT_guides(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Rulers & Guides"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        assert layout is not None
        assert scene is not None
        row = layout.row(align=True)
        row.prop(scene, "blix_show_rulers", toggle=True)
        row.prop(scene, "blix_show_guides", toggle=True)

        image = _space(context).image
        if image is None:
            return
        split = layout.split(factor=0.9)
        split.template_list(
            "BLIX_UL_guides", "", image, "blix_guides", image, "blix_guides_index", rows=3
        )
        col = split.column(align=True)
        cast(Any, col.operator("blix.guide_add", text="V")).orientation = "VERTICAL"
        cast(Any, col.operator("blix.guide_add", text="H")).orientation = "HORIZONTAL"
        col.separator()
        col.operator("blix.guide_remove", text="", icon="REMOVE")
        col.operator("blix.guide_clear", text="", icon="X")


class BLIX_PT_select(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Selection"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        assert layout is not None
        assert scene is not None
        layout.prop(scene, "blix_select_mode", expand=True)
        layout.prop(scene, "blix_select_brush_size")
        row = layout.row(align=True)
        cast(Any, row.operator("blix.select_rotate90", text="90 CCW")).turns = 1
        cast(Any, row.operator("blix.select_rotate90", text="180")).turns = 2
        cast(Any, row.operator("blix.select_rotate90", text="90 CW")).turns = 3
        row = layout.row(align=True)
        cast(Any, row.operator("blix.select_flip", text="Flip H")).horizontal = True
        cast(Any, row.operator("blix.select_flip", text="Flip V")).horizontal = False
        row = layout.row(align=True)
        row.operator("blix.select_copy", text="Copy")
        row.operator("blix.select_delete", text="Delete")


class BLIX_PT_shapes(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Shapes"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        assert layout is not None
        assert scene is not None
        col = layout.column()
        col.prop(scene, "blix_shape_filled")
        workspace = context.workspace
        assert workspace is not None
        tool = workspace.tools.from_space_image_mode("PAINT", create=False)
        if tool is not None and tool.idname == "blix.shape_hexagon":
            col.prop(scene, "blix_shape_hex_pointy")


class BLIX_PT_mirror(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Mirror"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        assert layout is not None
        assert scene is not None
        row = layout.row(align=True)
        row.prop(scene, "blix_mirror_h", toggle=True)
        row.prop(scene, "blix_mirror_v", toggle=True)
        layout.label(text="Brush, shapes, dither brush", icon="INFO")


class BLIX_UL_layers(bpy.types.UIList):
    def draw_item(
        self,
        context: bpy.types.Context | None,
        layout: bpy.types.UILayout,
        data: Any | None,
        item: Any | None,
        icon: int | None,
        active_data: Any,
        active_property: str | None,
        index: int | None = 0,
        flt_flag: int | None = 0,
    ) -> None:
        assert item is not None
        row = layout.row(align=True)
        tag = row.row(align=True)
        tag.alignment = "LEFT"
        slot = layers.current_slot(item)
        tag_text = layers.layer_tag(item)
        if slot is not None:
            tag_text = f"{tag_text} {layers.slot_tag(slot)}"
        tag.label(text=tag_text)
        row.prop(item, "label", text="", emboss=False)
        canvas = cast(bpy.types.Image, data)
        scene = context.scene if context is not None else None
        if scene is not None and cels.animated(scene, canvas):
            row.active = layers.shown(canvas, item)
            row.prop(
                item,
                "cel",
                text="",
                icon="KEYFRAME_HLT" if item.cel else "KEYFRAME",
                emboss=False,
            )
        row.prop(
            item, "visible", text="", icon="HIDE_OFF" if item.visible else "HIDE_ON", emboss=False
        )
        row.prop(item, "lock", text="", icon="LOCKED" if item.lock else "UNLOCKED", emboss=False)


class BLIX_PT_layers(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Layers"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        assert layout is not None
        image = _space(context).image
        if image is None:
            return
        canvas = image if len(props.layers(image)) else props.canvas_of(image)
        if canvas is None:
            layout.operator("blix.layers_init")
            return

        editing = image != canvas
        layout.operator(
            "blix.layer_view_toggle",
            text="Show Composite" if editing else "Edit Active Layer",
        )
        scene = context.scene
        if scene is not None:
            row = layout.row(align=True)
            row.prop(scene, "blix_layer_dim", toggle=True)
            row.prop(scene, "blix_layer_hatch", toggle=True)
            row.prop(scene, "blix_layer_outline", toggle=True)
        row = layout.row()
        row.template_list(
            "BLIX_UL_layers", "", canvas, "blix_layers", canvas, "blix_layers_index", rows=4
        )
        col = row.column(align=True)
        col.operator("blix.layer_add", text="", icon="ADD")
        col.operator("blix.layer_remove", text="", icon="REMOVE")
        col.operator("blix.layer_duplicate", text="", icon="DUPLICATE")
        col.separator()
        cast(Any, col.operator("blix.layer_move", text="", icon="TRIA_UP")).up = True
        cast(Any, col.operator("blix.layer_move", text="", icon="TRIA_DOWN")).up = False

        layer = layers.active_layer(canvas)
        if layer is not None:
            layout.prop(layer, "blend")
            layout.prop(layer, "opacity")
        row = layout.row(align=True)
        row.operator("blix.layer_merge_down", text="Merge Down")
        row.operator("blix.layer_flatten", text="Flatten")
        layout.operator("blix.layers_update")


class BLIX_PT_cels(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Animation"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        assert layout is not None
        assert scene is not None
        image = _space(context).image
        if image is None:
            return
        canvas = image if len(props.layers(image)) else props.canvas_of(image)
        if canvas is None:
            return
        if not cels.animated(scene, canvas):
            layout.operator("blix.cels_enable", icon="PLAY")
            return
        layout.prop(scene, "frame_current")
        row = layout.row(align=True)
        cast(Any, row.operator("screen.frame_jump", text="", icon="REW")).end = False
        cast(Any, row.operator("blix.cel_jump", text="", icon="PREV_KEYFRAME")).forward = False
        screen = context.screen
        playing = screen is not None and screen.is_animation_playing
        row.operator("screen.animation_play", text="", icon="PAUSE" if playing else "PLAY")
        cast(Any, row.operator("blix.cel_jump", text="", icon="NEXT_KEYFRAME")).forward = True
        cast(Any, row.operator("screen.frame_jump", text="", icon="FF")).end = True
        layout.prop(scene, "blix_cel_follow", toggle=True)
        layout.prop(scene, "blix_onion", toggle=True)
        col = layout.column(align=True)
        col.active = props.onion(scene)
        col.prop(scene, "blix_onion_before")
        col.prop(scene, "blix_onion_after")
        col.prop(scene, "blix_onion_opacity", slider=True)
        layer = layers.active_layer(canvas)
        item = cels.track(scene, canvas, layer) if layer is not None else None
        if item is not None:
            row = layout.row(align=True)
            row.prop(item, "frame_start")
            row.prop(item, "frame_end")
            col = layout.column()
            col.use_property_split = True
            col.use_property_decorate = True
            col.prop(item, "visible")
            col.prop(item, "cel")
            row = layout.row(align=True)
            cast(Any, row.operator("blix.slot_add", text="Add Cel")).duplicate = False
            cast(Any, row.operator("blix.slot_add", text="Duplicate")).duplicate = True
            row.operator("blix.slot_remove", text="Delete")
        row = layout.row(align=True)
        cast(Any, row.operator("blix.frames_insert")).count = 1
        cast(Any, row.operator("blix.frames_remove")).count = 1
        layout.label(text="Layers as Frames")
        row = layout.row(align=True)
        row.operator("blix.cels_layout")
        row.operator("blix.cel_insert")
        layout.operator("blix.cels_export", icon="EXPORT")
        layout.operator("blix.cels_disable")


class BLIX_PT_stacking(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Sprite Stacking"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        assert layout is not None
        assert scene is not None
        image = _space(context).image
        if image is None:
            return
        canvas = image if len(props.layers(image)) else props.canvas_of(image)
        if canvas is None:
            return
        col = layout.column()
        col.prop(scene, "blix_stack_preview")
        col.prop(scene, "blix_stack_pixelate")
        sub = col.column()
        sub.active = props.stack_pixelate(scene)
        sub.prop(scene, "blix_stack_resolution")
        col.prop(scene, "blix_stack_projection", text="Projection")
        col.prop(scene, "blix_stack_angle")
        layer = layers.active_layer(canvas)
        if layer is not None:
            layout.prop(layer, "height")
        layout.operator("blix.stack_export", icon="EXPORT")


class BLIX_PT_dither(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Dither"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        assert layout is not None
        assert scene is not None
        col = layout.column()
        col.prop(scene, "blix_dither_size")
        col.prop(scene, "blix_dither_brush_size")
        col.prop(scene, "blix_dither_density")
        col.prop(scene, "blix_dither_transparent")


class BLIX_PT_palette(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Palette"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        tool_settings = context.tool_settings
        assert layout is not None
        assert tool_settings is not None
        image_paint = tool_settings.image_paint
        assert image_paint is not None
        layout.operator("blix.palette_import", icon="IMPORT")
        layout.template_ID(image_paint, "palette", new="palette.new")
        if image_paint.palette is not None:
            layout.template_palette(image_paint, "palette")


_classes = (
    BLIX_PT_grid,
    BLIX_UL_guides,
    BLIX_PT_guides,
    BLIX_PT_select,
    BLIX_PT_shapes,
    BLIX_PT_mirror,
    BLIX_UL_layers,
    BLIX_PT_layers,
    BLIX_PT_cels,
    BLIX_PT_stacking,
    BLIX_PT_dither,
    BLIX_PT_palette,
)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
