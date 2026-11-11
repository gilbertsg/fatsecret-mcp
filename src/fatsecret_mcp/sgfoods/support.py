"""What the SG foods and custom Foods need from the server, given to the tools that use them."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from fatsecret_mcp.sgfoods.registry import CustomFoodRegistry
from fatsecret_mcp.sgfoods.store import SgFoodStore


@dataclass(frozen=True)
class CustomFoodSupport:
    store: SgFoodStore | None
    """The SG FoodID table; None when the server has no data directory with one."""
    registry: CustomFoodRegistry | None
    """Present only when custom Foods are on (FATSECRET_PREMIER): Foods are then created in the account."""


DATA_DIR_VAR = "FATSECRET_DATA_DIR"
PREMIER_VAR = "FATSECRET_PREMIER"
DATABASE_FILE = "sgfoodid.sqlite"
ALIASES_FILE = "aliases.json"
REGISTRY_FILE = "registry.sqlite"


class CustomFoodConfigError(Exception):
    """The SG foods or custom Foods settings can't be used."""


def load_custom_foods(environ: Mapping[str, str]) -> CustomFoodSupport | None:
    """The SG foods and custom Foods the environment turns on; None when neither is.

    `FATSECRET_DATA_DIR` holds `sgfoodid.sqlite` and `aliases.json` (built outside the server) and the
    registry. `FATSECRET_PREMIER=true` needs FatSecret's Premier plan, and creates custom Foods.
    """
    premier = environ.get(PREMIER_VAR, "false").strip().lower()

    if premier not in ("true", "false"):
        raise CustomFoodConfigError(f"{PREMIER_VAR} must be true or false.")

    directory = environ.get(DATA_DIR_VAR, "").strip()

    if not directory:
        if premier == "true":
            raise CustomFoodConfigError(f"{PREMIER_VAR}=true needs {DATA_DIR_VAR}, which holds the registry.")

        return None

    folder = Path(directory)

    if not folder.is_dir():
        raise CustomFoodConfigError(f"{DATA_DIR_VAR} is not a directory: {folder}")

    database = folder / DATABASE_FILE
    store = SgFoodStore(database, folder / ALIASES_FILE) if database.exists() else None
    registry = CustomFoodRegistry(folder / REGISTRY_FILE) if premier == "true" else None

    return CustomFoodSupport(store=store, registry=registry)


@cache
def custom_foods_from_environment() -> CustomFoodSupport | None:
    """The settings of this process, loaded once and shared by every User's server."""
    return load_custom_foods(os.environ)
