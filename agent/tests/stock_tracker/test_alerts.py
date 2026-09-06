"""Unit tests for buy-signal alert persistence and de-duplication."""

from __future__ import annotations

from src.stock_tracker.alerts import AlertStore, spec_fingerprint


def _hit(code: str, signal_date: str = "2026-09-05") -> dict:
    return {
        "code": code,
        "name": "贵州茅台",
        "price": 1500.0,
        "signal_date": signal_date,
    }


def test_spec_fingerprint_is_stable_and_key_order_insensitive():
    a = {"buy": {"mode": "and", "conditions": [{"primitive": "x"}]}}
    b = {"buy": {"conditions": [{"primitive": "x"}], "mode": "and"}}
    assert spec_fingerprint(a) == spec_fingerprint(a)
    # dict key order must not matter (sort_keys=True).
    assert spec_fingerprint(a) == spec_fingerprint(b)


def test_spec_fingerprint_differs_on_rule_change():
    a = {"buy": {"mode": "and", "conditions": [{"primitive": "macd_golden_cross"}]}}
    b = {"buy": {"mode": "and", "conditions": [{"primitive": "kdj_golden_cross"}]}}
    assert spec_fingerprint(a) != spec_fingerprint(b)


def test_emit_deduplicates_same_code_and_signal_date(tmp_path):
    store = AlertStore(root=tmp_path)
    spec = {"buy": {"mode": "and", "conditions": []}}
    first = store.emit([_hit("600519.SH")], spec)
    second = store.emit([_hit("600519.SH")], spec)
    assert len(first) == 1
    assert len(second) == 0
    assert store.list() == first  # newest-first, single entry


def test_emit_re_notifies_after_strategy_change(tmp_path):
    store = AlertStore(root=tmp_path)
    spec_a = {"buy": {"mode": "and", "conditions": [{"primitive": "a"}]}}
    spec_b = {"buy": {"mode": "and", "conditions": [{"primitive": "b"}]}}
    assert len(store.emit([_hit("600519.SH")], spec_a)) == 1
    # A different fingerprint resets the de-dupe map, so the same signal re-fires.
    assert len(store.emit([_hit("600519.SH")], spec_b)) == 1


def test_emit_skips_hits_without_code(tmp_path):
    store = AlertStore(root=tmp_path)
    emitted = store.emit([{"name": "no code"}, _hit("000001.SZ")], {"buy": {}})
    assert [a.code for a in emitted] == ["000001.SZ"]


def test_list_unread_only_and_acknowledge(tmp_path):
    store = AlertStore(root=tmp_path)
    spec = {"buy": {"mode": "and", "conditions": []}}
    store.emit([_hit("600519.SH"), _hit("000001.SZ", "2026-09-04")], spec)

    assert len(store.list()) == 2
    assert len(store.list(unread_only=True)) == 2

    updated = store.acknowledge()
    assert updated == 2
    assert store.list(unread_only=True) == []


def test_acknowledge_specific_ids(tmp_path):
    store = AlertStore(root=tmp_path)
    spec = {"buy": {"mode": "and", "conditions": []}}
    emitted = store.emit([_hit("600519.SH"), _hit("000001.SZ")], spec)
    target = [emitted[0].id]
    assert store.acknowledge(target) == 1
    assert len(store.list(unread_only=True)) == 1
