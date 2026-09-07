"""Typed access to Blix RNA properties registered at runtime."""

from typing import Any, cast

import bpy


def guides(image: bpy.types.Image) -> Any:
    return cast(Any, image).blix_guides


def guides_index(image: bpy.types.Image) -> int:
    return cast(Any, image).blix_guides_index


def set_guides_index(image: bpy.types.Image, value: int) -> None:
    cast(Any, image).blix_guides_index = value


def layers(image: bpy.types.Image) -> Any:
    return cast(Any, image).blix_layers


def layers_index(image: bpy.types.Image) -> int:
    return cast(Any, image).blix_layers_index


def set_layers_index(image: bpy.types.Image, value: int) -> None:
    cast(Any, image).blix_layers_index = value


def references(image: bpy.types.Image) -> Any:
    return cast(Any, image).blix_references


def references_index(image: bpy.types.Image) -> int:
    return cast(Any, image).blix_references_index


def set_references_index(image: bpy.types.Image, value: int) -> None:
    cast(Any, image).blix_references_index = value


def canvas_of(image: bpy.types.Image) -> bpy.types.Image | None:
    return cast(Any, image).blix_canvas


def set_canvas_of(image: bpy.types.Image, canvas: bpy.types.Image | None) -> None:
    cast(Any, image).blix_canvas = canvas


def show_rulers(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_show_rulers


def show_guides(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_show_guides


def show_pixel_grid(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_show_pixel_grid


def layer_dim(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_layer_dim


def layer_hatch(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_layer_hatch


def layer_outline(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_layer_outline


def grid_divisions(scene: bpy.types.Scene) -> int:
    return int(cast(Any, scene).blix_grid_divisions)


def select_mode(scene: bpy.types.Scene) -> str:
    return cast(Any, scene).blix_select_mode


def select_brush_size(scene: bpy.types.Scene) -> int:
    return int(cast(Any, scene).blix_select_brush_size)


def mirror_h(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_mirror_h


def mirror_v(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_mirror_v


def shape_filled(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_shape_filled


def shape_hex_pointy(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_shape_hex_pointy


def dither_pattern(scene: bpy.types.Scene) -> str:
    return cast(Any, scene).blix_dither_pattern


def dither_custom(scene: bpy.types.Scene) -> bpy.types.Image | None:
    return cast(Any, scene).blix_dither_custom


def dither_gradient(scene: bpy.types.Scene) -> str:
    return cast(Any, scene).blix_dither_gradient


def dither_density(scene: bpy.types.Scene) -> float:
    return cast(Any, scene).blix_dither_density


def dither_brush_size(scene: bpy.types.Scene) -> int:
    return cast(Any, scene).blix_dither_brush_size


def dither_transparent(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_dither_transparent


def stack_preview(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_stack_preview


def stack_pixelate(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_stack_pixelate


def stack_resolution(scene: bpy.types.Scene) -> float:
    return cast(Any, scene).blix_stack_resolution


def stack_projection(scene: bpy.types.Scene) -> str:
    return cast(Any, scene).blix_stack_projection


def stack_angle(scene: bpy.types.Scene) -> float:
    return cast(Any, scene).blix_stack_angle


def stack_scale(scene: bpy.types.Scene) -> int:
    return cast(Any, scene).blix_stack_scale


def stack_scale_layers(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_stack_scale_layers


def stack_noise(scene: bpy.types.Scene) -> float:
    return cast(Any, scene).blix_stack_noise


def stack_grain(scene: bpy.types.Scene) -> int:
    return cast(Any, scene).blix_stack_grain


def stack_cavity(scene: bpy.types.Scene) -> float:
    return cast(Any, scene).blix_stack_cavity


def voxel_object(scene: bpy.types.Scene) -> bpy.types.Object | None:
    return cast(Any, scene).blix_voxel_object


def voxel_resolution(scene: bpy.types.Scene) -> int:
    return int(cast(Any, scene).blix_voxel_resolution)


def voxel_depth(scene: bpy.types.Scene) -> int:
    return int(cast(Any, scene).blix_voxel_depth)


def voxel_scale(scene: bpy.types.Scene) -> float:
    return float(cast(Any, scene).blix_voxel_scale)


def cels(scene: bpy.types.Scene) -> Any:
    return cast(Any, scene).blix_cels


def cel_follow(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_cel_follow


def onion(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_onion


def onion_before(scene: bpy.types.Scene) -> int:
    return int(cast(Any, scene).blix_onion_before)


def onion_after(scene: bpy.types.Scene) -> int:
    return int(cast(Any, scene).blix_onion_after)


def onion_opacity(scene: bpy.types.Scene) -> float:
    return cast(Any, scene).blix_onion_opacity
