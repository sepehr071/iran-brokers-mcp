import json
from pathlib import Path

import httpx
import pytest
from mcp import Client

from iran_brokers_mcp.agah import http as agah_http
from iran_brokers_mcp.mofid import http as mofid_http
from iran_brokers_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"
CLIENTS = (mofid_http, agah_http)


def fixture(name: str):
    """A saved real response; Mofid and Agah fixtures live in their own folders and never share a name."""
    path = next(p for p in (FIXTURES / "mofid" / name, FIXTURES / "agah" / name) if p.exists())
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_http_client(monkeypatch):
    """The shared httpx clients are bound to one event loop; each test gets new ones."""
    for h in CLIENTS:
        monkeypatch.setattr(h, "RETRY_DELAY", 0)
        monkeypatch.setattr(h, "JITTER", (0, 0))
        h.set_transport(None)
    yield
    for h in CLIENTS:
        h.set_transport(None)


@pytest.fixture
def api():
    """Route table for a fake upstream: api["/path"] = JSON body, or a callable(request) -> httpx.Response.

    Both providers' clients use it. Keys match the URL path only (host ignored). Unknown paths return 404.
    Every request is appended to api.calls so tests can assert on query params and headers.
    """

    class Routes(dict):
        calls: list[httpx.Request]

    routes = Routes()
    routes.calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        target = routes.get(request.url.path)
        if target is None:
            return httpx.Response(404, json={"error": "no fake route"})
        if callable(target):
            return target(request)
        return httpx.Response(200, json=target)

    for h in CLIENTS:
        h.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        call_tool = c.call_tool

        async def parsed(*args, **kwargs):
            result = await call_tool(*args, **kwargs)
            if not result.is_error:
                result.structured_content = json.loads(result.content[0].text)
            return result

        c.call_tool = parsed
        yield c
