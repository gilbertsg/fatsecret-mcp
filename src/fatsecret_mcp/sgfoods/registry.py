"""Which custom Foods this server has already created in FatSecret.

FatSecret can't edit, delete or list a User's custom Foods, so this file is the only record. It lives
on a mounted volume: losing it would make the server create the same Food again.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from fatsecret_mcp.domain.draft_token import custom_food_from_dict
from fatsecret_mcp.domain.models import CustomFood


@dataclass(frozen=True)
class RegisteredFood:
    user_id: str
    food: CustomFood
    """The values the Food was created with, which FatSecret now holds."""
    food_id: str
    serving_id: str | None
    """None until the Food's Serving has been looked up."""
    serving_units: float = 1.0
    """FatSecret units in that Serving: logging one Serving of the Food sends this many units."""


class CustomFoodRegistry:
    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS custom_foods ("
            " user_id TEXT NOT NULL, key TEXT NOT NULL, food_id TEXT NOT NULL, serving_id TEXT,"
            " serving_units REAL NOT NULL DEFAULT 1, spec_json TEXT NOT NULL, created_at TEXT NOT NULL,"
            " PRIMARY KEY (user_id, key))"
        )

    def get(self, user_id: str, key: str) -> RegisteredFood | None:
        row = self._db.execute(
            "SELECT food_id, serving_id, spec_json, serving_units FROM custom_foods"
            " WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()

        if row is None:
            return None

        return RegisteredFood(
            user_id=user_id,
            food=custom_food_from_dict(json.loads(row[2])),
            food_id=row[0],
            serving_id=row[1],
            serving_units=row[3],
        )

    def add(self, user_id: str, food: CustomFood, food_id: str) -> None:
        """Record a Food right after FatSecret created it, before anything else can fail."""
        self._db.execute(
            "INSERT INTO custom_foods (user_id, key, food_id, serving_id, spec_json, created_at)"
            " VALUES (?, ?, ?, NULL, ?, ?)",
            (user_id, food.key, food_id, json.dumps(asdict(food)), datetime.now(UTC).isoformat()),
        )

    def set_serving(self, user_id: str, key: str, serving_id: str, serving_units: float) -> None:
        self._db.execute(
            "UPDATE custom_foods SET serving_id = ?, serving_units = ? WHERE user_id = ? AND key = ?",
            (serving_id, serving_units, user_id, key),
        )

    def close(self) -> None:
        self._db.close()
