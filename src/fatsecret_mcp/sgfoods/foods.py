"""An SG FoodID item as a custom Food: its default serving, with HPB's values scaled to that serving."""

from __future__ import annotations

from fatsecret_mcp.domain.custom_foods import MAX_FOOD_NAME_LENGTH, hpb_key
from fatsecret_mcp.domain.drafts import InvalidDraftItem
from fatsecret_mcp.domain.models import CustomFood, Nutrition
from fatsecret_mcp.sgfoods.store import SgFood

BRAND = "HPB"
SUFFIX = f" ({BRAND})"


def hpb_custom_food(food: SgFood) -> CustomFood:
    """The custom Food for an HPB item, created per its default serving, e.g. "1 plate (323 g)".

    FatSecret needs kcal and all three Macros; an item HPB has no value for can't be used.
    """
    missing = [
        name
        for name, value in (
            ("protein", food.protein_100g),
            ("fat", food.fat_100g),
            ("carbohydrate", food.carb_100g),
        )
        if value is None
    ]

    if missing:
        raise InvalidDraftItem(
            f"HPB has no {', '.join(missing)} value for {food.name}, so it can't be logged exactly. "
            "Choose another item, or log a catalog Food or a labelled estimate."
        )

    scale = food.default_serving_g / 100
    year = f", {food.last_updated_year}" if food.last_updated_year else ""

    return CustomFood(
        key=hpb_key(food.id),
        name=food.name[: MAX_FOOD_NAME_LENGTH - len(SUFFIX)] + SUFFIX,
        serving_label=food.default_serving_label,
        serving_g=food.default_serving_g,
        per_serving=Nutrition(
            kcal=_round(food.kcal_100g * scale),
            protein=_round((food.protein_100g or 0) * scale),
            fat=_round((food.fat_100g or 0) * scale),
            carbohydrate=_round((food.carb_100g or 0) * scale),
        ),
        saturated_fat=_scaled(food.sat_fat_100g, scale),
        sugar=_scaled(food.sugar_100g, scale),
        fibre=_scaled(food.fibre_100g, scale),
        sodium_mg=_scaled(food.sodium_mg_100g, scale),
        source=f"HPB SG FoodID, {food.data_source or 'unknown basis'}{year}",
        brand=BRAND,
    )


def kcal_mismatch_warning(food: SgFood) -> str | None:
    if not food.kcal_mismatch:
        return None

    return (
        f"HPB's kcal for {food.name} don't agree with its Macros, so the source row may be wrong. "
        "Consider a catalog Food or a labelled estimate instead, and tell the User."
    )


def _round(value: float) -> float:
    return round(value, 2)


def _scaled(value: float | None, scale: float) -> float | None:
    return None if value is None else _round(value * scale)
