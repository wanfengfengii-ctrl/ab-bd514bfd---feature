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


# ---------------- shared cooling -----------------------------------------

def _cooling_body(demands, *, capacity=10, initial_amount=10,
                  recovery_per_time=0, latest=None):
    latest = latest if latest is not None else [50] * 5
    eqs = ["X", "Y", "Z", "W", "V"]
    return {
        "horizon": 200,
        "exposures": [
            _exposure(c, equipment=q, startup_demand=d, latest_start=ls)
            for c, q, d, ls in zip("ABCDE", eqs, demands, latest)
        ],
        "links": [],
        "shared_cooling": {
            "capacity": capacity,
            "initial_amount": initial_amount,
            "recovery_per_time": recovery_per_time,
        },
    }


def test_shared_cooling_success_events_shape():
    body = _cooling_body([6, 5, 1, 1, 1], initial_amount=6,
                         recovery_per_time=2)
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["feasible"] is True
    events = data["cooling_events"]
    assert len(events) == 5
    assert [ev["order"] for ev in events] == [0, 1, 2, 3, 4]
    for ev in events:
        assert {"order", "exposure_id", "start", "recovered",
                "level_before", "level_after", "demand"} <= ev.keys()
        assert ev["level_after"] >= 0
        assert ev["level_after"] == ev["level_before"] - ev["demand"]


def test_shared_cooling_shortage_is_200_without_partial():
    body = _cooling_body([4, 6, 5, 1, 1], latest=[0] * 5)
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["feasible"] is False
    assert data["reason"] == "no_schedule"
    assert data["starts"] is None
    assert data["cooling_events"] is None


def test_shared_cooling_initial_over_capacity_is_400():
    body = _cooling_body([1] * 5, capacity=5, initial_amount=6,
                         recovery_per_time=1)
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 400
    data = r.json()
    assert data["reason"] == "input_error"
    assert any("initial_amount" in m for m in data["field_errors"])


def test_shared_cooling_missing_demand_is_400():
    body = _cooling_body([1] * 5, capacity=5, initial_amount=5,
                         recovery_per_time=1)
    del body["exposures"][0]["startup_demand"]
    r = client.post("/api/schedule", json=body)
    assert r.status_code == 400
    assert any("startup_demand" in m for m in r.json()["field_errors"])


def test_legacy_body_without_shared_cooling_has_null_events():
    r = client.post("/api/schedule", json=_body())
    assert r.status_code == 200
    data = r.json()
    assert data["feasible"] is True
    assert data["cooling_events"] is None
