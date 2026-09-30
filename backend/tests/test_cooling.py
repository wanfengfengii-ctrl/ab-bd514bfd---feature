"""Tests for the shared cryogenic coolant bank, solved jointly with starts."""
import pytest

from app.scheduler import InputValidationError, solve
from app.schemas import Link


def _exp(id_, equipment, demand, earliest=0, latest=50, duration=2):
    return dict(
        id=id_, duration=duration, earliest_start=earliest,
        latest_start=latest, equipment=equipment, cooling=0,
        startup_demand=demand,
    )


def _req(demands, *, cap=10, init=10, rate=0, links=None):
    exps = [
        _exp(c, q, d)
        for c, q, d in zip("ABCDE", "XYZWV", demands)
    ]
    return {
        "exposures": exps,
        "links": links or [],
        "shared_cooling": {
            "capacity": cap, "initial_amount": init, "recovery_per_time": rate,
        },
    }


def _events_by_id(r):
    return {ev["exposure_id"]: ev for ev in r["cooling_events"]}


def test_coolant_forces_postponement(builder):
    # Independent equipment: without the bank every exposure would start at 0.
    # init=6, rate=2: A(6) at 0 empties the bank and B(5) could only start at 3
    # (makespan 8); delaying A to 4 instead makes B affordable at 1 while
    # keeping the makespan at 6, so the joint optimization postpones A.
    req = builder(**_req([6, 5, 1, 1, 1], cap=10, init=6, rate=2))
    r = solve(req, phase_seconds=5)
    assert r["feasible"] is True
    assert r["starts"] == [4, 1, 0, 0, 0]
    assert r["makespan"] == 6

    by_id = _events_by_id(r)
    a, b = by_id["A"], by_id["B"]
    assert (a["level_before"], a["recovered"], a["level_after"]) == (6, 6, 0)
    assert b["start"] == 1
    assert (b["level_before"], b["recovered"], b["level_after"]) == (5, 2, 0)


def test_same_time_startups_deduct_in_entry_order_without_recovery(builder):
    req = builder(**_req([4, 6, 1, 1, 1], cap=10, init=10, rate=2))
    r = solve(req, phase_seconds=5)
    assert r["feasible"] is True
    assert r["starts"] == [0, 2, 0, 0, 0]

    events = r["cooling_events"]
    # Chronological order, ties broken by entry order.
    assert [(ev["exposure_id"], ev["start"]) for ev in events] == [
        ("A", 0), ("C", 0), ("D", 0), ("E", 0), ("B", 2),
    ]
    same_t0 = [ev for ev in events if ev["start"] == 0]
    assert all(ev["recovered"] == 0 for ev in same_t0)
    assert [(ev["level_before"], ev["level_after"]) for ev in same_t0] == [
        (10, 6), (6, 5), (5, 4), (4, 3),
    ]
    b = events[-1]
    assert (b["level_before"], b["recovered"], b["level_after"]) == (7, 4, 1)


def test_recovery_is_capped_at_capacity(builder):
    exps = [
        _exp("A", "X", 7, earliest=4),
        *[_exp(c, q, 0) for c, q in zip("BCDE", "YZWV")],
    ]
    req = builder(exps, shared_cooling={
        "capacity": 10, "initial_amount": 3, "recovery_per_time": 100,
    })
    r = solve(req, phase_seconds=5)
    assert r["feasible"] is True
    a = _events_by_id(r)["A"]
    # An arbitrarily high rate over 4 units must be clamped to capacity 10.
    assert a["level_before"] == 10
    assert a["recovered"] == 7
    assert a["level_after"] == 3


