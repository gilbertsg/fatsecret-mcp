"""HPB dishes and labelled products as custom Foods: searched, drafted, created once, logged."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import CatalogFood, Food, Nutrition, Serving
from fatsecret_mcp.fatsecret.budget import daily_budget
from fatsecret_mcp.fatsecret.client import custom_food_params
from fatsecret_mcp.fatsecret.errors import UpstreamFailure
from fatsecret_mcp.fatsecret.food_cache import FoodCache
from fatsecret_mcp.server import build_server
from fatsecret_mcp.sgfoods.registry import CustomFoodRegistry
from fatsecret_mcp.sgfoods.store import SgFoodStore
from fatsecret_mcp.sgfoods.support import CustomFoodConfigError, CustomFoodSupport, load_custom_foods
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret

pytestmark = pytest.mark.anyio
TODAY = date(2026, 10, 14)
ATTRIBUTION = "Contains information from SG FoodID (test)"
CHICKEN_RICE = "D01"
ICED_TEH = "D02"
KING_PRAWN = "D03"
MYSTERY = "D04"

SCHEMA = """
CREATE TABLE foods (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, food_group TEXT NOT NULL,
  subgroup TEXT, composition TEXT, product_type TEXT, prep_format TEXT, kcal_100g REAL NOT NULL,
  protein_100g REAL, fat_100g REAL, sat_fat_100g REAL, carb_100g REAL, sugar_100g REAL, fibre_100g REAL,
  sodium_mg_100g REAL, default_serving_label TEXT NOT NULL, default_serving_g REAL NOT NULL,
  alt_servings_json TEXT NOT NULL, data_source TEXT, kcal_mismatch INTEGER NOT NULL DEFAULT 0,
  last_updated_year INTEGER, url TEXT, fetched_at TEXT NOT NULL);
CREATE VIRTUAL TABLE foods_fts USING fts5(name, description, content='foods', content_rowid='rowid',
  tokenize='porter unicode61');
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

ROWS: list[tuple[Any, ...]] = [
    # id, name, kcal, P, F, C, sodium, serving label, serving g, alts, basis, year, mismatch
    (
        CHICKEN_RICE,
        "Steamed chicken rice",
        147,
        5.95,
        3.6,
        21.88,
        300,
        "1 plate",
        323,
        [["1 bowl", 200]],
        "Lab Analysis",
        2025,
        0,
    ),
    (ICED_TEH, "Iced Teh", 40, 0.4, 1.2, 6.9, None, "1 glass", 250, [], "Borrowed", 2023, 0),
    (KING_PRAWN, "Cooked king prawn", 464, 20, 1, 1, None, "100 g", 100, [], "Derived", 2018, 1),
    (MYSTERY, "Mystery sauce", 80, None, 2, 10, None, "1 tbsp", 15, [], "NIP", 2024, 0),
]


@pytest.fixture
def store(tmp_path: Path) -> SgFoodStore:
    database = tmp_path / "sgfoodid.sqlite"
    db = sqlite3.connect(database)
    db.executescript(SCHEMA)

    for id_, name, kcal, protein, fat, carb, sodium, label, grams, alts, basis, year, mismatch in ROWS:
        db.execute(
            "INSERT INTO foods (id,name,food_group,kcal_100g,protein_100g,fat_100g,carb_100g,sodium_mg_100g,"
            "default_serving_label,default_serving_g,alt_servings_json,data_source,kcal_mismatch,"
            "last_updated_year,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                id_,
                name,
                "Test",
                kcal,
                protein,
                fat,
                carb,
                sodium,
                label,
                grams,
                json.dumps([{"label": a, "grams": g} for a, g in alts]),
                basis,
                mismatch,
                year,
                "2026-10-21",
            ),
        )

    db.execute("INSERT INTO foods_fts(rowid,name,description) SELECT rowid,name,'' FROM foods")
    db.execute("INSERT INTO meta VALUES ('attribution', ?)", (ATTRIBUTION,))
    db.execute("INSERT INTO meta VALUES ('accessed', '2026-10-21')")
    db.commit()
    db.close()

    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({"Iced Teh": ["teh peng"]}))

    return SgFoodStore(database, aliases)


