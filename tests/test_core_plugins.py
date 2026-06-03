"""Tests for entry-point plugin discovery and adapter resolution (NFR-17, NFR-19, NFR-9; SG-9)."""

from __future__ import annotations

import pytest

from mfo.core import plugins
from mfo.core.plugins import discover_plugins, resolve_factory
from mfo.vision import detect


class _FakeEntryPoint:
    """Stand-in for ``importlib.metadata.EntryPoint`` with a controllable ``load()``."""

    def __init__(self, name: str, factory: object, *, fail: bool = False) -> None:
        self.name = name
        self._factory = factory
        self._fail = fail

    def load(self) -> object:
        if self._fail:
            raise ImportError(f"cannot import plugin {self.name!r}")
        return self._factory


def _patch_entry_points(
    monkeypatch: pytest.MonkeyPatch, registered_group: str, eps: list[object]
) -> None:
    def fake_entry_points(*, group: str = "") -> list[object]:
        return eps if group == registered_group else []

    monkeypatch.setattr(plugins, "entry_points", fake_entry_points)


def _factory() -> str:
    return "made-by-plugin"


def test_discover_plugins_returns_registered_factories(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, "mfo.detectors", [_FakeEntryPoint("fake", _factory)])
    found = discover_plugins("mfo.detectors")
    assert set(found) == {"fake"}
    assert found["fake"]() == "made-by-plugin"


def test_discover_plugins_empty_when_none_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, "mfo.detectors", [])
    assert discover_plugins("mfo.translators") == {}


def test_broken_plugin_is_skipped_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    eps = [_FakeEntryPoint("broken", None, fail=True), _FakeEntryPoint("ok", _factory)]
    _patch_entry_points(monkeypatch, "mfo.ocr", eps)
    with pytest.warns(UserWarning, match="skipping plugin 'broken'"):
        found = discover_plugins("mfo.ocr")
    # The good plugin still resolves; the broken one never makes resolution fatal (NFR-9).
    assert set(found) == {"ok"}


def test_resolve_factory_prefers_builtins(monkeypatch: pytest.MonkeyPatch) -> None:
    builtins = {"x": lambda: "builtin"}
    # A plugin claims the same name; the built-in must win and the registry is never even scanned.
    monkeypatch.setattr(
        plugins, "entry_points", lambda *, group="": pytest.fail("plugins scanned for a built-in")
    )
    assert resolve_factory("x", builtins, "mfo.detectors", kind="detector")() == "builtin"


def test_resolve_factory_falls_back_to_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, "mfo.detectors", [_FakeEntryPoint("fake", _factory)])
    factory = resolve_factory("fake", {"builtin": _factory}, "mfo.detectors", kind="detector")
    assert factory() == "made-by-plugin"


def test_resolve_factory_unknown_lists_builtins_and_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_entry_points(monkeypatch, "mfo.detectors", [_FakeEntryPoint("pluginname", _factory)])
    with pytest.raises(ValueError, match="unknown detector 'nope'") as exc:
        resolve_factory("nope", {"builtin": _factory}, "mfo.detectors", kind="detector")
    message = str(exc.value)
    assert "builtin" in message and "pluginname" in message


# --- integration: a get_* resolver finds and runs an entry-point detector (DoD) ---


def _fake_detector_factory(*, lang: str | None = None) -> detect.RegionDetector:
    return detect.ConnectedComponentsDetector()


def test_get_detector_resolves_entry_point_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(
        monkeypatch, "mfo.detectors", [_FakeEntryPoint("thirdparty", _fake_detector_factory)]
    )
    detector = detect.get_detector("thirdparty", merge_overlap=False)
    assert isinstance(detector, detect.ConnectedComponentsDetector)


def test_builtin_detectors_resolve_without_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    # No plugins installed: the offline built-ins must still resolve (I-7/I-8).
    _patch_entry_points(monkeypatch, "mfo.detectors", [])
    assert detect.get_detector("baseline") is not None
