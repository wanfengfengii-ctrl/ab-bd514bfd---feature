"""Unit tests for the shared cryocooler budget constraint."""
import pytest

from app.scheduler import InputValidationError, compute_cooling_trace, solve
from app.schemas import Link


def _ids(n):
    return [chr(65 + i) for i in range(n)]


def test_disabled_request_has_no_cooling_trace(builder, E):
    req = builder([
        E("A", equipment="X"), E("B", equipment="Y"), E("C", equipment="Z"),
        E("D", equipment="W"), E("F", equipment="V"),
    ])
    r = solve(req, phase_seconds=2)
    assert r["feasible"] is True
    assert "cooling_trace" not in r
    assert r["starts"] == [0, 0, 0, 0, 0]


def test_zero_consumptions_everything_at_zero(builder, E, C):
    req = builder(
        [E(chr(65 + i), startup=0, equipment=chr(88 + i)) for i in range(5)],
        shared_cooling=C(capacity=10, initial=0, recovery=0),
    )
    r = solve(req, phase_seconds=2)
    assert r["feasible"] is True
    assert r["starts"] == [0, 0, 0, 0, 0]
    trace = r["cooling_trace"]
    assert [(s["exposure_id"], s["start"], s["recovery"],
             s["level_before"], s["consumption"], s["level_after"]) for s in trace] == [
        ("A", 0, 0, 0, 0, 0),
        ("B", 0, 0, 0, 0, 0),
        ("C", 0, 0, 0, 0, 0),
        ("D", 0, 0, 0, 0, 0),
        ("E", 0, 0, 0, 0, 0),
    ]


def test_same_time_draws_deduct_back_to_back_in_entry_order(builder, E, C):
    # Initial 10, no recovery: 3+3+3 = 9 fits at t=0, the remaining two draws
    # must move to later times. With zero recovery nothing ever comes back...
    req = builder(
        [E(chr(65 + i), duration=1, startup=3, equipment=chr(88 + i))
         for i in range(5)],
        shared_cooling=C(capacity=10, initial=10, recovery=0),
    )
    r = solve(req, phase_seconds=3)
    assert r["feasible"] is False
    assert r.get("starts") is None


def test_cooling_forces_starts_later(builder, E, C):
    # Independent equipment so cooling is the only reason to wait: after the
    # three t=0 draws (3+3+3 = 9, level 1), a fourth draw of 3 needs at least
    # one time unit (recovery 5 -> level 6). Sum-of-starts then lex order
    # packs remaining draws as early as the ledger allows.
    req = builder(
        [E(chr(65 + i), duration=1, startup=3, equipment=chr(88 + i))
         for i in range(5)],
        shared_cooling=C(capacity=10, initial=10, recovery=5),
    )
    r = solve(req, phase_seconds=5)
    assert r["feasible"] is True
    starts = r["starts"]
    assert starts[:3] == [0, 0, 0]
    assert starts[3] >= 1
    for step in r["cooling_trace"]:
        assert step["level_after"] >= 0
        assert step["level_before"] <= 10
    # Ledger independently replayed from the returned starts must match.
    replay = compute_cooling_trace(
        [e for e in req.exposures], starts, req.shared_cooling)
    assert [s.model_dump() for s in replay] == r["cooling_trace"]


def test_recovery_capped_at_capacity(builder, E, C):
    # One draw at t=0 (4), next exposure forced to start at >= 3; by then
    # recovery would overfill the capacity, so the level is capped at 10.
    exps = [
        E("A", duration=1, startup=4, equipment="X", earliest=0, latest=0),
        E("B", duration=1, startup=3, equipment="Y", earliest=3, latest=50),
        E("C", duration=1, startup=1, equipment="Z"),
        E("D", duration=1, startup=1, equipment="W"),
        E("E", duration=1, startup=1, equipment="V"),
    ]
    req = builder(exps, shared_cooling=C(capacity=10, initial=10, recovery=100))
    r = solve(req, phase_seconds=5)
    assert r["feasible"] is True
    trace = {s["exposure_id"]: s for s in r["cooling_trace"]}
    b = trace["B"]
    assert b["start"] >= 3
    assert b["level_before"] == 10          # capped, not 6 + 300
    assert b["recovery"] == (b["start"] - 0) * 100