@pytest.fixture
def registry(tmp_path: Path) -> CustomFoodRegistry:
    return CustomFoodRegistry(tmp_path / "registry.sqlite")


@pytest.fixture
def connect_custom(
    household: Household, fatsecret: FakeFatSecret, clock: FakeClock, food_cache: FoodCache
) -> Callable[[str, CustomFoodSupport | None], Client]:
    def connect(user_id: str, custom: CustomFoodSupport | None) -> Client:
        server = build_server(
            user=household.user(user_id),
            fatsecret=fatsecret,
            clock=clock,
            signing_key=household.signing_key,
            daily_requests=daily_budget(len(household.users)),
            food_cache=food_cache,
            custom=custom,
        )

        return Client(server)

    return connect


async def call(client: Client, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)

    assert not result.is_error, result.content
    assert result.structured_content is not None

    return result.structured_content


async def call_error(client: Client, tool: str, arguments: dict[str, Any]) -> str:
    result = await client.call_tool(tool, arguments)

    assert result.is_error
    assert result.content

    return getattr(result.content[0], "text", "")


async def draft(client: Client, items: list[dict[str, Any]]) -> dict[str, Any]:
    return await call(client, "draft_entries", {"items": items, "meal": "lunch", "diary_day": "2026-10-14"})


async def confirm(client: Client, draft_id: str) -> dict[str, Any]:
    return await call(client, "confirm_draft", {"draft_id": draft_id})


async def test_search_sg_foods_returns_servings_source_attribution_and_warnings(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client], store: SgFoodStore
) -> None:
    custom = CustomFoodSupport(store=store, registry=None)

    async with connect_custom("jakub", custom) as client:
        rice = await call(client, "search_sg_foods", {"description": "chicken rice"})
        teh = await call(client, "search_sg_foods", {"description": "teh peng"})
        prawn = await call(client, "search_sg_foods", {"description": "king prawn"})
        none = await call(client, "search_sg_foods", {"description": "nonexistentdish"})

    first = rice["matches"][0]

    assert rice["attribution"] == ATTRIBUTION
    assert (first["hpb_id"], first["name"], first["kcal_per_100g"]) == (
        CHICKEN_RICE,
        "Steamed chicken rice",
        147,
    )
    assert first["default_serving"] == {"label": "1 plate", "grams": 323}
    assert first["default_serving_kcal"] == pytest.approx(474.8, abs=0.1)
    assert first["other_servings"] == [{"label": "1 bowl", "grams": 200}]
    assert (first["data_source"], first["year"], first["warning"]) == ("Lab Analysis", 2025, None)
    assert [match["name"] for match in teh["matches"]] == ["Iced Teh"]
    assert prawn["matches"][0]["warning"] is not None
    assert none == {"status": "not_found", "matches": [], "attribution": ATTRIBUTION}


async def test_the_sg_search_is_offered_only_with_a_table(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
) -> None:
    async with connect_custom("jakub", None) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]

    assert "search_sg_foods" not in names
    assert {"search_foods", "draft_entries", "confirm_draft"} <= set(names)


async def test_a_draft_of_an_hpb_dish_shows_hpb_values_and_a_new_permanent_food_and_writes_nothing(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    fatsecret: FakeFatSecret,
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)

    async with connect_custom("jakub", custom) as client:
        drafted = await draft(client, [{"hpb_id": CHICKEN_RICE, "servings": 0.5}])

    item = drafted["items"][0]

    assert item["name"] == "Steamed chicken rice (HPB)"
    assert item["amount"] == "161.5 g"
    assert (item["kcal"], item["protein"], item["fat"], item["carbohydrate"]) == (237, 9.6, 5.8, 35.3)
    assert item["custom_food"] == {
        "name": "Steamed chicken rice (HPB)",
        "source": "HPB SG FoodID, Lab Analysis, 2025",
        "creates_new_food": True,
        "warnings": [],
    }
    assert fatsecret.created_foods == []
    assert registry.get("jakub", f"hpb:{CHICKEN_RICE}") is None


