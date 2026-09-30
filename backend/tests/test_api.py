"""API smoke tests: health, input errors vs infeasibility, success shape."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _exposure(id, **kw):
    base = dict(
        id=id, duration=2, earliest_start=0, latest_start=100,
        equipment="X", cooling=0,
    )
    base.update(kw)
    return base


def _body(exposures=None, links=None, horizon=1000):
    return {
        "horizon": horizon,
        "exposures": exposures if exposures is not None else [
            _exposure("A"), _exposure("B", equipment="Y"),
            _exposure("C", equipment="Z"), _exposure("D", equipment="W"),
            _exposure("F", equipment="V"),
        ],
        "links": links or [],
    }


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_feasible_response_shape():
    r = client.post("/api/schedule", json=_body())
    assert r.status_code == 200
    data = r.json()
    assert data["feasible"] is True
    assert len(data["starts"]) == 5
    assert len(data["finishes"]) == 5
    assert data["makespan"] is not None
    assert isinstance(data["equipment_orders"], list)


def test_infeasible_is_200_without_partial_solution():
    body = _body(exposures=[
        _exposure("A", duration=5, latest_start=4),
        _exposure("B", duration=5, latest_start=4),
        _exposure("C", equipment="Y"), _exposure("D", equipment="Z"),
        _exposure("F", equipment="W"),
    ])
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["feasible"] is False
    assert data["reason"] == "no_schedule"
    assert data["starts"] is None
    assert data["finishes"] is None


def test_input_error_is_400():
    body = _body(exposures=[
        _exposure("A", earliest_start=40, latest_start=10),
        _exposure("B"), _exposure("C", equipment="Y"),
        _exposure("D", equipment="Z"), _exposure("F", equipment="W"),
    ])
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 400
    data = r.json()
    assert data["feasible"] is False
    assert data["reason"] == "input_error"
    assert data["field_errors"]


def test_unknown_link_reference_is_400():
    body = _body(links=[{"from_id": "A", "to_id": "ZZ", "min_gap": 0}])
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 400
    assert "unknown exposure" in " ".join(r.json()["field_errors"])


def test_schema_rejects_wrong_count():
    body = _body()
    body["exposures"] = body["exposures"][:3]
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 422


def test_schema_rejects_negative_duration():
    body = _body(exposures=[
        _exposure("A", duration=-1), _exposure("B"),
        _exposure("C", equipment="Y"), _exposure("D", equipment="Z"),
        _exposure("F", equipment="W"),
    ])
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 422


def test_openapi_available():
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/api/schedule" in r.json()["paths"]


def _cooling_body(consumptions, cooling, latest=None):
    body = _body(exposures=[
        _exposure(chr(65 + i), equipment=chr(88 + i), duration=1,
                  startup_consumption=consumptions[i],
                  **({"latest_start": latest} if latest is not None else {}))
        for i in range(5)
    ])
    body["shared_cooling"] = cooling
    return body


def test_shared_cooling_feasible_returns_trace():
    body = _cooling_body(
        [3, 3, 3, 3, 3], {"capacity": 10, "initial": 10, "recovery": 5})
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["feasible"] is True
    trace = data["cooling_trace"]
    assert trace is not None and len(trace) == 5
    # Chronological order, ties broken by entry order.
    times = [(s["start"], s["exposure_id"]) for s in trace]
    assert times == sorted(times, key=lambda x: (x[0], x[1]))
    for step in trace:
        assert step["level_before"] - step["consumption"] == step["level_after"]
        assert step["level_after"] >= 0
    # Without cooling the same exposures all start at 0; cooling forces waits.
    assert any(s > 0 for s in data["starts"])


def test_shared_cooling_insufficient_is_200_no_schedule():
    body = _cooling_body(
        [6, 6, 0, 0, 0], {"capacity": 10, "initial": 10, "recovery": 0},
        latest=4)
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["feasible"] is False
    assert data["reason"] == "no_schedule"
    # Never a partial plan: every solution field is null, trace included.
    for k in ("starts", "finishes", "makespan", "sum_starts",
              "slacks", "equipment_orders", "cooling_trace"):
        assert data[k] is None


def test_shared_cooling_initial_over_capacity_is_400():
    body = _cooling_body(
        [1, 1, 1, 1, 1], {"capacity": 5, "initial": 6, "recovery": 1})
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 400
    data = r.json()
    assert data["reason"] == "input_error"
    assert any("initial" in m and "capacity" in m for m in data["field_errors"])


def test_shared_cooling_obstruction_diagnostic():
    # First 6-unit draw leaves 4; the second 6-unit draw can never fit within
    # the tight window with zero recovery.
    body = _cooling_body(
        [6, 6, 0, 0, 0], {"capacity": 10, "initial": 10, "recovery": 0},
        latest=4)
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["feasible"] is False
    ob = data["cooling_obstruction"]
    assert ob == {"exposure_id": "B", "entry_index": 1, "feasible_count": 1}


def test_disabled_cooling_response_shape_unchanged():
    r = client.post("/api/schedule", json=_body())
    assert r.status_code == 200
    data = r.json()
    assert "cooling_trace" not in data