def test_recovery_from_time_zero_before_first_start(builder, E, C):
    # Initial 0, recovery 2/unit; A is the only positive draw (4 units) so its
    # earliest feasible start is t=2 (recovery credited from time zero).
    exps = [
        E("A", duration=1, startup=4, equipment="X", earliest=0, latest=50),
        E("B", duration=1, startup=0, equipment="Y"),
        E("C", duration=1, startup=0, equipment="Z"),
        E("D", duration=1, startup=0, equipment="W"),
        E("E", duration=1, startup=0, equipment="V"),
    ]
    req = builder(exps, shared_cooling=C(capacity=10, initial=0, recovery=2))
    r = solve(req, phase_seconds=5)
    assert r["feasible"] is True
    assert r["starts"] == [2, 0, 0, 0, 0]
    a = r["cooling_trace"][-1]
    assert a["exposure_id"] == "A" and a["start"] == 2
    assert a["recovery"] == 4 and a["level_before"] == 4 and a["level_after"] == 0


def test_same_time_draws_get_no_recovery_even_if_not_adjacent_entries(builder, E, C):
    # A is forced late (t=4); B..E all at t=0. At t=0 four draws of 2 (8).
    # A at t=4: +8 recovery -> capped 10, draws 2 -> 8.
    exps = [
        E("A", duration=1, startup=2, equipment="X", earliest=4, latest=50),
        E("B", duration=1, startup=2, equipment="Y"),
        E("C", duration=1, startup=2, equipment="Z"),
        E("D", duration=1, startup=2, equipment="W"),
        E("E", duration=1, startup=2, equipment="V"),
    ]
    req = builder(exps, shared_cooling=C(capacity=10, initial=10, recovery=2))
    r = solve(req, phase_seconds=5)
    assert r["feasible"] is True
    trace = r["cooling_trace"]
    assert [s["exposure_id"] for s in trace] == ["B", "C", "D", "E", "A"]
    b0 = [s for s in trace if s["start"] == 0]
    assert [s["recovery"] for s in b0] == [0, 0, 0, 0]
    a = trace[-1]
    assert a["exposure_id"] == "A" and a["start"] == 4
    assert a["recovery"] == 8 and a["level_before"] == 10 and a["level_after"] == 8


def test_infeasible_when_cooling_cannot_cover_window(builder, E, C):
    # Two draws of 6 at t=0 with initial 10 and zero recovery never fit, even
    # though their equipment is distinct.
    exps = [
        E("A", duration=1, startup=6, equipment="X", latest=4),
        E("B", duration=1, startup=6, equipment="Y", latest=4),
        E("C", duration=1, startup=0, equipment="Z"),
        E("D", duration=1, startup=0, equipment="W"),
        E("E", duration=1, startup=0, equipment="V"),
    ]
    req = builder(exps, shared_cooling=C(capacity=10, initial=10, recovery=0))
    r = solve(req, phase_seconds=3)
    assert r["feasible"] is False
    assert "starts" not in r


def test_initial_above_capacity_is_input_error(builder, E, C):
    exps = [E(chr(65 + i), duration=1, startup=1, equipment=chr(88 + i))
            for i in range(5)]
    req = builder(exps, shared_cooling=C(capacity=5, initial=6, recovery=1))
    with pytest.raises(InputValidationError) as ei:
        solve(req, phase_seconds=2)
    assert any("initial" in m and "capacity" in m for m in ei.value.errors)


