import random
from functools import cache

import pytest

from planner.exceptions import InfeasibleTripError
from planner.services.optimizer import Candidate, plan_fuel_stops, plan_fuel_stops_dp

PLANNERS = [plan_fuel_stops, lambda *a: plan_fuel_stops_dp(*a, stop_penalty=0.0)]


def simulate(plan, stations_total, tank, mpg, start):
    """Drive the plan and assert the tank never goes negative or overflows."""
    fuel, pos = start, 0.0
    for p in plan.purchases:
        fuel -= (p.candidate.mile - pos) / mpg
        assert fuel >= -1e-6, "ran dry before a stop"
        fuel += p.gallons
        assert fuel <= tank + 1e-6, "overfilled the tank"
        pos = p.candidate.mile
    fuel -= (stations_total - pos) / mpg
    assert fuel >= -1e-6, "ran dry before the destination"


def brute_force_integer(stations, total, tank, start):
    """Exact DP over integer fuel levels (mpg=1, integer miles) for verification."""
    pos = [0] + [s.mile for s in stations]
    price = [None] + [s.price for s in stations]

    @cache
    def best(i, fuel):
        if pos[i] + fuel >= total:
            return 0.0
        options = []
        lo = 0 if price[i] is not None else 0
        hi = (tank - fuel) if price[i] is not None else 0
        for buy in range(lo, hi + 1):
            f = fuel + buy
            cost = buy * (price[i] or 0)
            if pos[i] + f >= total:
                options.append(cost)
            for j in range(i + 1, len(pos)):
                d = pos[j] - pos[i]
                if d > f:
                    break
                options.append(cost + best(j, f - d))
        return min(options) if options else float("inf")

    return best(0, start)


@pytest.mark.parametrize("planner", PLANNERS)
def test_full_tank_short_trip_needs_no_stops(planner):
    plan = planner([Candidate(100, 3.0)], 300, 50, 10, 50)
    assert plan.purchases == [] and plan.total_cost == 0


@pytest.mark.parametrize("planner", PLANNERS)
def test_buys_cheap_fuel_and_only_what_is_needed(planner):
    # 900 miles, full tank (500 mi). Cheap station at 450, expensive at 480.
    cands = [Candidate(450, 2.50), Candidate(480, 4.00)]
    plan = planner(cands, 900, 50, 10, 50)
    assert [p.candidate.price for p in plan.purchases] == [2.50]
    assert plan.total_gallons == pytest.approx(85 - 50 + 5)  # arrive at 450 with 5 gal; need 45 to finish
    assert plan.total_cost == pytest.approx(40 * 2.50)


@pytest.mark.parametrize("planner", PLANNERS)
def test_fills_up_when_cheapest_in_range(planner):
    # Cheap at 100, pricey at 550; going 1000 miles from empty-ish start.
    cands = [Candidate(100, 2.0), Candidate(550, 5.0)]
    plan = planner(cands, 1000, 50, 10, 10)
    first = plan.purchases[0]
    assert first.candidate.price == 2.0 and first.gallons == pytest.approx(50)
    simulate(plan, 1000, 50, 10, 10)


@pytest.mark.parametrize("planner", PLANNERS)
def test_gap_larger_than_range_is_reported(planner):
    with pytest.raises(InfeasibleTripError):
        planner([Candidate(100, 3.0), Candidate(700, 3.0)], 1200, 50, 10, 50)


@pytest.mark.parametrize("seed", range(40))
def test_greedy_and_dp_match_exhaustive_search(seed):
    rnd = random.Random(seed)
    total, tank = rnd.randint(20, 45), rnd.randint(6, 12)
    start = rnd.randint(0, tank)
    miles = sorted(rnd.sample(range(0, total), rnd.randint(4, 10)))
    stations = [Candidate(m, round(rnd.uniform(2.5, 4.5), 2)) for m in miles]
    exact = brute_force_integer(stations, total, tank, start)
    if exact == float("inf"):
        with pytest.raises(InfeasibleTripError):
            plan_fuel_stops(stations, total, tank, 1, start)
        return
    for planner in PLANNERS:
        plan = planner(stations, total, tank, 1, start)
        simulate(plan, total, tank, 1, start)
        assert plan.total_cost == pytest.approx(exact, abs=1e-6)


@pytest.mark.parametrize("seed", range(25))
def test_stop_penalty_never_adds_stops_and_stays_feasible(seed):
    rnd = random.Random(1000 + seed)
    total = 3000.0
    stations = sorted(
        (Candidate(rnd.uniform(0, total), round(rnd.uniform(2.6, 4.2), 3)) for _ in range(120)),
        key=lambda c: c.mile,
    )
    try:
        greedy = plan_fuel_stops(stations, total, 50, 10, 50)
    except InfeasibleTripError:
        return
    pen = plan_fuel_stops_dp(stations, total, 50, 10, 50, stop_penalty=10)
    simulate(pen, total, 50, 10, 50)
    assert len(pen.purchases) <= len(greedy.purchases)
    assert pen.total_cost >= greedy.total_cost - 1e-6               # greedy is the dollar optimum
    assert pen.total_cost + 10 * len(pen.purchases) <= greedy.total_cost + 10 * len(greedy.purchases) + 1e-6
