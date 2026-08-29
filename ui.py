"""Image editor sidebar panel exposing native pixel-grid overlay."""

import bpy


class BLIX_PT_main(bpy.types.Panel):
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Blix"
    bl_label = "Pixel Grid"

    def draw(self, context: bpy.types.Context) -> None:
        uv_editor = context.space_data.uv_editor
        col = self.layout.column()
        col.prop(uv_editor, "grid_shape_source", text="Shape")
        col.prop(uv_editor, "show_grid_over_image", text="Over Image")
        if uv_editor.grid_shape_source == 'FIXED':
            col.prop(uv_editor, "custom_grid_subdivisions", text="Subdivisions")


_classes = (BLIX_PT_main,)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