def test_partial_draw_larger_than_capacity_is_infeasible_not_input_error(builder, E, C):
    exps = [
        E("A", duration=1, startup=11, equipment="X"),
        E("B", duration=1, startup=0, equipment="Y"),
        E("C", duration=1, startup=0, equipment="Z"),
        E("D", duration=1, startup=0, equipment="W"),
        E("E", duration=1, startup=0, equipment="V"),
    ]
    req = builder(exps, shared_cooling=C(capacity=10, initial=10, recovery=10))
    r = solve(req, phase_seconds=3)
    # Level can never exceed capacity 10, so a draw of 11 is never affordable.
    assert r["feasible"] is False


def test_obstruction_points_at_first_exhausting_draw(builder, E, C):
    # A(6) fits at t=0 (level 4); B(6) can never be afforded within window 4
    # with zero recovery — B is the step that exhausts the cooler.
    exps = [
        E("A", duration=1, startup=6, equipment="X", latest=4),
        E("B", duration=1, startup=6, equipment="Y", latest=4),
        E("C", duration=1, startup=0, equipment="Z"),
        E("D", duration=1, startup=0, equipment="W"),
        E("E", duration=1, startup=0, equipment="V"),
    ]
    req = builder(exps, shared_cooling=C(capacity=10, initial=10, recovery=0))
    r = solve(req, phase_seconds=3)
    assert r["feasible"] is False
    ob = r["cooling_obstruction"]
    assert ob == {"exposure_id": "B", "entry_index": 1, "feasible_count": 1}


def test_no_obstruction_when_cooling_alone_is_feasible(builder, E, C):
    # Equipment infeasibility (two long exposures on X within a tight window),
    # but the cooler budget itself is ample: no cooling culprit is claimed.
    exps = [
        E("A", duration=5, startup=0, equipment="X", latest=4),
        E("B", duration=5, startup=0, equipment="X", latest=4),
        E("C", duration=1, startup=0, equipment="Y"),
        E("D", duration=1, startup=0, equipment="Z"),
        E("E", duration=1, startup=0, equipment="W"),
    ]
    req = builder(exps, shared_cooling=C(capacity=10, initial=10, recovery=5))
    r = solve(req, phase_seconds=3)
    assert r["feasible"] is False
    assert r.get("cooling_obstruction") is None


def test_no_obstruction_when_disabled(builder, E):
    req = builder([
        E("A", duration=5, latest=4), E("B", duration=5, latest=4),
        E("C"), E("D", equipment="Y"), E("E", equipment="Z"),
    ])
    r = solve(req, phase_seconds=2)
    assert r["feasible"] is False
    assert "cooling_obstruction" not in r


def test_cooling_participates_in_lex_tie_break(builder, E, C):
    # All five are on distinct equipment with duration 1, so the cooler alone
    # shapes the schedule. At t=0 four draws fit (5+1+1+1 = 8); one draw of 5
    # must wait one time unit. Sum-minimum is 1; phase 3 then pushes the wait
    # onto the latest entry possible, keeping earlier entries at 0.
    exps = [
        E("A", duration=1, startup=5, equipment="X"),
        E("B", duration=1, startup=5, equipment="Y"),
        E("C", duration=1, startup=1, equipment="Z"),
        E("D", duration=1, startup=1, equipment="W"),
        E("E", duration=1, startup=1, equipment="V"),
    ]
    req = builder(exps, shared_cooling=C(capacity=10, initial=10, recovery=5))
    r = solve(req, phase_seconds=5)
    assert r["feasible"] is True
    assert r["starts"] == [0, 1, 0, 0, 0]
    ledger = {(s["start"], s["exposure_id"]): s for s in r["cooling_trace"]}
    assert ledger[(0, "A")]["level_after"] == 5
    assert ledger[(0, "E")]["level_after"] == 2
    b = ledger[(1, "B")]
    assert b["recovery"] == 5 and b["level_before"] == 7 and b["level_after"] == 2
