"""Minimum-cost refuelling along a fixed route (the classic "gas station problem").

Given stations at known mile markers with known prices, a tank that holds
``tank_gallons`` and a consumption of ``mpg``, the greedy rule below is
provably optimal when fuel can be bought in any quantity:

  At each station, look ahead one full tank of range:
    * If a CHEAPER station is in range, buy only enough to reach the first
      such station (maybe nothing).
    * Otherwise, if the destination is in range, buy just enough to arrive.
    * Otherwise fill the tank here (this is the cheapest fuel in range) and
      drive to the cheapest station within range.

It runs in O(n) amortised per step over a few hundred candidate stations,
i.e. well under a millisecond. Optimality is checked against an exhaustive
dynamic program in the test suite.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from planner.exceptions import InfeasibleTripError

EPS = 1e-9


@dataclass
class Candidate:
    mile: float
    price: float
    payload: Any = None


@dataclass
class Purchase:
    candidate: Candidate
    gallons: float
    fuel_on_arrival: float

    @property
    def cost(self) -> float:
        return self.gallons * self.candidate.price


@dataclass
class FuelPlan:
    purchases: list[Purchase] = field(default_factory=list)
    total_miles: float = 0.0
    start_gallons: float = 0.0
    end_gallons: float = 0.0

    @property
    def total_cost(self) -> float:
        return sum(p.cost for p in self.purchases)

    @property
    def total_gallons(self) -> float:
        return sum(p.gallons for p in self.purchases)


def plan_fuel_stops(
    candidates: Sequence[Candidate],
    total_miles: float,
    tank_gallons: float,
    mpg: float,
    start_gallons: float,
) -> FuelPlan:
    stations = sorted((c for c in candidates if -EPS <= c.mile <= total_miles + EPS), key=lambda c: c.mile)
    range_miles = tank_gallons * mpg
    plan = FuelPlan(total_miles=total_miles, start_gallons=start_gallons)

    pos, fuel = 0.0, start_gallons
    cur: int | None = None  # None = at the origin (nothing to buy there)
    nxt = 0                 # first station index strictly after `cur`

    def gap_error(at_mile: float) -> InfeasibleTripError:
        ahead = next((s.mile for s in stations if s.mile > at_mile + EPS), total_miles)
        return InfeasibleTripError(
            f"No fuel station within range after mile {at_mile:.0f} "
            f"(next option at mile {ahead:.0f}, range is {range_miles:.0f} miles).",
            stranded_after_mile=round(at_mile, 1),
            next_option_mile=round(ahead, 1),
        )

    while True:
        if pos + fuel * mpg >= total_miles - EPS:
            plan.end_gallons = fuel - (total_miles - pos) / mpg
            return plan

        if cur is None:
            # Leaving the origin: drive to the first station (all are "cheaper"
            # than the origin, where nothing can be bought).
            if nxt >= len(stations) or stations[nxt].mile > pos + fuel * mpg + EPS:
                raise gap_error(pos)
            target = nxt
        else:
            here = stations[cur]
            arrival = fuel
            limit = pos + range_miles + EPS
            window_end = nxt
            cheaper = None
            while window_end < len(stations) and stations[window_end].mile <= limit:
                if cheaper is None and stations[window_end].price < here.price - EPS:
                    cheaper = window_end
                window_end += 1

            if cheaper is not None:
                need = (stations[cheaper].mile - pos) / mpg - fuel
                target = cheaper
            elif total_miles - pos <= range_miles + EPS:
                need = (total_miles - pos) / mpg - fuel
                target = None
            else:
                if window_end == nxt:
                    raise gap_error(pos)
                need = tank_gallons - fuel
                window = range(nxt, window_end)
                # cheapest in range; ties -> farthest (fewer stops)
                target = min(window, key=lambda k: (stations[k].price, -stations[k].mile))

            if need > EPS:
                fuel += need
                plan.purchases.append(Purchase(here, need, arrival))

            if target is None:
                plan.end_gallons = fuel - (total_miles - pos) / mpg
                return plan

        fuel -= (stations[target].mile - pos) / mpg
        pos = stations[target].mile
        cur, nxt = target, target + 1
        fuel = max(fuel, 0.0)


# ---------------------------------------------------------------------------
# Exact optimiser with a per-stop penalty
# ---------------------------------------------------------------------------
#
# The greedy rule above minimises dollars only, so it will happily stop to top
# up 0.1 gal at a station a cent cheaper. Real drivers trade a few cents for
# fewer stops, so the planner minimises   fuel cost + stop_penalty * stops.
#
# Structural lemma (Khuller, Malekian & Mestre, "To fill or not to fill: the
# gas station problem", ESA 2007): for any fixed set of stops there is an
# optimal plan where, at every stop, you either FILL THE TANK or buy JUST
# ENOUGH to reach the next stop empty. So fuel on arrival at a stop is one of:
#   * 0                               (previous stop bought "just enough")
#   * tank - dist(prev, here)         (previous stop filled up)
#   * start_fuel - dist(origin, here) (first stop)
# That gives O(n * w) states and O(n * w^2) transitions, w = stations within
# one tank (~20 after bucketing), i.e. a few milliseconds per trip.
# With stop_penalty = 0 it returns exactly the greedy optimum (verified in tests).


def plan_fuel_stops_dp(
    candidates: Sequence[Candidate],
    total_miles: float,
    tank_gallons: float,
    mpg: float,
    start_gallons: float,
    stop_penalty: float = 0.0,
) -> FuelPlan:
    st = sorted((c for c in candidates if -EPS <= c.mile <= total_miles + EPS), key=lambda c: c.mile)
    n = len(st)
    plan = FuelPlan(total_miles=total_miles, start_gallons=start_gallons)
    need_total = total_miles / mpg
    if start_gallons >= need_total - EPS:
        plan.end_gallons = start_gallons - need_total
        return plan

    miles = [c.mile for c in st]
    INF = float("inf")
    # best[j][key] = (objective, back_pointer); key: ("o",) | ("z",) | ("f", i)
    best: list[dict] = [dict() for _ in range(n)]

    def arrival(j: int, key) -> float:
        if key[0] == "o":
            return start_gallons - miles[j] / mpg
        if key[0] == "z":
            return 0.0
        return tank_gallons - (miles[j] - miles[key[1]]) / mpg

    def relax(j, key, value, back):
        cur = best[j].get(key)
        if cur is None or value < cur[0] - EPS:
            best[j][key] = (value, back)

    reach0 = start_gallons * mpg
    for j in range(n):
        if miles[j] > reach0 + EPS:
            break
        relax(j, ("o",), stop_penalty, None)

    final_best, final_back = INF, None
    for i in range(n):
        if not best[i]:
            continue
        p = st[i].price
        # Next stops within one tank of i.
        k = i + 1
        nexts = []
        while k < n and miles[k] - miles[i] <= tank_gallons * mpg + EPS:
            nexts.append(k)
            k += 1
        to_dest = (total_miles - miles[i]) / mpg
        for key, (val, _) in list(best[i].items()):
            a = arrival(i, key)
            if a < -1e-6:
                continue
            # finish from here
            if to_dest <= tank_gallons + EPS and a <= to_dest + EPS:
                buy = max(0.0, to_dest - a)
                total = val + buy * p
                if total < final_best - EPS:
                    final_best, final_back = total, (i, key, buy)
            for j in nexts:
                d = (miles[j] - miles[i]) / mpg
                if a <= d + EPS:  # "just enough" (otherwise i would not be a stop)
                    relax(j, ("z",), val + (d - a) * p + stop_penalty, (i, key, d - a))
                relax(j, ("f", i), val + (tank_gallons - a) * p + stop_penalty, (i, key, tank_gallons - a))

    if final_back is None:
        # Reuse the greedy pass purely to produce a precise "where you get stranded" error.
        plan_fuel_stops(candidates, total_miles, tank_gallons, mpg, start_gallons)
        raise InfeasibleTripError("No feasible refuelling plan within the vehicle's range.")

    # Reconstruct.
    chain = []
    i, key, buy = final_back
    while True:
        chain.append((i, key, buy))
        back = best[i][key][1]
        if back is None:
            break
        pi, pkey, pbuy = back
        i, key, buy = pi, pkey, pbuy
    chain.reverse()

    for i, key, buy in chain:
        a = max(arrival(i, key), 0.0)
        if buy > EPS:
            plan.purchases.append(Purchase(st[i], buy, a))
    last_i, last_key, last_buy = chain[-1]
    fuel_end = max(arrival(last_i, last_key), 0.0) + last_buy - (total_miles - miles[last_i]) / mpg
    plan.end_gallons = max(fuel_end, 0.0)
    return plan