async def test_grams_and_servings_are_alternatives_and_an_item_without_macros_is_refused(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)

    async with connect_custom("jakub", custom) as client:
        both = await client.call_tool(
            "draft_entries", {"items": [{"hpb_id": CHICKEN_RICE, "grams": 100, "servings": 1}]}
        )
        neither = await client.call_tool("draft_entries", {"items": [{"hpb_id": CHICKEN_RICE}]})
        no_protein = await call_error(client, "draft_entries", {"items": [{"hpb_id": MYSTERY, "grams": 15}]})
        unknown = await call_error(client, "draft_entries", {"items": [{"hpb_id": "NOPE", "grams": 15}]})

    assert both.is_error
    assert neither.is_error
    assert "protein" in no_protein
    assert "search_sg_foods" in unknown


async def test_an_hpb_row_with_inconsistent_kcal_is_allowed_with_a_warning(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)

    async with connect_custom("jakub", custom) as client:
        drafted = await draft(client, [{"hpb_id": KING_PRAWN, "grams": 100}])

    warnings = drafted["items"][0]["custom_food"]["warnings"]

    assert len(warnings) == 1
    assert "don't agree" in warnings[0]


async def test_confirming_creates_the_food_once_and_logs_the_dishs_own_values(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    fatsecret: FakeFatSecret,
    household: Household,
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)

    async with connect_custom("jakub", custom) as client:
        drafted = await draft(client, [{"hpb_id": CHICKEN_RICE, "grams": 323}])
        confirmed = await confirm(client, drafted["draft_id"])

    entries = await fatsecret.list_food_entries(household.user("jakub").fatsecret, TODAY)
    created = fatsecret.created_foods[0]

    assert confirmed["failure"] is None
    assert len(fatsecret.created_foods) == 1
    assert (created.name, created.serving_label, created.serving_g) == (
        "Steamed chicken rice (HPB)",
        "1 plate",
        323,
    )
    assert created.per_serving == Nutrition(kcal=474.81, protein=19.22, fat=11.63, carbohydrate=70.67)
    assert (created.sodium_mg, created.saturated_fat) == (969.0, None)
    assert [(entry.name, entry.number_of_units) for entry in entries] == [("Steamed chicken rice (HPB)", 1)]
    assert entries[0].nutrition.kcal == pytest.approx(474.81)
    assert entries[0].food_id == "custom-1"


async def test_the_food_is_created_with_the_brand_fatsecret_requires_and_only_known_nutrients(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    fatsecret: FakeFatSecret,
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)

    async with connect_custom("jakub", custom) as client:
        hpb = await draft(client, [{"hpb_id": CHICKEN_RICE, "servings": 1}])
        await confirm(client, hpb["draft_id"])
        bar = await draft(client, [label()])
        await confirm(client, bar["draft_id"])

    dish, product = (custom_food_params(food) for food in fatsecret.created_foods)

    assert (dish["food_name"], dish["brand_name"]) == ("Steamed chicken rice", "HPB")
    assert (dish["serving_size"], dish["serving_amount"], dish["serving_amount_unit"]) == (
        "1 plate (323 g)",
        "323",
        "g",
    )
    assert dish["sodium"] == "969"
    assert {"saturated_fat", "sugar", "fiber"}.isdisjoint(dish)
    assert (product["food_name"], product["brand_name"], product["calories"]) == ("Oat Bar", "Label", "400")
    assert {"sodium", "fiber", "saturated_fat"}.isdisjoint(product)


async def test_the_same_dish_three_times_creates_one_food_and_later_drafts_say_it_exists(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    fatsecret: FakeFatSecret,
    household: Household,
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)
    flags: list[bool] = []

    async with connect_custom("jakub", custom) as client:
        for grams in (323, 200, 400):
            drafted = await draft(client, [{"hpb_id": CHICKEN_RICE, "grams": grams}])
            flags.append(drafted["items"][0]["custom_food"]["creates_new_food"])
            await confirm(client, drafted["draft_id"])

    entries = await fatsecret.list_food_entries(household.user("jakub").fatsecret, TODAY)

    assert flags == [True, False, False]
    assert len(fatsecret.created_foods) == 1
    assert len(entries) == 3
    assert {entry.food_id for entry in entries} == {"custom-1"}


