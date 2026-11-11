"""Make sure a custom Food exists in the User's account, creating it at most once."""

from __future__ import annotations

import math
from dataclasses import dataclass

from fatsecret_mcp.domain.models import CustomFood, Serving
from fatsecret_mcp.fatsecret.errors import UpstreamFailure
from fatsecret_mcp.fatsecret.gateway import AccessToken, FatSecretGateway
from fatsecret_mcp.sgfoods.registry import CustomFoodRegistry


@dataclass(frozen=True)
class LoggableFood:
    food_id: str
    serving_id: str
    serving_units: float
    """Send this many FatSecret units to log one of the Food's Servings."""


async def ensure_custom_food(
    user_id: str,
    token: AccessToken,
    food: CustomFood,
    registry: CustomFoodRegistry,
    fatsecret: FatSecretGateway,
) -> LoggableFood:
    """The ids to log `food` with. A Food FatSecret created is recorded before anything else can fail.

    So a Draft whose entry then failed can be confirmed again without creating a second, permanent Food.
    """
    registered = registry.get(user_id, food.key)

    if registered is None:
        food_id = await fatsecret.create_custom_food(token, food)

        registry.add(user_id, food, food_id)
        registered = registry.get(user_id, food.key)

    assert registered is not None

    if registered.serving_id is not None:
        return LoggableFood(registered.food_id, registered.serving_id, registered.serving_units)

    created = await fatsecret.get_custom_food(token, registered.food_id)
    serving = _serving_to_log(created.servings, food)

    if serving is None:
        raise UpstreamFailure(
            f"FatSecret created {food.name} (food {registered.food_id}) but showed no Serving to log "
            "it with. Confirming this Draft again retries only that lookup"
        )

    registry.set_serving(user_id, food.key, serving.id, serving.number_of_units)

    return LoggableFood(registered.food_id, serving.id, serving.number_of_units)


def _serving_to_log(servings: list[Serving], food: CustomFood) -> Serving | None:
    """The Serving made from the Food's own Serving: the one in grams of its size, else the only real one."""
    real = [
        serving
        for serving in servings
        if serving.id != "0" and math.isfinite(serving.number_of_units) and serving.number_of_units > 0
    ]

    for serving in real:
        weighs_the_same = (
            serving.metric_unit == "g"
            and serving.metric_amount is not None
            and abs(serving.metric_amount - food.serving_g) < 0.5
        )

        if weighs_the_same:
            return serving

    return real[0] if len(real) == 1 else None