def test_insufficient_coolant_is_infeasible_without_partial_solution(builder):
    # A single demand beyond what the bank can ever hold can never be met.
    r = solve(builder(**_req([99, 1, 1, 1, 1], cap=10, init=10, rate=1)),
              phase_seconds=5)
    assert r["feasible"] is False
    assert r["reason"] == "infeasible"
    assert "starts" not in r
    assert "cooling_events" not in r

    # No recovery and the combined t=0 demand exceeds the initial charge.
    exps = [_exp(c, q, d, latest=0)
            for c, q, d in zip("ABCDE", "XYZWV", [4, 6, 5, 1, 1])]
    req = builder(exps, shared_cooling={
        "capacity": 10, "initial_amount": 10, "recovery_per_time": 0,
    })
    r2 = solve(req, phase_seconds=5)
    assert r2["feasible"] is False
    assert "starts" not in r2


def test_cooling_events_are_internally_consistent(builder):
    req = builder(**_req([3, 4, 2, 5, 1], cap=8, init=5, rate=2))
    r = solve(req, phase_seconds=5)
    assert r["feasible"] is True

    starts_by_id = {e.id: s for e, s in zip(req.exposures, r["starts"])}
    entry_index = {e.id: i for i, e in enumerate(req.exposures)}
    prev_after, prev_start = 5, 0
    for k, ev in enumerate(r["cooling_events"]):
        assert ev["order"] == k
        assert ev["start"] == starts_by_id[ev["exposure_id"]]
        assert ev["level_after"] == ev["level_before"] - ev["demand"]
        assert 0 <= ev["level_after"] <= ev["level_before"] <= 8
        assert ev["level_before"] == prev_after + ev["recovered"]
        if k > 0:
            gap = ev["start"] - prev_start
            assert ev["recovered"] == min(2 * gap, 8 - prev_after)
            assert (ev["start"], entry_index[ev["exposure_id"]]) > (
                prev_start, entry_index[r["cooling_events"][k - 1]["exposure_id"]]
            )
        prev_after = ev["level_after"]
        prev_start = ev["start"]


def test_ample_coolant_preserves_original_optimum(builder):
    r = solve(builder(**_req([1] * 5, cap=10, init=10, rate=0)), phase_seconds=5)
    assert r["feasible"] is True
    assert r["starts"] == [0, 0, 0, 0, 0]
    assert (r["makespan"], r["sum_starts"]) == (2, 0)


def test_coolant_plus_link_solved_jointly(builder):
    req = builder(**_req(
        [4, 4, 1, 1, 1], cap=10, init=4, rate=2,
        links=[Link(from_id="A", to_id="B", min_gap=0, max_gap=3)],
    ))
    r = solve(req, phase_seconds=5)
    assert r["feasible"] is True
    s = {eid: s_ for eid, s_ in zip("ABCDE", r["starts"])}
    gap = s["B"] - (s["A"] + 2)
    assert 0 <= gap <= 3
    assert all(ev["level_after"] >= 0 for ev in r["cooling_events"])


def test_initial_amount_above_capacity_is_input_error(builder):
    with pytest.raises(InputValidationError) as ei:
        solve(builder(**_req([1] * 5, cap=5, init=6, rate=1)), phase_seconds=2)
    assert any("initial_amount" in m for m in ei.value.errors)


def test_missing_startup_demand_is_input_error(builder):
    req = builder(**_req([1] * 5, cap=5, init=5, rate=1))
    req.exposures[2].startup_demand = None
    with pytest.raises(InputValidationError) as ei:
        solve(req, phase_seconds=2)
    assert any("startup_demand missing" in m and "C" in m
               for m in ei.value.errors)


def test_disabled_cooling_keeps_legacy_response(builder, E):
    req = builder([
        E("A", 2, 0, 50, "X"), E("B", 3, 0, 50, "Y"),
        E("C", 2, 0, 50, "Z"), E("D", 4, 0, 50, "W"),
        E("F", 1, 0, 50, "V"),
    ])
    r = solve(req, phase_seconds=2)
    assert r["feasible"] is True
    assert r["cooling_events"] is None
    assert r["starts"] == [0, 0, 0, 0, 0]
