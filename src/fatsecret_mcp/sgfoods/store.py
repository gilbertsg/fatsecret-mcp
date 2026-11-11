"""The SG FoodID table: a read-only SQLite file built outside the server, searched with FTS5."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

MAX_RESULTS = 10


@dataclass(frozen=True)
class SgFood:
    """One SG FoodID item, with values per 100 g; None where HPB has no value."""

    id: str
    name: str
    description: str | None
    food_group: str
    subgroup: str | None
    kcal_100g: float
    protein_100g: float | None
    fat_100g: float | None
    sat_fat_100g: float | None
    carb_100g: float | None
    sugar_100g: float | None
    fibre_100g: float | None
    sodium_mg_100g: float | None
    default_serving_label: str
    default_serving_g: float
    alt_servings: tuple[tuple[str, float], ...]
    data_source: str | None
    last_updated_year: int | None
    kcal_mismatch: bool
    """True when the item's kcal disagree with its Macros by over 15%: the source row may be wrong."""


class SgFoodStore:
    def __init__(self, database: Path, aliases: Path | None = None) -> None:
        # Read-only: the server never changes the table; a refresh replaces the file.
        self._db = sqlite3.connect(f"file:{database}?mode=ro", uri=True, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._aliases = _alias_map(aliases)
        self.attribution = self._meta("attribution")
        self.accessed = self._meta("accessed")

    def get(self, food_id: str) -> SgFood | None:
        row = self._db.execute("SELECT * FROM foods WHERE id = ?", (food_id,)).fetchone()

        return _food(row) if row is not None else None

    def search(self, query: str, limit: int = 5) -> list[SgFood]:
        """Matches best first: an alias or exact name, then FTS5 relevance (all words, else any word)."""
        limit = max(1, min(limit, MAX_RESULTS))
        words = re.findall(r"\w+", query.lower())
        found: dict[str, SgFood] = {}

        if not words:
            return []

        target = self._aliases.get(" ".join(words))
        exact = self._db.execute(
            "SELECT * FROM foods WHERE lower(name) = ? OR name = ?", (" ".join(words), target or "")
        ).fetchall()

        for row in exact:
            found.setdefault(row["id"], _food(row))

        for joiner in (" AND ", " OR "):
            # Any-word matching is only for queries nothing else matched: it adds noise to good results.
            if len(found) >= limit or (found and joiner == " OR "):
                break

            match = joiner.join(f'"{word}"*' for word in words)
            rows = self._db.execute(
                "SELECT f.* FROM foods_fts JOIN foods f ON f.rowid = foods_fts.rowid "
                "WHERE foods_fts MATCH ? ORDER BY bm25(foods_fts, 10, 1), length(f.name) LIMIT ?",
                (match, limit * 2),
            ).fetchall()

            for row in rows:
                found.setdefault(row["id"], _food(row))

        return list(found.values())[:limit]

    def _meta(self, key: str) -> str:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()

        return row["value"] if row is not None else ""

    def close(self) -> None:
        self._db.close()


def _food(row: sqlite3.Row) -> SgFood:
    alts = json.loads(row["alt_servings_json"])

    return SgFood(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        food_group=row["food_group"],
        subgroup=row["subgroup"],
        kcal_100g=row["kcal_100g"],
        protein_100g=row["protein_100g"],
        fat_100g=row["fat_100g"],
        sat_fat_100g=row["sat_fat_100g"],
        carb_100g=row["carb_100g"],
        sugar_100g=row["sugar_100g"],
        fibre_100g=row["fibre_100g"],
        sodium_mg_100g=row["sodium_mg_100g"],
        default_serving_label=row["default_serving_label"],
        default_serving_g=row["default_serving_g"],
        alt_servings=tuple((alt["label"], alt["grams"]) for alt in alts),
        data_source=row["data_source"],
        last_updated_year=row["last_updated_year"],
        kcal_mismatch=bool(row["kcal_mismatch"]),
    )


def _alias_map(path: Path | None) -> dict[str, str]:
    """Lower-case alias -> exact item name, from a file of {item name: [aliases]}."""
    if path is None or not path.exists():
        return {}

    raw: dict[str, list[str]] = json.loads(path.read_text())

    return {alias.lower(): name for name, aliases in raw.items() for alias in aliases}
