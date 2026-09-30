"""CP-SAT scheduler for synchrotron beamline exposures.

Optimization order (lexicographic):
  1. minimum final finish time (makespan)
  2. minimum sum of all start times
  3. lexicographically smallest start-time vector in entry order

Each exposure occupies its equipment from ``start`` to ``start + duration +
cooling``; intervals on the same equipment may not overlap, which enforces both
exclusive equipment use and the post-exposure cooling window.

When shared cooling is enabled, every exposure start is also an instantaneous
draw on one common cryocooler budget. The level starts at ``initial`` at time
zero, recovers ``recovery`` units per elapsed integer time unit (capped at
``capacity``), and draws at the same time tick happen back-to-back in entry
order with no recovery between them. Every post-draw level must be
non-negative; these constraints are part of the CP-SAT model in every
optimization phase, never a post-hoc filter.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

from .schemas import (
    CoolingStep,
    EquipmentOrder,
    Exposure,
    Link,
    ScheduleRequest,
    SharedCooling,
    SlackInfo,
)


class InputValidationError(ValueError):
    """Semantic input errors (HTTP 400), distinct from infeasibility."""

    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_request(req: ScheduleRequest) -> None:
    errors: List[str] = []
    ids = [e.id for e in req.exposures]
    id_set = set(ids)
    if len(id_set) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        errors.append(f"duplicate exposure id: {', '.join(dupes)}")

    for idx, e in enumerate(req.exposures):
        if e.earliest_start > e.latest_start:
            errors.append(
                f"exposure {e.id}: earliest_start ({e.earliest_start}) > "
                f"latest_start ({e.latest_start})"
            )
        if e.latest_start + e.duration > req.horizon:
            errors.append(
                f"exposure {e.id}: latest possible finish "
                f"{e.latest_start + e.duration} exceeds horizon {req.horizon}"
            )

    pair_seen = set()
    for ln in req.links:
        if ln.from_id not in id_set:
            errors.append(f"link references unknown exposure '{ln.from_id}'")
        if ln.to_id not in id_set:
            errors.append(f"link references unknown exposure '{ln.to_id}'")
        if ln.from_id == ln.to_id:
            errors.append(f"link {ln.from_id}->{ln.to_id} references the same exposure")
        if ln.max_gap is not None and ln.max_gap < ln.min_gap:
            errors.append(
                f"link {ln.from_id}->{ln.to_id}: max_gap ({ln.max_gap}) < "
                f"min_gap ({ln.min_gap})"
            )
        key = (ln.from_id, ln.to_id)
        if key in pair_seen:
            errors.append(f"duplicate link {ln.from_id}->{ln.to_id}")
        pair_seen.add(key)

    sc = req.shared_cooling
    if sc is not None and sc.initial > sc.capacity:
        errors.append(
            f"shared_cooling: initial ({sc.initial}) exceeds capacity ({sc.capacity})"
        )

    if errors:
        raise InputValidationError(errors)


def _build_model(req: ScheduleRequest) -> Tuple[cp_model.CpModel, Dict[str, Any]]:
    model = cp_model.CpModel()
    exps: List[Exposure] = req.exposures
    n = len(exps)
    H = req.horizon

    starts: List[cp_model.IntVar] = []
    ends: List[cp_model.IntVar] = []
    occupy_until: List[cp_model.IntVar] = []
    intervals_by_eq: Dict[str, List[cp_model.IntervalVar]] = defaultdict(list)

    for e in exps:
        s = model.new_int_var(e.earliest_start, e.latest_start, f"start_{e.id}")
        finish = s + e.duration
        release = finish + e.cooling
        # Equipment stays occupied through the cooling window.
        iv = model.new_interval_var(s, e.duration + e.cooling, release, f"occ_{e.id}")
        intervals_by_eq[e.equipment].append(iv)
        starts.append(s)
        ends.append(finish)
        occupy_until.append(release)

    for eq, ivs in intervals_by_eq.items():
        if len(ivs) > 1:
            model.add_no_overlap(ivs)

    by_id = {e.id: i for i, e in enumerate(exps)}
    for ln in req.links:
        a, b = by_id[ln.from_id], by_id[ln.to_id]
        gap = starts[b] - ends[a]
        model.add(gap >= ln.min_gap)
        if ln.max_gap is not None:
            model.add(gap <= ln.max_gap)

    makespan = model.new_int_var(0, H, "makespan")
    model.add_max_equality(makespan, ends)
    sum_starts = model.new_int_var(0, H * n, "sum_starts")
    model.add(sum_starts == sum(starts))

    cooling_vars: Dict[str, Any] = {}
    if req.shared_cooling is not None:
        cooling_vars = _add_shared_cooling(model, exps, starts, req.shared_cooling, H)

    return model, {
        "starts": starts,
        "ends": ends,
        "occupy_until": occupy_until,
        "makespan": makespan,
        "sum_starts": sum_starts,
        "cooling": cooling_vars,
    }


def _add_shared_cooling(
    model: cp_model.CpModel,
    exps: List[Exposure],
    starts: List[cp_model.IntVar],
    sc: SharedCooling,
    H: int,
) -> Dict[str, Any]:
    """Model the shared cryocooler budget inside the CP-SAT model.

    All start events are jointly ordered into a global startup sequence:
    earlier start time first, and equal start times keep exposure *entry*
    order. Recovery is credited by elapsed time between consecutive distinct
    start times (``recovery`` per integer time unit, capped at ``capacity``);
    same-time draws are deducted back-to-back in that sequence with no
    recovery between them, and every post-draw level must be non-negative.

    Since the rank of each exposure is itself a model variable derived from
    the start variables, the budget participates in feasibility and in all
    three lexicographic optimization phases — it can force starts later, but
    solutions are never filtered after solving.
    """
    n = len(exps)

    # Total ordering key: n * start + entry index. Equal starts break ties by
    # entry order; distinct times dominate the index term.
    keys = [model.new_int_var(0, n * H + n, f"cool_key_{i}") for i in range(n)]
    for i in range(n):
        model.add(keys[i] == n * starts[i] + i)

    # rank[i] = position of exposure i in the global startup sequence.
    ranks = [model.new_int_var(0, n - 1, f"cool_rank_{i}") for i in range(n)]
    model.add_all_different(ranks)
    for i in range(n):
        earlier = []
        for j in range(n):
            if i == j:
                continue
            before = model.new_bool_var(f"cool_before_{j}_{i}")
            model.add(keys[j] < keys[i]).only_enforce_if(before)
            model.add(keys[j] > keys[i]).only_enforce_if(before.negated())
            earlier.append(before)
        model.add(ranks[i] == sum(earlier))

    # q[k] is the exposure index at sequence position k; q = inverse(rank).
    q = [model.new_int_var(0, n - 1, f"cool_q_{k}") for k in range(n)]
    model.add_inverse(ranks, q)

    # Chronological start times, consumptions and ledger variables.
    seq_start = [model.new_int_var(0, H, f"cool_seqstart_{k}") for k in range(n)]
    seq_cons = [model.new_int_var(0, sc.capacity, f"cool_seqcons_{k}") for k in range(n)]
    consumptions = [model.new_constant(e.startup_consumption) for e in exps]
    for k in range(n):
        model.add_element(q[k], starts, seq_start[k])
        model.add_element(q[k], consumptions, seq_cons[k])

    rec_bound = H * max(sc.recovery, 1)
    recoveries: List[cp_model.IntVar] = []
    levels_before: List[cp_model.IntVar] = []
    levels_after: List[cp_model.IntVar] = []
    same_flags: List[cp_model.IntVar] = []

    prev_after: Optional[cp_model.IntVar] = None
    for k in range(n):
        if k == 0:
            # Recovery from time zero to the first startup event.
            full_rec = model.new_int_var(0, rec_bound, f"cool_fullrec_{k}")
            model.add(full_rec == seq_start[k] * sc.recovery)
            same = model.new_constant(0)
            rec = full_rec
        else:
            dt = model.new_int_var(0, H, f"cool_dt_{k}")
            model.add(dt == seq_start[k] - seq_start[k - 1])
            same = model.new_bool_var(f"cool_same_{k}")
            model.add(dt == 0).only_enforce_if(same)
            model.add(dt > 0).only_enforce_if(same.negated())
            full_rec = model.new_int_var(0, rec_bound, f"cool_fullrec_{k}")
            model.add(full_rec == dt * sc.recovery)
            rec = model.new_int_var(0, rec_bound, f"cool_rec_{k}")
            model.add(rec == 0).only_enforce_if(same)
            model.add(rec == full_rec).only_enforce_if(same.negated())
        same_flags.append(same)
        recoveries.append(rec)

        if prev_after is None:
            before = model.new_int_var(0, sc.capacity, f"cool_before_{k}")
            model.add_min_equality(before, [sc.initial + rec, sc.capacity])
        else:
            before = model.new_int_var(0, sc.capacity, f"cool_before_{k}")
            model.add_min_equality(before, [prev_after + rec, sc.capacity])
        levels_before.append(before)

        after = model.new_int_var(0, sc.capacity, f"cool_after_{k}")
        model.add(after == before - seq_cons[k])
        levels_after.append(after)
        prev_after = after

    return {
        "ranks": ranks,
        "q": q,
        "seq_start": seq_start,
        "seq_cons": seq_cons,
        "same": same_flags,
        "recoveries": recoveries,
        "levels_before": levels_before,
        "levels_after": levels_after,
    }


class SolverTimeoutError(RuntimeError):
    """The solver could not decide within the time limit (status UNKNOWN)."""


def _cooling_only_request(req: ScheduleRequest,
                          prefix: Optional[int] = None) -> Any:
    """Build a cooling-only relaxation, bypassing the 5-10 exposure schema.

    Every exposure gets its own equipment and links are dropped, so the only
    remaining constraints are start windows plus the shared-cooling budget.
    ``prefix`` keeps just the first ``prefix`` exposures (entry order);
    internal sub-problems may therefore contain fewer than five exposures.
    """
    from types import SimpleNamespace

    exps = list(req.exposures[:prefix] if prefix is not None else req.exposures)
    relaxed = []
    for i, e in enumerate(exps):
        data = e.model_dump()
        data["equipment"] = f"__cool_only_{i}"
        relaxed.append(Exposure(**data))
    return SimpleNamespace(
        horizon=req.horizon,
        exposures=relaxed,
        links=[],
        shared_cooling=req.shared_cooling,
    )


def _diagnose_cooling_exhaustion(
    req: ScheduleRequest, phase_seconds: float
) -> Optional[Dict[str, Any]]:
    """If the shared-cooling budget alone makes the plan impossible, identify
    the first exposure (in entry order) whose inclusion makes the
    cooling-only problem infeasible.

    Returns None when cooling is not the proven obstruction (equipment/links
    may still be) or when the diagnosis itself hits the time limit. This is
    an explanation only — it never returns a partial schedule.
    """
    if req.shared_cooling is None:
        return None

    def status_of(sub: ScheduleRequest) -> Optional[int]:
        model, v = _build_model(sub)
        _solver, status, _name = _solve_phase(model, v, phase_seconds, None)
        # None status would mean UNKNOWN (time limit); INFEASIBLE is reported
        # by _solve_phase even though it hands back no solver object.
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE, cp_model.INFEASIBLE):
            return status
        return None

    full = status_of(_cooling_only_request(req))
    if full != cp_model.INFEASIBLE:
        # UNKNOWN -> cannot claim cooling is the culprit; OPTIMAL/FEASIBLE ->
        # the cooling budget alone is satisfiable, so something else blocks it.
        return None

    n = len(req.exposures)
    lo = 1
    hi = n
    # Smallest entry-order prefix whose cooling-only problem is infeasible.
    # Infeasibility is monotone as exposures are appended.
    while lo < hi:
        mid = (lo + hi) // 2
        st = status_of(_cooling_only_request(req, mid))
        if st is None:
            return None  # diagnosis hit the time limit; do not guess
        if st == cp_model.INFEASIBLE:
            hi = mid
        else:
            lo = mid + 1
    culprit = req.exposures[lo - 1]
    return {
        "exposure_id": culprit.id,
        "entry_index": lo - 1,
        "feasible_count": lo - 1,
    }


def _solve_phase(
    model: cp_model.CpModel,
    vars_: Dict[str, Any],
    phase_seconds: float,
    hint: Optional[List[int]],
) -> Tuple[Optional[cp_model.CpSolver], int, str]:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = phase_seconds
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = 20260929
    if hint is not None:
        for s, v in zip(vars_["starts"], hint):
            model.add_hint(s, v)
    status = solver.solve(model)
    name = solver.status_name(status)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return solver, status, name
    return None, status, name


def solve(req: ScheduleRequest, phase_seconds: float = 10.0) -> Dict[str, Any]:
    """Run the staged lexicographic optimization.

    Raises InputValidationError for bad input. Returns a result dict with
    feasible=False when no executable timing exists.
    """
    validate_request(req)
    exps: List[Exposure] = req.exposures
    links: List[Link] = req.links
    n = len(exps)

    t0 = time.perf_counter()

    # Phase 1: minimize makespan.
    model, v = _build_model(req)
    model.minimize(v["makespan"])
    solver, status, status_name = _solve_phase(model, v, phase_seconds, None)
    if solver is None:
        if status == cp_model.INFEASIBLE:
            result = {"feasible": False, "reason": "infeasible", "status": status_name}
            obstruction = _diagnose_cooling_exhaustion(req, phase_seconds)
            if obstruction is not None:
                result["cooling_obstruction"] = obstruction
            return result
        # UNKNOWN: time limit hit without proving infeasibility — not a
        # definitive "no schedule", surface as an error instead.
        raise SolverTimeoutError(f"phase makespan: solver status {status_name}")
    best_makespan = solver.value(v["makespan"])
    hint = [solver.value(s) for s in v["starts"]]

    # Phase 2: minimize sum of starts, makespan pinned.
    model, v = _build_model(req)
    model.add(v["makespan"] == best_makespan)
    model.minimize(v["sum_starts"])
    solver, _status, status_name = _solve_phase(model, v, phase_seconds, hint)
    if solver is None:
        # The hint from phase 1 already satisfies this pin, so INFEASIBLE
        # cannot occur; UNKNOWN would mean the time limit was hit.
        raise SolverTimeoutError(f"phase sum_starts: solver status {status_name}")
    best_sum = solver.value(v["sum_starts"])
    hint = [solver.value(s) for s in v["starts"]]

    # Phase 3: lex-minimize the start vector in entry order, one var per stage.
    starts_values = list(hint)
    for i in range(n):
        model, v = _build_model(req)
        model.add(v["makespan"] == best_makespan)
        model.add(v["sum_starts"] == best_sum)
        for j in range(i):
            model.add(v["starts"][j] == starts_values[j])
        model.minimize(v["starts"][i])
        solver, _status, status_name = _solve_phase(model, v, phase_seconds, hint)
        if solver is None:
            raise SolverTimeoutError(f"phase lex[{i}]: solver status {status_name}")
        starts_values = [solver.value(s) for s in v["starts"]]
        hint = starts_values

    starts = starts_values
    finishes = [starts[i] + exps[i].duration for i in range(n)]

    # Per-link margins.
    by_id = {e.id: i for i, e in enumerate(exps)}
    slacks: List[SlackInfo] = []
    for ln in links:
        a, b = by_id[ln.from_id], by_id[ln.to_id]
        gap = starts[b] - finishes[a]
        slacks.append(
            SlackInfo(
                from_id=ln.from_id,
                to_id=ln.to_id,
                min_gap=ln.min_gap,
                max_gap=ln.max_gap,
                actual_gap=gap,
                slack=(None if ln.max_gap is None else ln.max_gap - gap),
            )
        )

    # Equipment execution orders.
    grouped: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
    for i, e in enumerate(exps):
        grouped[e.equipment].append((starts[i], e.id))
    equipment_orders = [
        EquipmentOrder(equipment=eq, sequence=[iid for _, iid in sorted(items)])
        for eq, items in sorted(grouped.items())
    ]

    result = {
        "feasible": True,
        "starts": starts,
        "finishes": finishes,
        "makespan": max(finishes),
        "sum_starts": sum(starts),
        "slacks": [s.model_dump() for s in slacks],
        "equipment_orders": [o.model_dump() for o in equipment_orders],
        "solver_time_ms": int((time.perf_counter() - t0) * 1000),
    }
    if req.shared_cooling is not None:
        trace = compute_cooling_trace(exps, starts, req.shared_cooling)
        result["cooling_trace"] = [s.model_dump() for s in trace]
    return result


def compute_cooling_trace(
    exps: List[Exposure],
    starts: List[int],
    sc: SharedCooling,
) -> List[CoolingStep]:
    """Replay the shared-cooler ledger for a solved start vector.

    Startup events are ordered by (start time, entry index): recovery is
    credited from time zero to the first event and between consecutive
    distinct start times, while same-time draws happen back-to-back with no
    recovery. Raises ValueError if a draw would make the level negative
    (which would mean the solver returned a constraint-violating plan).
    """
    order = sorted(range(len(exps)), key=lambda i: (starts[i], i))
    steps: List[CoolingStep] = []
    level: Optional[int] = None
    prev_t: Optional[int] = None
    for i in order:
        t = starts[i]
        if level is None:
            rec = t * sc.recovery
            level = min(sc.initial + rec, sc.capacity)
        else:
            if t == prev_t:
                rec = 0
            else:
                rec = (t - prev_t) * sc.recovery
            level = min(level + rec, sc.capacity)
        before = level
        consumption = exps[i].startup_consumption
        after = before - consumption
        if after < 0:
            raise ValueError(
                f"shared cooling exhausted at exposure {exps[i].id} (t={t}): "
                f"level {before} < consumption {consumption}"
            )
        steps.append(
            CoolingStep(
                exposure_id=exps[i].id,
                start=t,
                recovery=rec,
                level_before=before,
                consumption=consumption,
                level_after=after,
            )
        )
        level = after
        prev_t = t
    return steps