async def test_each_user_gets_their_own_food(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    fatsecret: FakeFatSecret,
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)

    for user in ("jakub", "ola"):
        async with connect_custom(user, custom) as client:
            drafted = await draft(client, [{"hpb_id": ICED_TEH, "servings": 1}])
            await confirm(client, drafted["draft_id"])

    assert len(fatsecret.created_foods) == 2


async def test_a_food_created_before_its_entry_failed_is_not_created_again_on_retry(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    fatsecret: FakeFatSecret,
    household: Household,
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)

    async with connect_custom("jakub", custom) as client:
        drafted = await draft(client, [{"hpb_id": CHICKEN_RICE, "grams": 323}])

        fatsecret.fail_entry_creation_with(UpstreamFailure("no answer"))

        error = await call_error(client, "confirm_draft", {"draft_id": drafted["draft_id"]})

        assert "get_diary_day" in error
        assert len(fatsecret.created_foods) == 1
        assert registry.get("jakub", f"hpb:{CHICKEN_RICE}") is not None

        fatsecret.fail_entry_creation_with(None)

        confirmed = await confirm(client, drafted["draft_id"])

    entries = await fatsecret.list_food_entries(household.user("jakub").fatsecret, TODAY)

    assert confirmed["failure"] is None
    assert len(fatsecret.created_foods) == 1
    assert len(entries) == 1


async def test_the_entry_is_logged_in_the_units_of_the_servings_fatsecret_made(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    fatsecret: FakeFatSecret,
    household: Household,
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)
    fatsecret.custom_serving_units = 100

    async with connect_custom("jakub", custom) as client:
        drafted = await draft(client, [{"hpb_id": ICED_TEH, "servings": 0.5}])
        await confirm(client, drafted["draft_id"])

    entries = await fatsecret.list_food_entries(household.user("jakub").fatsecret, TODAY)

    assert entries[0].number_of_units == 50
    assert entries[0].nutrition.kcal == pytest.approx(50.0)
    assert registry.get("jakub", f"hpb:{ICED_TEH}") is not None


def label(
    kcal: float = 400, protein: float = 20, fat: float = 15, carbohydrate: float = 45, **more: Any
) -> dict[str, Any]:
    return {
        "product_name": "Oat Bar",
        "nutrition_label": {
            "kcal": kcal,
            "protein": protein,
            "fat": fat,
            "carbohydrate": carbohydrate,
            "estimated": False,
        },
        "grams": 50,
        "search_phrase": "granola bar",
        **more,
    }


async def test_a_label_becomes_a_custom_food_reused_by_name_with_a_warning_when_values_drift(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    fatsecret: FakeFatSecret,
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)

    async with connect_custom("jakub", custom) as client:
        first = await draft(client, [label()])

        await confirm(client, first["draft_id"])

        same = await draft(client, [label()])
        drifted = await draft(client, [label(kcal=330, protein=15)])
        changed = await draft(client, [label(kcal=330, protein=15, recipe_changed=True)])

    item = first["items"][0]

    assert (item["name"], item["amount"], item["kcal"]) == ("Oat Bar", "50 g", 200)
    assert item["custom_food"]["creates_new_food"] is True
    assert item["stand_in"] is None
    assert same["items"][0]["custom_food"] == {
        "name": "Oat Bar",
        "source": "nutrition label",
        "creates_new_food": False,
        "warnings": [],
    }
    assert "kcal 400 -> 330" in drifted["items"][0]["custom_food"]["warnings"][0]
    assert drifted["items"][0]["kcal"] == 200
    assert changed["items"][0]["custom_food"]["name"] == "Oat Bar (2026)"
    assert changed["items"][0]["custom_food"]["creates_new_food"] is True
    assert changed["items"][0]["kcal"] == 165
    assert len(fatsecret.created_foods) == 1


