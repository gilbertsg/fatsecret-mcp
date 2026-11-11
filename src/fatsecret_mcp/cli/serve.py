"""`serve`: run the server locally over stdio, or serve the Household's connector over HTTP.

Heavy modules are imported only when needed, so the HTTP server starts quickly after a cold start.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from fatsecret_mcp.config.loader import acting_user, load_household, validate_http_household

if TYPE_CHECKING:
    from fatsecret_mcp.config.models import Household, User

Transport = Literal["stdio", "http"]


async def serve(environ: Mapping[str, str], *, transport: Transport, host: str, port: int) -> None:
    household = load_household(environ)

    if transport == "http":
        validate_http_household(household)

        await _serve_http(household, host, port)
    else:
        await _serve_stdio(household, acting_user(household, environ))


async def _serve_stdio(household: Household, user: User) -> None:
    from fatsecret_mcp.domain.diary_day import system_clock
    from fatsecret_mcp.fatsecret.budget import daily_budget
    from fatsecret_mcp.fatsecret.client import connected_api
    from fatsecret_mcp.fatsecret.food_cache import FoodCache
    from fatsecret_mcp.server import build_server
    from fatsecret_mcp.sgfoods.support import custom_foods_from_environment

    food_cache = FoodCache(clock=system_clock)

    async with connected_api(household.consumer) as fatsecret, food_cache.lifespan():
        server = build_server(
            user=user,
            fatsecret=fatsecret,
            signing_key=household.signing_key,
            daily_requests=daily_budget(len(household.users)),
            food_cache=food_cache,
            custom=custom_foods_from_environment(),
        )

        await server.run_stdio_async()


async def _serve_http(household: Household, host: str, port: int) -> None:
    import uvicorn

    from fatsecret_mcp.fatsecret.client import connected_api
    from fatsecret_mcp.http import build_http_app

    async with connected_api(household.consumer) as fatsecret:
        app = build_http_app(household, fatsecret)
        config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            access_log=False,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        await server.serve()
