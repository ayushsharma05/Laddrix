"""The API's routes, one module per area (server/routes/, #441): none can hide another.

FastAPI tries routes in the order they were added and takes the first that fits. With the
routes spread over several modules, a broad one (/api/alerts/{alert_id}) added before a
narrow one (/api/alerts/webhook) would quietly catch its requests. The production server is
built (tools/serve.build, with the screens) and every route is asked for with its own
address: the route that answers must be that route.
"""

import re

import pytest
from fastapi.routing import APIRoute

PARAM = re.compile(r"\{(\w+)(?::\w+)?\}")


@pytest.fixture
def app(tmp_path):
    from test_serve import env

    from mocktrade.tools.serve import ServeSettings, build

    ui, data = tmp_path / "ui", tmp_path / "data"
    ui.mkdir()
    (ui / "index.html").write_text("<html>MockTrade</html>")
    data.mkdir()
    built = build(ServeSettings.from_env(env(MOCKTRADE_DATA=str(data), MOCKTRADE_UI=str(ui))))
    yield built.app
    if getattr(built, "fence", None) is not None:
        built.fence.release()


def every_route(routes) -> list[APIRoute]:
    """In the order they are tried, inside included routers too."""
    out: list[APIRoute] = []
    for r in routes:
        if hasattr(r, "original_router"):  # an included router, kept whole (FastAPI 0.14x)
            out += every_route(r.original_router.routes)
        elif isinstance(r, APIRoute):
            out.append(r)
    return out


def address(route: APIRoute) -> str:
    """A real address for the route: each {name} filled with a value no literal part uses."""
    return PARAM.sub(lambda m: f"v-{m.group(1)}", route.path)


def test_every_route_answers_its_own_address(app):
    routes = every_route(app.routes)
    assert len(routes) > 100  # the whole API is here, the admin API included
    hidden = []
    for i, route in enumerate(routes):
        path = address(route)
        for earlier in routes[:i]:
            if earlier.methods & route.methods and earlier.path_regex.match(path):
                hidden.append(f"{sorted(route.methods)} {route.path} is caught by {earlier.path}")
                break
    assert hidden == []


def test_the_screens_come_after_the_whole_api(app):
    routes = every_route(app.routes)
    assert routes[-1].path == "/{path:path}"  # the built screens: anything the API isn't


def test_a_broad_route_added_first_would_be_caught():
    """The check itself: /api/alerts/{alert_id} before /api/alerts/webhook hides it."""
    from fastapi import APIRouter

    api = APIRouter()
    api.add_api_route("/api/alerts/{alert_id}", lambda alert_id: None, methods=["DELETE"])
    api.add_api_route("/api/alerts/webhook", lambda: None, methods=["DELETE"])
    broad, narrow = every_route(api.routes)
    assert broad.path_regex.match(address(narrow))
