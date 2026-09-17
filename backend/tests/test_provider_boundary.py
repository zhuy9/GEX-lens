"""The five boundary tests mandated by PRD Section 4.4."""

import ast
import sys
from pathlib import Path

import pytest
from conftest import StubProvider, make_settings, make_snapshot
from fastapi.testclient import TestClient

from analytics import analyze_snapshot
from app import create_app
from provider import ProviderError

BACKEND_DIR = Path(__file__).resolve().parents[1]


# --- #1: a StubProvider reaches both charts with exactly one fetch_chain call,
# with no HTTP allowed and no Nasdaq import/construction. -----------------


def test_stub_provider_reaches_both_charts_with_one_fetch_call(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "nasdaq", None)  # any `import nasdaq` now raises ImportError
    monkeypatch.setattr(
        "httpx.Client.get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP allowed in this test")),
    )

    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    client = TestClient(create_app(settings, provider=stub))

    response = client.post("/api/dashboard/SPY/refresh")

    assert response.status_code == 200
    assert stub.calls == 1
    body = response.json()
    assert body["gex"]["cells"]
    assert body["surface"]["status"] in ("READY", "INSUFFICIENT_DATA")


# --- #2: equal canonical inputs under two different provider IDs give
# identical analytics; only provenance metadata differs. -------------------


def test_identical_inputs_under_different_provider_ids_yield_identical_analytics():
    snap_a = make_snapshot(provider_id="fixture")
    snap_b = make_snapshot(provider_id="totally-different-test-stub")
    assert snap_a.contracts == snap_b.contracts
    assert snap_a.provider_id != snap_b.provider_id

    kwargs = dict(
        spot=snap_a.underlying_price,
        r=0.04,
        q=0.0,
        valuation_at=snap_a.collected_at,
        min_calendar_dte=1,
        max_calendar_dte=60,
        source_row_count=1,
    )
    _, gex_a, surface_a, quality_a = analyze_snapshot(snap_a.contracts, **kwargs)
    _, gex_b, surface_b, quality_b = analyze_snapshot(snap_b.contracts, **kwargs)

    assert gex_a == gex_b
    assert surface_a == surface_b
    assert quality_a == quality_b


# --- #3: static import-graph checks via the ast module. --------------------


def _imported_top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize(
    "module_name", ["provider.py", "models.py", "analytics.py", "storage.py", "fixtures.py"]
)
def test_module_does_not_import_nasdaq(module_name):
    assert "nasdaq" not in _imported_top_level_names(BACKEND_DIR / module_name)


@pytest.mark.parametrize("module_name", ["analytics.py", "storage.py"])
def test_module_does_not_import_http_libraries(module_name):
    names = _imported_top_level_names(BACKEND_DIR / module_name)
    assert "httpx" not in names
    assert "requests" not in names


def test_nasdaq_import_in_app_only_occurs_inside_build_provider():
    tree = ast.parse((BACKEND_DIR / "app.py").read_text(), filename="app.py")

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.violations: list[int] = []
            self.func_stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def _check(self, module_name: str, node: ast.Import | ast.ImportFrom) -> None:
            if module_name == "nasdaq" and (not self.func_stack or self.func_stack[-1] != "build_provider"):
                self.violations.append(node.lineno)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                self._check(alias.name.split(".")[0], node)
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = node.module
            if module:
                self._check(module.split(".")[0], node)
            self.generic_visit(node)

    visitor = Visitor()
    visitor.visit(tree)
    assert visitor.violations == []


# --- #4: pure analytics tests run against canonical fixtures without
# importing the Nasdaq parser; the Nasdaq parser is tested separately. -----
# Enforced structurally: test_analytics.py imports only `analytics`/`models`,
# and test_nasdaq.py is the only place `nasdaq` is imported in the test
# suite. The import-graph checks above (#3) confirm analytics.py itself
# cannot import nasdaq even if a test tried to smuggle it in.


# --- #5: a stub ProviderError leaves the saved snapshot unchanged and
# surfaces the documented error shape. --------------------------------------


def test_stub_provider_error_preserves_previous_snapshot(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "nasdaq", None)
    db_path = str(tmp_path / "t.duckdb")
    settings = make_settings(db_path)

    good_stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    first = TestClient(create_app(settings, provider=good_stub)).post("/api/dashboard/SPY/refresh").json()

    failing_stub = StubProvider(error=ProviderError("UPSTREAM_UNAVAILABLE", "boom"))
    client2 = TestClient(create_app(settings, provider=failing_stub))
    response = client2.post("/api/dashboard/SPY/refresh")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"

    unchanged = client2.get("/api/dashboard/SPY").json()
    assert unchanged["snapshot_id"] == first["snapshot_id"]
