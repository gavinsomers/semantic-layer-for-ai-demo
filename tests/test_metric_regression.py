"""Metric regression on frozen data (guide ch 6): every expected value still holds."""

import yaml

from ci import regression


def test_every_certified_metric_has_an_expected_value():
    from serving import layer
    covered = {e["metric"] for e in yaml.safe_load(regression.EXPECTED.read_text())["expected"]}
    certified = {n for n, m in layer.metrics().items()
                 if m["meta"]["status"] == "certified" and not m["meta"].get("hidden")
                 and m["type"] != "cumulative"}
    assert certified <= covered, f"no expected value for {sorted(certified - covered)}"


def test_metric_values_on_frozen_data_have_not_moved():
    lines = regression.check()
    assert lines and all(line.startswith("PASS") for line in lines), "\n".join(lines)


def test_regression_reports_a_moved_value():
    entry = {"id": "x", "metric": "goods_value", "total": 1500}
    result = {"path": "governed", "total": 1450, "values": []}
    assert regression.compare(entry, result) == "total expected 1500, got 1450"
