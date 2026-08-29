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


def grid_divisions(scene: bpy.types.Scene) -> int:
    return int(cast(Any, scene).blix_grid_divisions)


def dither_size(scene: bpy.types.Scene) -> int:
    return int(cast(Any, scene).blix_dither_size)


def dither_density(scene: bpy.types.Scene) -> float:
    return cast(Any, scene).blix_dither_density


def dither_brush_size(scene: bpy.types.Scene) -> int:
    return cast(Any, scene).blix_dither_brush_size
