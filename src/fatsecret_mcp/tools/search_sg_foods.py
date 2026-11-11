"""search_sg_foods: Singapore dishes and foods from HPB's SG FoodID, searched on this server."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations
from pydantic import Field

from fatsecret_mcp.sgfoods.foods import kcal_mismatch_warning
from fatsecret_mcp.sgfoods.store import SgFood, SgFoodStore
from fatsecret_mcp.tools.schemas import (
    FoodDescription,
    SgFoodMatch,
    SgFoodSearch,
    SgServing,
    as_tool_result,
)


def register(server: MCPServer, store: SgFoodStore) -> None:
    annotations = ToolAnnotations(
        title="Search Singapore Foods",
        read_only_hint=True,
    )

    @server.tool(annotations=annotations)
    async def search_sg_foods(
        description: FoodDescription,
        limit: Annotated[int, Field(ge=1, le=10, description="How many matches to return.")] = 5,
    ) -> Annotated[CallToolResult, SgFoodSearch]:
        """Find Singapore dishes, drinks and foods in HPB's SG FoodID, with kcal and Macros per 100 g.

        Prefer this over search_foods for hawker and other Singapore food (chicken rice, laksa, nasi lemak,
        kopi, teh, yong tau foo, prata, cai png): the US catalog has no such dishes. Search the dish name
        as the User says it, e.g. "teh peng" or "char kway teow"; hawker shorthand is understood. Each
        match has a default serving with its weight and kcal, and other servings. Before drafting, ask the
        User about the portion and the variations the dish has (extra rice, egg, gravy, less sugar) and
        pick the closest match; HPB lists many variants as separate items. Log a match with draft_entries
        as an SG food item (hpb_id). Show a match's data_source and its warning when it has one. Show the
        attribution with HPB results. This tool does not write to the Food Diary.
        """
        if not description.strip():
            raise ToolError("description must not be blank.")

        matches = [_match(food) for food in store.search(description, limit)]
        found = SgFoodSearch(
            status="found" if matches else "not_found",
            matches=matches,
            attribution=store.attribution,
        )

        return as_tool_result(found)


def _match(food: SgFood) -> SgFoodMatch:
    return SgFoodMatch(
        hpb_id=food.id,
        name=food.name,
        description=food.description,
        food_group=food.food_group,
        kcal_per_100g=food.kcal_100g,
        protein_per_100g=food.protein_100g,
        fat_per_100g=food.fat_100g,
        carbohydrate_per_100g=food.carb_100g,
        default_serving=SgServing(label=food.default_serving_label, grams=food.default_serving_g),
        default_serving_kcal=round(food.kcal_100g * food.default_serving_g / 100, 1),
        other_servings=[SgServing(label=label, grams=grams) for label, grams in food.alt_servings],
        data_source=food.data_source,
        year=food.last_updated_year,
        warning=kcal_mismatch_warning(food),
    )
