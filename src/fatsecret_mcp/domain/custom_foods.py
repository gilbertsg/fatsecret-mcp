"""Custom Foods: HPB dishes and labelled products that become a Food in the User's FatSecret account.

Unlike a Stand-in, a custom Food carries the dish's own kcal and Macros. FatSecret can't edit or delete
it, so it is created once and reused: the registry remembers it by `CustomFood.key`.
"""

from __future__ import annotations

import math
import re
from datetime import date

from fatsecret_mcp.domain.drafts import UNIT_DECIMALS, InvalidDraftItem
from fatsecret_mcp.domain.models import (
    CustomFood,
    CustomFoodDetails,
    DraftEntry,
    DraftItem,
    Nutrition,
    NutritionLabel,
)

# A label read again from a photo rarely matches to the digit; more than this apart is a different product.
LABEL_DRIFT = 0.05
LABEL_SERVING_G = 100.0
LABEL_BRAND = "Label"
ESTIMATE_BRAND = "Estimate"
ESTIMATE_SUFFIX = " (est.)"
MAX_FOOD_NAME_LENGTH = 100


def hpb_key(hpb_id: str) -> str:
    return f"hpb:{hpb_id}"


def label_key(product_name: str, *, recipe_year: int | None = None, estimated: bool = False) -> str:
    """The registry key of a labelled or estimated product: its name, lower-cased, without punctuation."""
    words = re.findall(r"\w+", product_name.lower())
    key = ("estimate:" if estimated else "label:") + " ".join(words)

    return f"{key}:{recipe_year}" if recipe_year is not None else key


def label_custom_food(
    product_name: str, label: NutritionLabel, *, recipe_changed: bool, today: date
) -> CustomFood:
    """The Food for a product read from its packaging, or a dish estimated without any source.

    Both get a 100 g Serving. An estimate is named "<dish> (est.)" under the brand "Estimate", so it
    can't be mistaken for a measured value in the diary, and is reused by dish name like a label.
    """
    estimated = label.estimated
    year = today.year if recipe_changed else None
    name = f"{product_name} ({year})" if recipe_changed else product_name

    if estimated:
        name += ESTIMATE_SUFFIX

    return CustomFood(
        key=label_key(product_name, recipe_year=year, estimated=estimated),
        name=name[:MAX_FOOD_NAME_LENGTH],
        serving_label="100 g",
        serving_g=LABEL_SERVING_G,
        per_serving=label.per_100_g,
        source="estimate, not measured" if estimated else "nutrition label",
        brand=ESTIMATE_BRAND if estimated else LABEL_BRAND,
    )


def per_100_g(food: CustomFood) -> Nutrition:
    return food.per_serving.scaled(100 / food.serving_g)


def label_drift(stored: CustomFood, read: Nutrition) -> str | None:
    """Why a label or estimate now differs from the stored Food's values, when it differs by more than 5%."""
    before = per_100_g(stored)
    parts: list[str] = []

    for field, unit in (("kcal", "kcal"), ("protein", "g"), ("fat", "g"), ("carbohydrate", "g")):
        was = getattr(before, field)
        now = getattr(read, field)

        if abs(now - was) > LABEL_DRIFT * max(was, 1.0) and abs(now - was) > 0.5:
            parts.append(f"{field} {was:g} -> {now:g} {unit}")

    if not parts:
        return None

    return (
        "These values differ from the stored Food per 100 g: " + ", ".join(parts) + ". The stored Food is "
        "used. If the recipe really changed, draft again with recipe_changed=true to create a new Food."
    )


def custom_item(
    food: CustomFood,
    *,
    exists: bool,
    grams: float | None,
    servings: float | None,
    entry_name: str | None,
    assumption: str | None,
    warnings: tuple[str, ...] = (),
) -> DraftItem:
    """The Draft item logging `grams` (or `servings` of the Food's Serving) of a custom Food.

    `exists` is True when the Food is already in the User's account.
    """
    if (grams is None) == (servings is None):
        raise InvalidDraftItem("Give either grams or servings, not both and not neither.")

    units = servings if servings is not None else (grams or 0) / food.serving_g
    units = round(units, UNIT_DECIMALS)

    if not math.isfinite(units) or units <= 0:
        raise InvalidDraftItem("Amount is too small to log.")

    eaten_g = units * food.serving_g
    name = (entry_name or food.name)[:MAX_FOOD_NAME_LENGTH]
    nutrition = food.per_serving.scaled(units)
    entry = DraftEntry(
        food_id="",
        serving_id="",
        number_of_units=units,
        entry_name=name,
        custom_food=food,
    )
    details = CustomFoodDetails(
        name=food.name,
        source=food.source,
        creates_new_food=not exists,
        warnings=warnings,
    )

    return DraftItem(
        entry=entry,
        serving=f"{food.serving_label} ({food.serving_g:g} g)",
        amount=f"{eaten_g:g} g",
        nutrition=nutrition,
        assumption=assumption,
        custom_food=details,
    )
