"""Minimum-cost refuelling along a fixed route (the "gas station problem").

Inputs: candidate stations at known mile markers with known prices, a tank of
``tank_gallons``, consumption ``mpg`` and ``start_gallons`` at departure.

Two optimisers:

* :func:`plan_fuel_stops` - greedy, provably the minimum *dollar* cost when
  fuel can be bought in any amount. At each station, look one tank ahead:
    - if a cheaper station is in range, buy just enough to reach the first one;
    - else if the destination is in range, buy just enough to arrive;
    - else fill up here and drive to the cheapest station in range.

* :func:`plan_fuel_stops_dp` - exact optimum of
  ``fuel cost + stop_penalty * number_of_stops``. Greedy happily stops to save a
  cent; drivers do not, so the API answers with this one. With
  ``stop_penalty=0`` both return the same cost (verified by the tests against
  exhaustive search).
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
    end_gallons: float = 0.0

    @property
    def total_cost(self) -> float:
        return sum(p.cost for p in self.purchases)

    @property
    def total_gallons(self) -> float:
        return sum(p.gallons for p in self.purchases)


def _on_route(candidates: Sequence[Candidate], total_miles: float) -> list[Candidate]:
    return sorted((c for c in candidates if -EPS <= c.mile <= total_miles + EPS), key=lambda c: c.mile)


def _gap_error(stations: list[Candidate], at_mile: float, total_miles: float, range_miles: float):
    ahead = next((s.mile for s in stations if s.mile > at_mile + EPS), total_miles)
    return InfeasibleTripError(
        f"No fuel station within range after mile {at_mile:.0f} "
        f"(next option at mile {ahead:.0f}, range is {range_miles:.0f} miles).",
        stranded_after_mile=round(at_mile, 1),
        next_option_mile=round(ahead, 1),
    )


def _first_gap(stations: list[Candidate], total_miles: float, range_miles: float, start_reach: float) -> float:
    """Mile after which the vehicle is stranded, assuming it refuels whenever possible."""
    reach, last = start_reach, 0.0
    for s in stations:
        if s.mile > reach + EPS:
            return last
        last, reach = s.mile, s.mile + range_miles
    return last if reach < total_miles - EPS else -1.0


# ---------------------------------------------------------------------------
# Greedy: minimum dollars
# ---------------------------------------------------------------------------


def plan_fuel_stops(
    candidates: Sequence[Candidate],
    total_miles: float,
    tank_gallons: float,
    mpg: float,
    start_gallons: float,
) -> FuelPlan:
    stations = _on_route(candidates, total_miles)
    range_miles = tank_gallons * mpg
    plan = FuelPlan()

    pos, fuel = 0.0, start_gallons
    current: int | None = None  # None = still at the origin, where nothing can be bought
    nxt = 0  # index of the first station after `current`

    while True:
        if pos + fuel * mpg >= total_miles - EPS:
            plan.end_gallons = fuel - (total_miles - pos) / mpg
            return plan

        if current is None:
            # Leaving the origin: drive to the first station (any price beats "can't buy").
            if nxt >= len(stations) or stations[nxt].mile > pos + fuel * mpg + EPS:
                raise _gap_error(stations, pos, total_miles, range_miles)
            target: int | None = nxt
        else:
            here = stations[current]
            window_end, cheaper = nxt, None
            while window_end < len(stations) and stations[window_end].mile <= pos + range_miles + EPS:
                if cheaper is None and stations[window_end].price < here.price - EPS:
                    cheaper = window_end
                window_end += 1

            if cheaper is not None:
                target = cheaper
                need = (stations[cheaper].mile - pos) / mpg - fuel
            elif total_miles - pos <= range_miles + EPS:
                target = None
                need = (total_miles - pos) / mpg - fuel
            elif window_end == nxt:
                raise _gap_error(stations, pos, total_miles, range_miles)
            else:
                # Cheapest fuel in range is here: fill up, then go to the cheapest
                # station in range (ties -> farthest, for fewer stops).
                target = min(range(nxt, window_end), key=lambda k: (stations[k].price, -stations[k].mile))
                need = tank_gallons - fuel

            if need > EPS:
                plan.purchases.append(Purchase(here, need, fuel))
                fuel += need
            if target is None:
                plan.end_gallons = fuel - (total_miles - pos) / mpg
                return plan

        fuel = max(fuel - (stations[target].mile - pos) / mpg, 0.0)
        pos = stations[target].mile
        current, nxt = target, target + 1


# ---------------------------------------------------------------------------
# Dynamic programme: minimum dollars + stop_penalty per stop
# ---------------------------------------------------------------------------
#
# Lemma (Khuller, Malekian & Mestre, "To fill or not to fill: the gas station
# problem", ESA 2007): for a fixed set of stops some optimal plan, at every
# stop, either FILLS the tank or buys JUST ENOUGH to reach the next stop empty.
# So the fuel on arrival at a stop is one of three things, which are the DP
# states (with the key used below):
#   ("origin",)   start_gallons - dist(origin, here)   first stop
#   ("empty",)    0                                    previous stop bought "just enough"
#   ("full", i)   tank - dist(i, here)                 previous stop i filled up
# That is O(n*w) states and O(n*w^2) transitions, w = stations within one tank
# (~20 after the planner's bucketing): a few milliseconds per trip.


def plan_fuel_stops_dp(
    candidates: Sequence[Candidate],
    total_miles: float,
    tank_gallons: float,
    mpg: float,
    start_gallons: float,
    stop_penalty: float = 0.0,
) -> FuelPlan:
    stations = _on_route(candidates, total_miles)
    range_miles = tank_gallons * mpg
    if start_gallons * mpg >= total_miles - EPS:
        return FuelPlan(end_gallons=start_gallons - total_miles / mpg)

    miles = [s.mile for s in stations]
    n = len(stations)

    def arrival_fuel(j: int, state: tuple) -> float:
        if state[0] == "origin":
            return start_gallons - miles[j] / mpg
        if state[0] == "empty":
            return 0.0
        return tank_gallons - (miles[j] - miles[state[1]]) / mpg

    # best[j][state] = (objective so far, back-pointer (i, state_at_i, gallons_bought_at_i) or None)
    best: list[dict[tuple, tuple[float, tuple | None]]] = [{} for _ in range(n)]

    def relax(j: int, state: tuple, value: float, back: tuple | None) -> None:
        current = best[j].get(state)
        if current is None or value < current[0] - EPS:
            best[j][state] = (value, back)

    for j in range(n):
        if miles[j] > start_gallons * mpg + EPS:
            break
        relax(j, ("origin",), stop_penalty, None)

    final_value, final_back = float("inf"), None
    for i in range(n):
        if not best[i]:
            continue
        price = stations[i].price
        to_finish = (total_miles - miles[i]) / mpg
        reachable = []
        k = i + 1
        while k < n and miles[k] - miles[i] <= range_miles + EPS:
            reachable.append(k)
            k += 1

        for state, (value, _) in list(best[i].items()):
            fuel = arrival_fuel(i, state)
            if fuel < -1e-6:
                continue
            # Last stop: buy just enough to finish (if fuel already sufficed, i would not be a stop).
            if to_finish <= tank_gallons + EPS and fuel <= to_finish + EPS:
                buy = max(0.0, to_finish - fuel)
                if value + buy * price < final_value - EPS:
                    final_value, final_back = value + buy * price, (i, state, buy)
            for j in reachable:
                leg = (miles[j] - miles[i]) / mpg
                if fuel <= leg + EPS:
                    relax(j, ("empty",), value + (leg - fuel) * price + stop_penalty, (i, state, leg - fuel))
                fill = tank_gallons - fuel
                relax(j, ("full", i), value + fill * price + stop_penalty, (i, state, fill))

    if final_back is None:
        gap = _first_gap(stations, total_miles, range_miles, start_gallons * mpg)
        raise _gap_error(stations, max(gap, 0.0), total_miles, range_miles)

    # Walk the back-pointers from the final stop to the first.
    chain = []
    step: tuple | None = final_back
    while step is not None:
        i, state, bought = step
        chain.append((i, state, bought))
        step = best[i][state][1]
    chain.reverse()

    plan = FuelPlan()
    for i, state, bought in chain:
        if bought > EPS:
            plan.purchases.append(Purchase(stations[i], bought, max(arrival_fuel(i, state), 0.0)))
    last_i, last_state, last_bought = chain[-1]
    plan.end_gallons = max(
        max(arrival_fuel(last_i, last_state), 0.0) + last_bought - (total_miles - miles[last_i]) / mpg, 0.0
    )
    return plan
