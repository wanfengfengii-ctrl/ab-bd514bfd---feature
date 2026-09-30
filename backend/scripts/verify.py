#!/usr/bin/env python3
"""One-shot verification for the beamline scheduler stack.

Aggregated exit code is a bitmask (0 = everything passed):
    bit 0 (1)   code tests (pytest)
    bit 1 (2)   build / static integrity checks
    bit 2 (4)   feasible schedule produced by the solver
    bit 3 (8)   infeasible-input API smoke (200 + feasible=false, no partial)
    bit 4 (16)  shared-cooling API smoke (cooling forces later starts;
                insufficient cooling yields 200 feasible=false with a null
                trace, and initial>capacity is a 400 input error)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback

import httpx

API_URL = os.environ.get("API_URL", "http://api:8000").rstrip("/")
WEB_URL = os.environ.get("WEB_URL", "http://web").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

results: dict[str, bool] = {}


def section(name):
    def deco(fn):
        def wrapped():
            print(f"\n=== {name} ===", flush=True)
            try:
                fn()
                results[name] = True
                print(f"[PASS] {name}", flush=True)
            except Exception as exc:  # noqa: BLE001
                results[name] = False
                print(f"[FAIL] {name}: {exc}", flush=True)
                traceback.print_exc()
        return wrapped
    return deco


@section("code tests (pytest)")
def check_pytest():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    print(proc.stdout[-2000:])
    print(proc.stderr[-1000:])
    if proc.returncode != 0:
        raise RuntimeError(f"pytest exited {proc.returncode}")


@section("build / static integrity")
def check_build():
    proc = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "app", "scripts"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or "compileall failed")
    import app.main  # full import graph incl. ortools must load

    static_dir = os.environ.get("WEB_STATIC_DIR")
    if static_dir:
        for name in ("index.html", "app.js", "styles.css"):
            path = os.path.join(static_dir, name)
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                raise RuntimeError(f"missing or empty static asset: {path}")


def _five(exposures):
    return {
        "horizon": 1000,
        "exposures": [
            {
                "id": eid, "duration": dur, "earliest_start": es,
                "latest_start": ls, "equipment": eq, "cooling": cool,
            }
            for (eid, dur, es, ls, eq, cool) in exposures
        ],
        "links": [],
    }


def _cooling_five(consumptions, sc, latest=50):
    """Five independent-equipment exposures with startup draws."""
    return {
        "horizon": 1000,
        "exposures": [
            {
                "id": chr(65 + i), "duration": 1, "earliest_start": 0,
                "latest_start": latest, "equipment": chr(88 + i), "cooling": 0,
                "startup_consumption": consumptions[i],
            }
            for i in range(5)
        ],
        "links": [],
        "shared_cooling": sc,
    }


@section("feasible schedule")
def check_feasible():
    from app.scheduler import solve
    from app.schemas import ScheduleRequest

    body = _five([
        ("A", 4, 0, 50, "X", 2),
        ("B", 3, 0, 50, "X", 1),
        ("C", 5, 0, 50, "Y", 0),
        ("D", 2, 0, 50, "Y", 3),
        ("E", 6, 2, 40, "Z", 0),
    ])
    body["links"] = [
        {"from_id": "A", "to_id": "B", "min_gap": 0, "max_gap": 20},
    ]
    r = solve(ScheduleRequest(**body), phase_seconds=5)
    if not r.get("feasible"):
        raise RuntimeError(f"expected feasible, got {r}")
    starts, finishes = r["starts"], r["finishes"]
    if len(starts) != 5 or len(finishes) != 5:
        raise RuntimeError("incomplete solution returned")

    # Re-verify every constraint independently of the solver's own claims.
    exps = {e["id"]: e for e in body["exposures"]}
    ids = [e["id"] for e in body["exposures"]]
    for sid, eid in zip(starts, ids):
        e = exps[eid]
        if not (e["earliest_start"] <= sid <= e["latest_start"]):
            raise RuntimeError(f"{eid} start {sid} outside window")
    for eq in {e["equipment"] for e in body["exposures"]}:
        on_eq = sorted(
            (starts[i], starts[i] + exps[eid]["duration"] + exps[eid]["cooling"], eid)
            for i, eid in enumerate(ids) if exps[eid]["equipment"] == eq
        )
        for (s1, occ1, a), (s2, _, b) in zip(on_eq, on_eq[1:]):
            if s2 < occ1:
                raise RuntimeError(f"equipment {eq}: {b} starts {s2} before {a} released {occ1}")
    a, b = ids.index("A"), ids.index("B")
    gap = starts[b] - finishes[a]
    if not (0 <= gap <= 20):
        raise RuntimeError(f"link gap {gap} outside [0,20]")
    print(f"  starts={starts} makespan={r['makespan']} sum={r['sum_starts']}")


@section("infeasible API smoke")
def check_api_infeasible():
    with httpx.Client(base_url=API_URL, timeout=30) as cli:
        h = cli.get("/health")
        h.raise_for_status()
        if h.json().get("status") != "ok":
            raise RuntimeError("api /health not ok")

        # Valid input, impossible timing: two long exposures on one equipment,
        # both forced to start within the first 4 time units.
        body = _five([
            ("A", 5, 0, 4, "X", 0),
            ("B", 5, 0, 4, "X", 0),
            ("C", 1, 0, 50, "Y", 0),
            ("D", 1, 0, 50, "Y", 0),
            ("E", 1, 0, 50, "Z", 0),
        ])
        r = cli.post("/api/schedule", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"expected HTTP 200 for infeasible, got {r.status_code}")
        data = r.json()
        if data.get("feasible") is not False or data.get("reason") != "no_schedule":
            raise RuntimeError(f"unexpected infeasible payload: {data}")
        if data.get("starts") is not None or data.get("finishes") is not None:
            raise RuntimeError("partial/stale solution returned for infeasible case")

        # Contrast: malformed input must be a 400 input_error.
        bad = _five([
            ("A", 1, 40, 10, "X", 0),
            ("B", 1, 0, 50, "X", 0),
            ("C", 1, 0, 50, "Y", 0),
            ("D", 1, 0, 50, "Y", 0),
            ("E", 1, 0, 50, "Z", 0),
        ])
        r2 = cli.post("/api/schedule", json=bad)
        if r2.status_code != 400 or r2.json().get("reason") != "input_error":
            raise RuntimeError(f"expected 400 input_error, got {r2.status_code} {r2.text[:200]}")

    # Web tier health (proxied page server up).
    try:
        with httpx.Client(base_url=WEB_URL, timeout=10) as cli:
            wh = cli.get("/health")
            wh.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"web health failed: {exc}") from exc


def _replay_cooling(body, data):
    """Independently replay the cooler ledger from starts, matching the spec."""
    sc = body["shared_cooling"]
    exps = {e["id"]: e for e in body["exposures"]}
    order = sorted(
        range(len(body["exposures"])),
        key=lambda i: (data["starts"][i], i),
    )
    level, prev_t, steps = None, None, []
    for i in order:
        t = data["starts"][i]
        rec = t * sc["recovery"] if level is None else (
            0 if t == prev_t else (t - prev_t) * sc["recovery"])
        level = min(sc["capacity"], (sc["initial"] if level is None else level) + rec)
        before = level
        after = before - exps[body["exposures"][i]["id"]]["startup_consumption"]
        if after < 0:
            raise RuntimeError(f"cooling went negative at {body['exposures'][i]['id']}")
        steps.append((body["exposures"][i]["id"], t, rec, before,
                      exps[body["exposures"][i]["id"]]["startup_consumption"], after))
        level, prev_t = after, t
    return steps


@section("shared-cooling API smoke")
def check_shared_cooling():
    sc = {"capacity": 10, "initial": 10, "recovery": 5}
    draws = [3, 3, 3, 3, 3]

    with httpx.Client(base_url=API_URL, timeout=60) as cli:
        # Baseline: without shared cooling every independent exposure starts 0.
        plain = _cooling_five(draws, sc)
        plain.pop("shared_cooling")
        rb = cli.post("/api/schedule", json=plain)
        rb.raise_for_status()
        base_starts = rb.json()["starts"]
        if any(s != 0 for s in base_starts):
            raise RuntimeError(f"baseline starts unexpectedly delayed: {base_starts}")

        # Enabled: the cooler must force later starts (in-model, not filtered).
        body = _cooling_five(draws, sc)
        r = cli.post("/api/schedule", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"cooling feasible case HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        if not data.get("feasible"):
            raise RuntimeError(f"expected feasible with cooling, got {data}")
        starts = data["starts"]
        if not any(s > 0 for s in starts):
            raise RuntimeError("cooling did not force any later start")
        if starts[:3] != [0, 0, 0]:
            raise RuntimeError(f"first three 3-unit draws should fit at t=0: {starts}")
        trace = data.get("cooling_trace")
        if not trace or len(trace) != 5:
            raise RuntimeError(f"missing/incomplete cooling_trace: {trace}")
        replay = _replay_cooling(body, data)
        got = [(s["exposure_id"], s["start"], s["recovery"],
                s["level_before"], s["consumption"], s["level_after"]) for s in trace]
        if got != replay:
            raise RuntimeError(f"cooling trace mismatch:\n api={got}\n replay={replay}")
        if any(s["level_after"] < 0 for s in trace):
            raise RuntimeError("negative post-draw level returned")
        print(f"  starts={starts} trace={len(trace)} steps, ledger independently verified")

        # Insufficient cooling, tight windows: 200 feasible=false, no partial.
        bad_body = _cooling_five(
            [6, 6, 0, 0, 0],
            {"capacity": 10, "initial": 10, "recovery": 0}, latest=4)
        r2 = cli.post("/api/schedule", json=bad_body)
        if r2.status_code != 200:
            raise RuntimeError(f"expected 200 for cooling-infeasible, got {r2.status_code}")
        d2 = r2.json()
        if d2.get("feasible") is not False or d2.get("reason") != "no_schedule":
            raise RuntimeError(f"unexpected cooling-infeasible payload: {d2}")
        if any(d2.get(k) is not None for k in
               ("starts", "finishes", "makespan", "cooling_trace")):
            raise RuntimeError("partial schedule/trace returned on cooling failure")
        ob = d2.get("cooling_obstruction")
        if not ob or ob.get("exposure_id") != "B" or ob.get("entry_index") != 1:
            raise RuntimeError(f"expected exhaustion at B as obstruction, got {ob}")
        print(f"  insufficient cooling correctly infeasible; exhaustion at {ob}")

        # Initial level above capacity is an input error (400), never no_schedule.
        bad_input = _cooling_five(
            [1, 1, 1, 1, 1], {"capacity": 5, "initial": 6, "recovery": 1})
        r3 = cli.post("/api/schedule", json=bad_input)
        if r3.status_code != 400 or r3.json().get("reason") != "input_error":
            raise RuntimeError(
                f"expected 400 input_error for initial>capacity, got "
                f"{r3.status_code} {r3.text[:200]}")


def main() -> int:
    check_pytest()
    check_build()
    check_feasible()
    check_api_infeasible()
    check_shared_cooling()

    bit = {"code tests (pytest)": 1, "build / static integrity": 2,
           "feasible schedule": 4, "infeasible API smoke": 8,
           "shared-cooling API smoke": 16}
    code = 0
    print("\n=== summary ===")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            code |= bit[name]
    print(f"\nexit code: {code} (bitmask 1=tests 2=build 4=feasible "
          f"8=api-smoke 16=cooling-smoke)")
    return code


if __name__ == "__main__":
    sys.exit(main())
