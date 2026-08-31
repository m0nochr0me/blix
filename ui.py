"""Image editor sidebar panels."""

from typing import Any, cast

import bpy

from . import layers, props


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
        col.prop(scene, "blix_shape_kind")
        col.prop(scene, "blix_shape_filled")
        if scene.blix_shape_kind == "HEXAGON":
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
        row.prop(item, "name", text="", emboss=False)
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
        layout.operator("blix.palette_import", icon="IMPORT")
        layout.template_ID(image_paint, "palette", new="palette.new")
        if image_paint.palette is not None:
            layout.template_palette(image_paint, "palette", color=True)


_classes = (
    BLIX_PT_grid,
    BLIX_UL_guides,
    BLIX_PT_guides,
    BLIX_PT_select,
    BLIX_PT_shapes,
    BLIX_PT_mirror,
    BLIX_UL_layers,
    BLIX_PT_layers,
    BLIX_PT_dither,
    BLIX_PT_palette,
)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
