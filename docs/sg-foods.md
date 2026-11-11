# Singapore foods and custom Foods (fork additions)

This fork adds two things to upstream 0.3.0. Both are off unless `FATSECRET_DATA_DIR` is set.

## `search_sg_foods`

A read-only tool over a local SQLite table of HPB's [SG FoodID](https://pphtpc.hpb.gov.sg/web/sgfoodid)
(hawker dishes, drinks, cooked and packaged foods) with kcal and Macros per 100 g, serving sizes, HPB's data
basis and year. Search is SQLite FTS5 plus an alias list of hawker shorthand ("teh peng", "ckt"). Results carry
HPB's attribution, which Claude shows.

`FATSECRET_DATA_DIR` holds three files, built outside the server:

| File | What |
|---|---|
| `sgfoodid.sqlite` | The table: `foods` (id, name, per-100g values, servings, `data_source`, `kcal_mismatch`, ...), `foods_fts`, `meta` (attribution). Opened read-only. |
| `aliases.json` | `{"exact item name": ["alias", ...]}`; an alias match ranks first. |
| `registry.sqlite` | Created by the server when custom Foods are on. See below. |

Values HPB doesn't have are NULL, never invented. An item without protein, fat or carbohydrate can't be logged.

## Custom Foods (`FATSECRET_PREMIER=true`)

Needs FatSecret's Premier plan (free for qualifying developers). Without it, behaviour is upstream's.

- A draft item with `hpb_id` (from `search_sg_foods`) plus `grams` or `servings` logs the dish with HPB's own
  values. The server reads them from the table; Claude passes only the id and the amount.
- A Nutrition Label item with `estimated=false` also becomes a custom Food, named like the product and reused by
  name. A label read again with values more than 5% apart shows a warning and reuses the stored Food;
  `recipe_changed=true` creates a new Food named with the year. Estimated labels stay Stand-ins.
- Each Food has one Serving: HPB's default serving (e.g. "1 plate (323 g)"), or 100 g for a label.
- The draft says whether confirming creates a **new permanent Food**. FatSecret has no API to edit, delete or list
  custom Foods, so the registry (`registry.sqlite`, per User and key) is the only record. It is written the moment
  FatSecret returns a Food id, so a retry after a failed entry never creates a second Food. Keep it on a volume
  and back it up.
- Known gap: if FatSecret doesn't answer a `food.create.v2` call, the Food may exist without a registry entry.
  `confirm_draft` then asks the User to check the Food Diary, and a retry could create a duplicate.
- `food.create.v2` gives only a food id. The server reads the Food back with the User's token (`food.get.v5`) to
  find its Serving and logs `number_of_units` in that Serving's units.