async def test_an_estimate_becomes_an_est_custom_food_reused_by_dish_name(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    fatsecret: FakeFatSecret,
    store: SgFoodStore,
    registry: CustomFoodRegistry,
) -> None:
    custom = CustomFoodSupport(store=store, registry=registry)
    rice = label(product_name="Egg fried rice", kcal=170, protein=6, fat=7, carbohydrate=20, grams=350)

    rice["nutrition_label"]["estimated"] = True

    async with connect_custom("jakub", custom) as client:
        first = await draft(client, [rice])
        confirmed = await confirm(client, first["draft_id"])
        again = await draft(client, [{**rice, "grams": 200}])
        drifted = await draft(client, [{**rice, "nutrition_label": {**rice["nutrition_label"], "kcal": 220}}])

    item = first["items"][0]

    assert confirmed["failure"] is None
    assert (item["name"], item["amount"], item["kcal"]) == ("Egg fried rice (est.)", "350 g", 595)
    assert item["custom_food"]["creates_new_food"] is True
    assert item["custom_food"]["source"] == "estimate, not measured"
    assert item["stand_in"] is None
    assert again["items"][0]["custom_food"]["creates_new_food"] is False
    assert again["items"][0]["kcal"] == 340
    assert "kcal 170 -> 220" in drifted["items"][0]["custom_food"]["warnings"][0]
    assert len(fatsecret.created_foods) == 1
    assert fatsecret.created_foods[0].brand == "Estimate"
    assert registry.get("jakub", "estimate:egg fried rice") is not None
    assert registry.get("jakub", "label:egg fried rice") is None


async def test_with_custom_foods_off_hpb_dishes_are_refused_and_labels_stay_stand_ins(
    connect_custom: Callable[[str, CustomFoodSupport | None], Client],
    fatsecret: FakeFatSecret,
    store: SgFoodStore,
) -> None:
    custom = CustomFoodSupport(store=store, registry=None)
    fatsecret.add_food(
        Food(
            id="bar",
            name="Granola Bar",
            brand=None,
            servings=[
                Serving(
                    id="bar-1",
                    description="100 g",
                    metric_amount=100,
                    metric_unit="g",
                    number_of_units=100,
                    nutrition=Nutrition(kcal=420, protein=10, fat=15, carbohydrate=60),
                )
            ],
        )
    )
    fatsecret.set_search_results("granola bar", [CatalogFood(id="bar", name="Granola Bar", brand=None)])

    async with connect_custom("jakub", custom) as client:
        refused = await call_error(
            client, "draft_entries", {"items": [{"hpb_id": CHICKEN_RICE, "grams": 100}]}
        )
        drafted = await draft(client, [label()])

    assert "FATSECRET_PREMIER" in refused
    assert drafted["items"][0]["stand_in"] is not None
    assert drafted["items"][0]["custom_food"] is None


def test_the_environment_turns_the_table_and_custom_foods_on(tmp_path: Path, store: SgFoodStore) -> None:
    assert store is not None  # the fixture writes the table into tmp_path
    folder = tmp_path

    assert load_custom_foods({}) is None

    on = load_custom_foods({"FATSECRET_DATA_DIR": str(folder), "FATSECRET_PREMIER": "true"})
    off = load_custom_foods({"FATSECRET_DATA_DIR": str(folder)})
    nothing = tmp_path / "nothing"

    nothing.mkdir()

    empty = load_custom_foods({"FATSECRET_DATA_DIR": str(nothing)})

    assert on is not None
    assert (on.store is not None, on.registry is not None) == (True, True)
    assert off is not None
    assert (off.store is not None, off.registry is not None) == (True, False)
    assert empty is not None
    assert (empty.store is not None, empty.registry is not None) == (False, False)


@pytest.mark.parametrize(
    "environ",
    [
        {"FATSECRET_PREMIER": "yes"},
        {"FATSECRET_PREMIER": "true"},
        {"FATSECRET_DATA_DIR": "/does/not/exist"},
    ],
)
def test_unusable_settings_are_refused_at_startup(environ: dict[str, str]) -> None:
    with pytest.raises(CustomFoodConfigError):
        load_custom_foods(environ)
