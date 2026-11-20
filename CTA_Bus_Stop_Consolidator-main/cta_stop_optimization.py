#!/usr/bin/env python3
"""
CTA bus stop reduction model (combination optimizer v2 - immutable ridership).

Takes the SAME raw boardings/alightings file as cta_stop_cleanup.py (keep both scripts in the
same folder) and proposes stops to remove, route by route.

How it decides
  1. Weekday ridership is averaged over all picks, and opposite-direction stops are paired into
     "locations" (a pair, or a solo stop) exactly like the cleanup script does.
  2. PROTECTED locations are never removed:
       TERMINAL   first/last stop of a route direction
       RAIL       street/cross-street text mentions a station/terminal/transit center
       TRANSFER   a stop of a CROSSING route (different main street) is within --transfer-radius
                  (uses every route in the file, even if you only analyze one); exact-coordinate
                  stops in a contiguous interlined section are excluded from automatic protection
       RIDERSHIP  top --protect-pct % of locations on the route (and above --min-protect-riders)
       MANUAL     listed in --protect-file (e.g. hospital stops)
  3. Combination optimizer searches alternative feasible removal sequences, rather than committing greedily:
       - too close:   nearest neighbor <= route-type minimum spacing * (1 + tolerance)
       - spacing:     the gap left after removal <= fixed route-type maximum spacing
       - ridership:   bottom --neighbor-logic-pct % of locations automatically pass the ridership test;
                      upper-middle locations use the local lower-neighbor comparison
     Cost = riders x added walk (feet) to the nearest remaining stop. Ridership eligibility is based on original CTA averages and original adjacent locations;
     removed riders are not added to neighbor totals for eligibility.
  4. Directional exception pass: one side of a surviving pair can be dropped if a PROTECTED stop in
     the same direction is within --asym-ft and spacing still holds (flagged ASYMMETRIC).
  5. Interlined sections are identified by at least two consecutive stops in a direction whose
     exact coordinates are also served by the same other route. Those stops are regular candidates,
     but at least one shared stop must remain in each direction-specific overlap section.
  6. Steps 3-5 are repeated at each tolerance in --sensitivity. A location whose outcome changes
     is flagged BORDERLINE for manual review.

Usage
  python cta_stop_reduction.py raw_data.csv -o reduction.xlsx
  python cta_stop_reduction.py raw_data.csv --routes 50            # just the 50 (still uses all routes
                                                                   # in the file to find transfers)
  python cta_stop_optimization.py raw_data.csv --routes 50 9 X49 --tolerance 5 --sensitivity 0 5

Requires: pandas, numpy, openpyxl
"""
import argparse
import fnmatch
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

try:
    from cta_stop_cleanup import (average_by_stop, haversine_matrix, load_raw, norm_streets,
                                  pair_stops, route_positions, route_sort_key)
except ImportError as exc:
    raise SystemExit(
        f"{exc}\n\ncta_stop_reduction.py needs the LATEST cta_stop_cleanup.py in the same folder. "
        "Replace your copy of cta_stop_cleanup.py with the newest version and run again.")

MI_M = 1609.344          # meters per mile
FT_M = 0.3048            # meters per foot
RAIL_RE = re.compile(r"\bSTATION\b|\bTERMINAL\b|TRANSIT CENTER|\bMETRA\b")

RED = PatternFill("solid", fgColor="F4CCCC")       # removed
ORANGE = PatternFill("solid", fgColor="FCE5CD")    # one side of a pair removed
YELLOW = PatternFill("solid", fgColor="FFF2CC")    # borderline (kept at primary tolerance)


def _norm_id(x):
    """Stop IDs as clean strings (8752, 8752.0, '8752' all -> '8752'); '' if missing."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return re.sub(r"\.0$", "", str(x).strip())


def load_protect_list(path: Path):
    """Read a CSV/xlsx with ROUTE and CTASTOPID columns -> set of (ROUTE upper, stop id)."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path, dtype=str)
    else:
        df = pd.read_csv(path, sep=None, engine="python", dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    missing = {"ROUTE", "CTASTOPID"} - set(df.columns)
    if missing:
        raise SystemExit(f"Protect file is missing required columns: {sorted(missing)}")
    return {(str(r).strip().upper(), _norm_id(s)) for r, s in zip(df["ROUTE"], df["CTASTOPID"])
            if str(r).strip() and _norm_id(s)}


def hav(lat1, lon1, lat2, lon2):
    """Element-wise haversine distance in meters."""
    R = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def tol_label(t):
    return f"{int(t) if float(t).is_integer() else t}PCT"


# --------------------------------------------------------------------------- #
# One route
# --------------------------------------------------------------------------- #
class RouteModel:
    def __init__(self, route, rtype, stops, pairs, transfers, manual, args,
                 interline_groups=None, interline_routes=None):
        self.route, self.rtype, self.args = route, rtype, args
        # Interlined sections are not automatic TRANSFER protection. They are
        # regular candidates, subject to a group-level rule that at least one
        # exact-coordinate shared stop remains in each contiguous overlap run.
        self.interline_groups = [set(g) for g in (interline_groups or []) if g]
        self.interline_routes = interline_routes or {}
        lo, hi = args.rapid_spacing if rtype == "Rapid" else args.local_spacing
        self.min_m, self.max_m = lo * MI_M, hi * MI_M
        self.st = stops
        self.base = {i: float(v) for i, v in stops["AVG_TOTAL"].items()}

        # ---- locations: a pair of stops, or a solo stop -------------------- #
        self.loc_of, self.locs = {}, {}
        n = 0
        for a, b in zip(pairs["IDX_A"], pairs["IDX_B"]):
            members = sorted([a, b], key=lambda i: str(stops.at[i, "DIRECTION"]))
            self.locs[n] = members
            for i in members:
                self.loc_of[i] = n
            n += 1
        for i in stops.index:
            if i not in self.loc_of:
                self.locs[n] = [i]
                self.loc_of[i] = n
                n += 1

        # ---- ordered stop sequence per direction + distance along route ---- #
        self.seq, self.pos, self.cum, self.noseq = {}, {}, {}, set()
        self.length = {}
        for d, g in stops.groupby("DIRECTION"):
            ok = g.dropna(subset=["SORTORDER", "LAT", "LON"]).sort_values("SORTORDER", kind="mergesort")
            ids = list(ok.index)
            self.noseq |= set(g.index) - set(ids)
            if len(ids) > 1:
                dd = hav(ok["LAT"].values[:-1], ok["LON"].values[:-1], ok["LAT"].values[1:], ok["LON"].values[1:])
                cum = np.concatenate([[0.0], np.cumsum(dd)])
            else:
                cum = np.zeros(len(ids))
            self.seq[d] = ids
            self.length[d] = float(cum[-1]) if len(cum) else 0.0
            for k, i in enumerate(ids):
                self.pos[i] = (d, k)
                self.cum[i] = float(cum[k])

        # Adjacent gaps that exceed the nominal maximum in the ORIGINAL network.
        # These commonly reflect barriers (river, expressway, rail yard, etc.).
        # A candidate stop at either end should be evaluated using its other side,
        # rather than being rejected because this pre-existing gap is already long.
        self.original_over_limit_edges = set()
        for d, ids in self.seq.items():
            for x, y in zip(ids[:-1], ids[1:]):
                if self.cum[y] - self.cum[x] > self.max_m:
                    self.original_over_limit_edges.add((x, y))

        # ---- protection ---------------------------------------------------- #
        self.total0 = {L: sum(self.base[i] for i in m) for L, m in self.locs.items()}
        vals = sorted(self.total0.values(), reverse=True)
        k = max(1, int(np.ceil(len(vals) * args.protect_pct / 100.0)))
        cutoff = vals[k - 1]
        self.protect, self.transfer_routes, self.location_interline_routes = {}, {}, {}
        for L, m in self.locs.items():
            r = []
            if any(i in self.noseq for i in m):
                r.append("NO_SORTORDER")
            if any(i in self.pos and self.pos[i][1] in (0, len(self.seq[self.pos[i][0]]) - 1) for i in m):
                r.append("TERMINAL")
            if any(RAIL_RE.search(f"{stops.at[i, 'STREET']} {stops.at[i, 'CROSS_ST']}".upper()) for i in m):
                r.append("RAIL")
            tr = set().union(*(transfers.get(i, set()) for i in m))
            interlined = set().union(*(self.interline_routes.get(i, set()) for i in m))
            tr -= interlined
            if tr:
                r.append("TRANSFER")
            if any((route.upper(), _norm_id(stops.at[i, "CTASTOPID"])) in manual for i in m):
                r.append("MANUAL")
            if args.protect_pct > 0 and self.total0[L] >= cutoff and self.total0[L] >= args.min_protect_riders:
                r.append("RIDERSHIP")
            self.protect[L] = r
            self.transfer_routes[L] = sorted(tr)
            self.location_interline_routes[L] = sorted(interlined)

        self.unprotected = [L for L in self.locs if not self.protect[L]]
        self.rank = {L: 1 + sum(1 for v in vals if v > self.total0[L]) for L in self.locs}
        # Route-wide rank cutoff: locations ranked below this percentile are
        # eligible under the ridership test without comparing against neighbors.
        self.neighbor_logic_cutoff = int(np.ceil(len(self.locs) * args.neighbor_logic_pct / 100.0))

    # ------------------------------------------------------------------ #
    def label(self, L):
        i = self.locs[L][0]
        return f"{self.st.at[i, 'STREET']} & {self.st.at[i, 'CROSS_ST']}"

    def static_info(self, L):
        """Original neighbors along the location's first direction (before any removals)."""
        m = self.locs[L]
        i = next((x for x in m if x in self.pos), None)
        info = dict(PREV_LOCATION="", PREV_TOTAL=np.nan, PREV_RANK=np.nan,
                    NEXT_LOCATION="", NEXT_TOTAL=np.nan, NEXT_RANK=np.nan,
                    RATIO_TO_LOWER_NEIGHBOR=np.nan, NEAREST_NEIGHBOR_FT=np.nan)
        if i is None:
            return info
        d, k = self.pos[i]
        seq = self.seq[d]
        tots = []
        for tag, j in (("PREV", k - 1), ("NEXT", k + 1)):
            if 0 <= j < len(seq):
                nl = self.loc_of[seq[j]]
                info[f"{tag}_LOCATION"] = self.label(nl)
                info[f"{tag}_TOTAL"] = self.total0[nl]
                info[f"{tag}_RANK"] = self.rank[nl]
                tots.append(self.total0[nl])
        if tots and min(tots) > 0:
            info["RATIO_TO_LOWER_NEIGHBOR"] = self.total0[L] / min(tots)
        gaps = []
        for x in m:
            if x in self.pos:
                dd, kk = self.pos[x]
                s = self.seq[dd]
                if kk > 0:
                    gaps.append(self.cum[x] - self.cum[s[kk - 1]])
                if kk < len(s) - 1:
                    gaps.append(self.cum[s[kk + 1]] - self.cum[x])
        if gaps:
            info["NEAREST_NEIGHBOR_FT"] = min(gaps) / FT_M
        return info

    # ------------------------------------------------------------------ #
    def run(self, tol):
        """Run the reduction at one tolerance (fraction, e.g. 0.10). Returns a result dict."""
        a = self.args
        cap, trig = self.max_m, self.min_m * (1 + tol)
        asym_m = a.asym_ft * FT_M
        kept = {i: True for i in self.st.index}
        absorbed = defaultdict(float)
        removed = {}                                   # stop idx -> dict(kind, walk_m, order)
        locs, loc_of, cum, pos, seq = self.locs, self.loc_of, self.cum, self.pos, self.seq

        def stop_total(i):
            return self.base[i] + absorbed[i]

        def loc_total(L):
            return sum(stop_total(i) for i in locs[L] if kept[i])

        def prev_next(i):
            d, k = pos[i]
            s = seq[d]
            j = k - 1
            while j >= 0 and not kept[s[j]]:
                j -= 1
            p = s[j] if j >= 0 else None
            j = k + 1
            while j < len(s) and not kept[s[j]]:
                j += 1
            n = s[j] if j < len(s) else None
            return p, n

        def evaluate(L):
            members = [i for i in locs[L] if kept[i]]
            if not members:
                return None
            merged_ok, directional_triggers, nb, cost = True, [], [], 0.0
            for i in members:
                if i in self.noseq or i not in pos:
                    return None
                p, n = prev_next(i)
                if p is None or n is None:
                    return None
                gp, gn = cum[i] - cum[p], cum[n] - cum[i]
                preexisting_barrier_gap = (
                    (p, i) in self.original_over_limit_edges
                    or (i, n) in self.original_over_limit_edges
                )
                if gp + gn > cap and not preexisting_barrier_gap:
                    merged_ok = False
                m = min(gp, gn)
                directional_triggers.append(m <= trig)
                def same_direction_location_total(j):
                    direction = self.st.at[j, "DIRECTION"]
                    return sum(stop_total(q) for q in locs[loc_of[j]]
                               if kept[q] and self.st.at[q, "DIRECTION"] == direction)
                nb += [same_direction_location_total(p), same_direction_location_total(n)]
                cost += stop_total(i) * m / FT_M
            directional_totals = [stop_total(i) for i in members]
            tot = sum(directional_totals)
            lower_neighbor = min(nb) if nb else np.nan
            ratio_to_lower = tot / lower_neighbor if lower_neighbor > 0 else np.nan
            # The bottom portion of the route-wide ridership distribution should
            # not be retained merely because it outperforms one adjacent stop.
            # Only the upper-middle band (after top-stop protection and through
            # this cutoff) is subject to the local neighbor-ridership comparison.
            bottom_half = self.rank[L] > self.neighbor_logic_cutoff
            # Evaluate each directional member against same-direction neighbor ridership.
            # A paired stop in the opposite direction can never inflate the neighbor total.
            direction_checks = []
            for i in members:
                directional_neighbors = []
                for j in (self.seq[self.pos[i][0]][self.pos[i][1] - 1]
                          if self.pos[i][1] > 0 else None,
                          self.seq[self.pos[i][0]][self.pos[i][1] + 1]
                          if self.pos[i][1] + 1 < len(self.seq[self.pos[i][0]]) else None):
                    if j is not None:
                        direction = self.st.at[i, "DIRECTION"]
                        directional_neighbors.append(sum(stop_total(q) for q in locs[loc_of[j]]
                            if kept[q] and self.st.at[q, "DIRECTION"] == direction))
                dtotal = stop_total(i)
                dlower = min(directional_neighbors) if directional_neighbors else np.nan
                direction_checks.append(dtotal < a.low_volume or
                    (dlower > 0 and dtotal <= a.ratio * dlower))
            # Whole-pair removal needs a spacing trigger in every direction.
            trigger = bool(directional_triggers) and all(directional_triggers)
            rule_b = bottom_half or all(direction_checks)
            return dict(merged_ok=merged_ok, trigger=trigger, rule_b=rule_b, cost=cost, tot=tot,
                        lower_neighbor=lower_neighbor, ratio_to_lower=ratio_to_lower)

        interline_guarded = set()

        def can_remove(members):
            """Never remove the last still-kept shared stop in an overlap section."""
            removing = set(members)
            for group in self.interline_groups:
                if group.intersection(removing):
                    if not any(kept[j] for j in group if j not in removing):
                        return False
            return True

        def remove_stop(i, kind):
            p, n = prev_next(i)
            gp, gn = cum[i] - cum[p], cum[n] - cum[i]
            t = stop_total(i)
            wp, wn = 1.0 / max(gp, 1.0), 1.0 / max(gn, 1.0)
            absorbed[p] += t * wp / (wp + wn)
            absorbed[n] += t * wn / (wp + wn)
            kept[i] = False
            removed[i] = dict(kind=kind, walk_m=min(gp, gn), order=len(removed) + 1)

        # ---- pass 1: whole locations, cheapest first ---------------------- #
        while True:
            best = None
            for L in self.unprotected:
                if not all(kept[i] for i in locs[L]):
                    continue
                e = evaluate(L)
                if e and e["merged_ok"] and e["trigger"] and e["rule_b"]:
                    if not can_remove(locs[L]):
                        interline_guarded.add(L)
                        continue
                    key = (e["cost"], e["tot"], L)
                    if best is None or key < best[0]:
                        best = (key, L)
            if best is None:
                break
            for i in locs[best[1]]:
                remove_stop(i, "REDUNDANT")
            # note: stops of one location sit in different directions, so removal order is independent

        # ---- pass 2: directional exceptions ------------------------------- #
        for L in self.unprotected:
            m = locs[L]
            if len(m) < 2 or not all(kept[i] for i in m):
                continue
            cands = []
            for i in m:
                if i in self.noseq or i not in pos:
                    continue
                p, n = prev_next(i)
                if p is None or n is None:
                    continue
                gp, gn = cum[i] - cum[p], cum[n] - cum[i]
                preexisting_barrier_gap = (
                    (p, i) in self.original_over_limit_edges
                    or (i, n) in self.original_over_limit_edges
                )
                if gp + gn > cap and not preexisting_barrier_gap:
                    continue
                near, g = (p, gp) if gp <= gn else (n, gn)
                if g <= asym_m and self.protect[loc_of[near]]:
                    if not can_remove([i]):
                        interline_guarded.add(L)
                        continue
                    cands.append((stop_total(i), i))
            if cands:
                remove_stop(min(cands)[1], "ASYMMETRIC")

        # ---- why were the others kept? ------------------------------------ #
        reasons = {}
        for L in self.unprotected:
            if all(not kept[i] for i in locs[L]) or any(not kept[i] for i in locs[L]):
                continue
            e = evaluate(L)
            if (L in interline_guarded and e is not None and e["merged_ok"]
                    and e["trigger"] and e["rule_b"] and not can_remove(locs[L])):
                reasons[L] = "KEPT_LAST_INTERLINE_TRANSFER"
            elif e is None:
                reasons[L] = "KEPT_NO_NEIGHBOR"
            elif not e["trigger"]:
                reasons[L] = "KEPT_SPACING_OK"
            elif not e["merged_ok"]:
                reasons[L] = "KEPT_NEEDED_FOR_SPACING"
            elif not e["rule_b"]:
                reasons[L] = "KEPT_RIDERSHIP_NOT_LOW_ENOUGH_VS_NEIGHBOR"
            else:
                reasons[L] = "KEPT"

        diagnostics = {}
        for L in self.unprotected:
            if all(kept[i] for i in locs[L]):
                e = evaluate(L)
                if e is not None:
                    diagnostics[L] = {
                        "CURRENT_LOWER_NEIGHBOR_RIDERS": e["lower_neighbor"],
                        "CURRENT_RIDERSHIP_RATIO_TO_LOWER_NEIGHBOR": e["ratio_to_lower"],
                    }

        n_after = {d: sum(1 for i in s if kept[i]) for d, s in seq.items()}
        return dict(removed=removed, reasons=reasons, diagnostics=diagnostics,
                    n_after=n_after, kept=kept)



class OptimizedRouteModel(RouteModel):
    """Combination optimizer with immutable, original-data ridership eligibility.

    Spacing is evaluated against the proposed surviving configuration. Ridership
    eligibility is evaluated only against the original CTA averages and original
    adjacent locations, so earlier removals cannot change another stop's eligibility.
    The bounded search is repeated with forward and reverse candidate ordering; the
    best result is selected using a stable, order-independent tie-break.
    """
    def run(self, tol):
        a = self.args
        cap = self.max_m
        trig = self.min_m * (1 + tol)
        asym_m = a.asym_ft * FT_M
        locs, loc_of, cum, pos, seq = self.locs, self.loc_of, self.cum, self.pos, self.seq
        ids = list(self.st.index)
        id_pos = {v: k for k, v in enumerate(ids)}
        beam_width = max(1, int(getattr(a, 'beam_width', 12)))
        # Ridership comparison is deliberately static: no absorbed riders and no
        # replacement-neighbor totals are ever used for eligibility.
        def original_ridership(L):
            return float(self.total0[L])

        def original_direction_total(stop_idx):
            direction = self.st.at[stop_idx, 'DIRECTION']
            L = loc_of[stop_idx]
            return float(sum(self.base[q] for q in locs[L]
                             if self.st.at[q, 'DIRECTION'] == direction))

        def static_ridership_test(L):
            members = locs[L]
            total = original_ridership(L)
            if self.rank[L] > self.neighbor_logic_cutoff:
                return True, 'BOTTOM_50_AUTO_PASS', float('nan'), float('nan')
            adjacent = set()
            for i in members:
                d, k = pos[i]
                sq = seq[d]
                if k > 0:
                    adjacent.add(loc_of[sq[k - 1]])
                if k + 1 < len(sq):
                    adjacent.add(loc_of[sq[k + 1]])
            # Paired candidate + exclusively paired original adjacent locations:
            # compare combined NB+SB totals. Otherwise compare each direction only
            # with the same direction at its original adjacent locations.
            all_adjacent_paired = bool(adjacent) and all(len(locs[nL]) >= 2 for nL in adjacent)
            if len(members) >= 2 and all_adjacent_paired:
                lower = min(original_ridership(nL) for nL in adjacent)
                passed = total < a.low_volume or (lower > 0 and total <= a.ratio * lower)
                return passed, 'PAIRED_COMBINED_STATIC', lower, total / lower if lower > 0 else float('nan')
            checks, lowers = [], []
            for i in members:
                d, k = pos[i]
                sq = seq[d]
                neighbors = []
                for j in (sq[k - 1] if k > 0 else None,
                          sq[k + 1] if k + 1 < len(sq) else None):
                    if j is not None:
                        neighbors.append(original_direction_total(j))
                lower = min(neighbors) if neighbors else float('nan')
                value = float(self.base[i])
                lowers.append(lower)
                checks.append(value < a.low_volume or
                              (lower > 0 and value <= a.ratio * lower))
            lower = min((x for x in lowers if np.isfinite(x)), default=float('nan'))
            ratio = total / lower if lower > 0 else float('nan')
            mode = 'DIRECTIONAL_SOLO_STATIC' if len(members) == 1 else 'DIRECTIONAL_SOLO_ADJACENCY_STATIC'
            return all(checks), mode, lower, ratio

        static_eligibility = {L: static_ridership_test(L) for L in self.unprotected}

        def search(order):
            order_rank = {L: n for n, L in enumerate(order)}
            def prev_next(st, i):
                d, k = pos[i]
                sq = seq[d]
                j = k - 1
                while j >= 0 and sq[j] not in st['kept']:
                    j -= 1
                p = sq[j] if j >= 0 else None
                j = k + 1
                while j < len(sq) and sq[j] not in st['kept']:
                    j += 1
                n = sq[j] if j < len(sq) else None
                return p, n

            def can_remove(st, members):
                removing = set(members)
                for group in self.interline_groups:
                    if group.intersection(removing) and not any(
                        j in st['kept'] for j in group if j not in removing
                    ):
                        return False
                return True

            def evaluate_spacing(st, L):
                members = [i for i in locs[L] if i in st['kept']]
                if not members:
                    return None
                triggers, merged, cost, walks = [], True, 0.0, {}
                for i in members:
                    if i in self.noseq or i not in pos:
                        return None
                    p, n = prev_next(st, i)
                    if p is None or n is None:
                        return None
                    gp, gn = cum[i] - cum[p], cum[n] - cum[i]
                    barrier = ((p, i) in self.original_over_limit_edges or
                               (i, n) in self.original_over_limit_edges)
                    if gp + gn > cap and not barrier:
                        merged = False
                    near = min(gp, gn)
                    triggers.append(near <= trig)
                    cost += self.base[i] * near / FT_M
                    walks[i] = (p, n, gp, gn, near)
                return dict(members=members, merged=merged,
                            trigger=bool(triggers) and all(triggers),
                            cost=cost, total=sum(self.base[i] for i in members), walks=walks)

            def apply(st, kind, payload):
                kept = set(st['kept'])
                removed = dict(st['removed'])
                for i in payload:
                    p, n = prev_next(st, i)
                    gp, gn = cum[i] - cum[p], cum[n] - cum[i]
                    removed[i] = dict(kind=kind, walk_m=min(gp, gn), order=len(removed) + 1)
                    kept.remove(i)
                    st = dict(kept=frozenset(kept), removed=removed, guarded=st['guarded'])
                return st

            def actions(st):
                out = []
                for L in order:
                    if not all(i in st['kept'] for i in locs[L]):
                        continue
                    e = evaluate_spacing(st, L)
                    eligible = static_eligibility[L][0]
                    if e and e['merged'] and e['trigger'] and eligible:
                        if can_remove(st, e['members']):
                            payload = tuple(sorted(e['members'], key=lambda i: id_pos[i]))
                            out.append((e['cost'], e['total'], L, 'REDUNDANT', payload))
                        else:
                            st['guarded'] = frozenset(set(st['guarded']) | {L})
                # Existing asymmetric exception: only one side of a paired
                # location may be removed when a protected same-direction stop
                # is within the configured distance and the resulting gap fits.
                for L in order:
                    members = locs[L]
                    if len(members) < 2 or not all(i in st['kept'] for i in members):
                        continue
                    for i in members:
                        if i in self.noseq or i not in pos:
                            continue
                        p, n = prev_next(st, i)
                        if p is None or n is None:
                            continue
                        gp, gn = cum[i] - cum[p], cum[n] - cum[i]
                        barrier = ((p, i) in self.original_over_limit_edges or
                                   (i, n) in self.original_over_limit_edges)
                        near, gap = (p, gp) if gp <= gn else (n, gn)
                        if (gp + gn <= cap or barrier) and gap <= asym_m:
                            if self.protect[loc_of[near]] and can_remove(st, [i]):
                                out.append((self.base[i] * min(gp, gn) / FT_M,
                                            self.base[i], L, 'ASYMMETRIC', (i,)))
                # Stable action order prevents dataframe/index ordering from
                # deciding which equal-scoring states survive beam truncation.
                out.sort(key=lambda x: (x[0], x[1], self.label(x[2]),
                                        tuple(sorted(_norm_id(self.st.at[i, 'CTASTOPID']) for i in x[4])), x[3]))
                return out

            def score(st):
                whole = sum(1 for L, members in locs.items()
                            if all(i not in st['kept'] for i in members))
                weighted = sum(self.base[i] * r['walk_m'] / FT_M
                               for i, r in st['removed'].items())
                riders = sum(self.base[i] for i in st['removed'])
                return (whole, -weighted, -riders, len(st['removed']))

            def signature(st):
                return tuple(sorted(_norm_id(self.st.at[i, 'CTASTOPID']) + '|' +
                                     str(self.st.at[i, 'DIRECTION']) for i in st['kept']))

            initial = dict(kept=frozenset(ids), removed={}, guarded=frozenset())
            beam = [initial]
            best = initial
            seen = {signature(initial)}
            for _ in range(len(ids)):
                candidates = []
                for st in beam:
                    acts = actions(st)
                    if not acts and score(st) > score(best):
                        best = st
                    for _c, _r, _L, kind, payload in acts:
                        ns = apply(st, kind, payload)
                        sig = signature(ns)
                        if sig in seen:
                            continue
                        seen.add(sig)
                        candidates.append(ns)
                        if score(ns) > score(best):
                            best = ns
                if not candidates:
                    break
                candidates.sort(key=lambda st: (score(st), tuple(reversed(signature(st)))), reverse=True)
                beam = candidates[:beam_width]
            for st in beam:
                if score(st) > score(best):
                    best = st
            return best, score(best), signature(best)

        canonical = sorted(self.unprotected, key=lambda L: (self.label(L),
                            tuple(sorted(_norm_id(self.st.at[i, 'CTASTOPID']) for i in locs[L]))))
        # Candidate traversal variants deliberately test whether route traversal
        # direction affects the bounded search. The chosen result is the best
        # objective across both searches, with a stable signature tie-break.
        forward = search(canonical)
        reverse = search(list(reversed(canonical)))
        # A third deterministic shuffled traversal catches tie/beam effects that
        # a simple reversal may not expose. Fixed seed makes runs reproducible.
        import random
        shuffled_order = list(canonical)
        random.Random(20251120).shuffle(shuffled_order)
        shuffled = search(shuffled_order)
        results = [forward, reverse, shuffled]
        best = sorted(results, key=lambda x: (x[1], tuple(reversed(x[2]))), reverse=True)[0][0]
        order_invariant = len({result[2] for result in results}) == 1

        reasons, diagnostics = {}, {}
        # Evaluate kept candidates against their final spacing context, but their
        # ridership eligibility remains the precomputed immutable baseline test.
        def final_prev_next(i):
            d, k = pos[i]
            sq = seq[d]
            j = k - 1
            while j >= 0 and sq[j] not in best['kept']:
                j -= 1
            p = sq[j] if j >= 0 else None
            j = k + 1
            while j < len(sq) and sq[j] not in best['kept']:
                j += 1
            return p, sq[j] if j < len(sq) else None

        for L in self.unprotected:
            members = locs[L]
            eligible, mode, lower, ratio = static_eligibility[L]
            diagnostics[L] = {
                'ORIGINAL_LOWER_NEIGHBOR_RIDERS': lower,
                'ORIGINAL_RIDERSHIP_RATIO_TO_LOWER_NEIGHBOR': ratio,
                'RIDERSHIP_COMPARISON_MODE': mode,
                'ORDER_VALIDATION': 'PASS' if order_invariant else 'DIFFERED_FORWARD_REVERSE',
                'ORIGINAL_RIDERSHIP_ELIGIBLE': 'Yes' if eligible else 'No',
            }
            if all(i not in best['kept'] for i in members) or any(i not in best['kept'] for i in members):
                continue
            merged, triggers = True, []
            for i in members:
                if i not in pos or i in self.noseq:
                    merged = False
                    continue
                p, n = final_prev_next(i)
                if p is None or n is None:
                    merged = False
                    continue
                gp, gn = cum[i] - cum[p], cum[n] - cum[i]
                barrier = ((p, i) in self.original_over_limit_edges or
                           (i, n) in self.original_over_limit_edges)
                if gp + gn > cap and not barrier:
                    merged = False
                triggers.append(min(gp, gn) <= trig)
            trigger = bool(triggers) and all(triggers)
            still_has_interline_peer = all(
                not group.intersection(set(members)) or
                any(j in best['kept'] for j in group if j not in set(members))
                for group in self.interline_groups
            )
            if L in best['guarded'] and merged and trigger and eligible and not still_has_interline_peer:
                reasons[L] = 'KEPT_LAST_INTERLINE_TRANSFER'
            elif not trigger:
                reasons[L] = 'KEPT_SPACING_OK'
            elif not merged:
                reasons[L] = 'KEPT_NEEDED_FOR_SPACING'
            elif not eligible:
                reasons[L] = 'KEPT_RIDERSHIP_NOT_LOW_ENOUGH_VS_ORIGINAL_NEIGHBOR'
            else:
                reasons[L] = 'KEPT'

        kept = {i: i in best['kept'] for i in ids}
        n_after = {d: sum(1 for i in sq if kept[i]) for d, sq in seq.items()}
        return dict(removed=best['removed'], reasons=reasons, diagnostics=diagnostics,
                    n_after=n_after, kept=kept,
                    order_validation='PASS' if order_invariant else 'DIFFERED_FORWARD_REVERSE',
                    forward_signature=forward[2], reverse_signature=reverse[2])


# --------------------------------------------------------------------------- #
# Whole-dataset assembly
# --------------------------------------------------------------------------- #
def detect_transfers(by_dir, route, stops, radius_m, overlap, primary):
    """For each stop on `route`, the set of OTHER routes with a stop within radius_m."""
    other = by_dir[by_dir["ROUTEA"] != route]
    if not overlap:
        other = other[other["ROUTEA"].map(primary) != primary[route]]
    out = {i: set() for i in stops.index}
    ok = stops.dropna(subset=["LAT", "LON"])
    other = other.dropna(subset=["LAT", "LON"])
    if ok.empty or other.empty:
        return out
    D = haversine_matrix(ok["LAT"].values, ok["LON"].values, other["LAT"].values, other["LON"].values)
    near = D <= radius_m
    oroutes = other["ROUTEA"].values
    for row, i in enumerate(ok.index):
        out[i] = set(oroutes[near[row]])
    return out


def detect_interline_sections(by_dir, route, stops):
    """Find contiguous same-coordinate overlaps with another route.

    A single shared intersection is not enough to classify as interlining.
    A section requires at least two consecutive stops, in the analyzed route's
    direction sequence, whose exact (LAT, LON) coordinates are also served by
    the same other route. Each direction is evaluated separately so a viable
    transfer remains in each direction of the overlapping segment.

    Returns (groups, routes_by_stop). Each group is a set of stop row indices
    that form one contiguous interlined section; routes_by_stop identifies the
    other route(s) sharing those exact-coordinate stops.
    """
    other = by_dir[by_dir["ROUTEA"].astype(str) != str(route)].dropna(subset=["LAT", "LON"])
    coord_routes = defaultdict(set)
    for r, lat, lon in zip(other["ROUTEA"], other["LAT"], other["LON"]):
        coord_routes[(float(lat), float(lon))].add(str(r))

    routes_by_stop = {i: set() for i in stops.index}
    groups = []
    for _, direction_rows in stops.groupby("DIRECTION", sort=False):
        ordered = direction_rows.dropna(subset=["SORTORDER", "LAT", "LON"]).sort_values(
            "SORTORDER", kind="mergesort")
        ids = list(ordered.index)
        matches = {
            i: coord_routes.get((float(stops.at[i, "LAT"]), float(stops.at[i, "LON"])), set())
            for i in ids
        }
        other_routes = set().union(*(matches[i] for i in ids)) if ids else set()
        for other_route in other_routes:
            run = []
            for i in ids:
                if other_route in matches[i]:
                    run.append(i)
                else:
                    if len(run) >= 2:
                        groups.append(set(run))
                        for j in run:
                            routes_by_stop[j].add(other_route)
                    run = []
            if len(run) >= 2:
                groups.append(set(run))
                for j in run:
                    routes_by_stop[j].add(other_route)
    return groups, routes_by_stop


def sensitivity_label(sigs):
    return "BORDERLINE" if len(set(sigs)) > 1 else "STABLE"


def pairing_review_candidates(stops, max_dist):
    """Nearby opposite-direction stops with mismatched intersection names, left solo."""
    rows = []
    for route, rt in stops.groupby("ROUTEA"):
        rt = rt.dropna(subset=["LAT", "LON"])
        if len(rt) < 2:
            continue
        idx = rt.index.to_numpy()
        D = haversine_matrix(rt["LAT"].to_numpy(), rt["LON"].to_numpy(),
                             rt["LAT"].to_numpy(), rt["LON"].to_numpy())
        dirs = rt["DIRECTION"].astype(str).to_numpy()
        names = [norm_streets(a, b) for a, b in zip(rt["STREET"], rt["CROSS_ST"])]
        for i, j in np.argwhere(np.triu((D <= max_dist) & (dirs[:, None] != dirs[None, :]), k=1)):
            if names[i] == names[j]:
                continue
            a, b = rt.loc[idx[i]], rt.loc[idx[j]]
            rows.append({
                "ROUTE": route,
                "STOP_ID_A": _norm_id(a["CTASTOPID"]), "DIRECTION_A": a["DIRECTION"],
                "LOCATION_A": f'{a["STREET"]} & {a["CROSS_ST"]}',
                "STOP_ID_B": _norm_id(b["CTASTOPID"]), "DIRECTION_B": b["DIRECTION"],
                "LOCATION_B": f'{b["STREET"]} & {b["CROSS_ST"]}',
                "DISTANCE_M": float(D[i, j]), "DISTANCE_FT": float(D[i, j] / FT_M),
                "PAIRING_ACTION": "NOT PAIRED - BOTH STOPS TREATED AS SOLO",
                "REVIEW_FLAG": "NEARBY OPPOSITE-DIRECTION STOPS HAVE DIFFERENT INTERSECTION NAMES",
            })
    return pd.DataFrame(rows)


def process(by_dir, pairs, args, tols, primary_tol, rapid_patterns, manual, sel_routes):
    primary = (by_dir.assign(_n=[" ".join(sorted(norm_streets(s, ""))) for s in by_dir["STREET"]])
                     .groupby("ROUTEA")["_n"].agg(lambda x: x.value_counts().idxmax()))
    loc_rows, stop_rows, individual_rows, sum_rows, gap_rows, warnings = [], [], [], [], [], []

    for route in sorted(sel_routes, key=route_sort_key):
        stops = by_dir[by_dir["ROUTEA"] == route]
        prs = pairs[pairs["IDX_A"].isin(stops.index)] if len(pairs) else pairs
        rtype = "Rapid" if any(fnmatch.fnmatch(route.upper(), p.upper()) for p in rapid_patterns) else "Local"
        transfers = detect_transfers(by_dir, route, stops, args.transfer_radius, args.overlap_transfers, primary)
        interline_groups, interline_routes = detect_interline_sections(by_dir, route, stops)
        m = OptimizedRouteModel(route, rtype, stops, prs, transfers, manual, args,
                       interline_groups=interline_groups, interline_routes=interline_routes)
        if m.noseq:
            warnings.append(f"Route {route}: {len(m.noseq)} stops have no SORTORDER/coordinates and were "
                            f"left out of spacing (protected).")

        runs = {t: m.run(t / 100.0) for t in tols}
        for t, result in runs.items():
            if result.get('order_validation') != 'PASS':
                warnings.append(f"Route {route} at {t:g}% tolerance: forward/reverse/shuffled search produced different kept-stop sets; selected the better-scoring result. Review ORDER_VALIDATION.")
        prim = runs[primary_tol]

        # ---------- location table ---------- #
        posr = route_positions(m.st)               # distance along the route -> rows listed in route order

        def loc_pos(L):
            v = [posr.get(i, np.nan) for i in m.locs[L]]
            v = [x for x in v if not np.isnan(x)]
            return float(np.mean(v)) if v else float("inf")

        order = sorted(m.locs, key=lambda L: (loc_pos(L), m.label(L)))
        n_loc = len(m.locs)
        for L in order:
            mem = m.locs[L]
            sig = [tuple(sorted(i for i in mem if i in runs[t]["removed"])) for t in tols]
            removed_now = [i for i in mem if i in prim["removed"]]
            if m.protect[L]:
                decision, reason = "PROTECTED", "; ".join("PROTECTED_" + r for r in m.protect[L])
                sens = "STABLE"
            elif len(removed_now) == len(mem):
                decision, reason = "REMOVED", "REMOVED_REDUNDANT"
                sens = sensitivity_label(sig)
            elif removed_now:
                decision, reason = "REMOVED (one side)", "REMOVED_ASYMMETRIC"
                sens = sensitivity_label(sig)
            else:
                decision, reason = "KEPT", prim["reasons"].get(L, "KEPT")
                sens = sensitivity_label(sig)

            walk_ft = sum(m.base[i] * prim["removed"][i]["walk_m"] / FT_M for i in removed_now)
            flags = []
            if sens == "BORDERLINE":
                flags.append("BORDERLINE")
            if decision == "REMOVED (one side)":
                flags.append("ASYMMETRIC")
            if decision.startswith("REMOVED") and m.rank[L] <= np.ceil(n_loc * args.review_pct / 100.0):
                flags.append("HIGH_RIDERSHIP_REMOVED")

            sts = m.st.loc[mem]
            row = {
                "ROUTE": route, "ROUTE_TYPE": rtype,
                "LOCATION": m.label(L),
                "DIRECTIONS": " / ".join(str(x) for x in sts["DIRECTION"]),
                "STOP_IDS": " / ".join(_norm_id(x) for x in sts["CTASTOPID"]),
                "AVG_BOARDINGS": sts["AVG_BOARDINGS"].sum(), "AVG_ALIGHTINGS": sts["AVG_ALIGHTINGS"].sum(),
                "AVG_TOTAL": m.total0[L], "RANK_IN_ROUTE": m.rank[L], "LOCATIONS_ON_ROUTE": n_loc,
                "DECISION": decision, "REASON": reason, "SENSITIVITY": sens,
                "REVIEW_FLAGS": "; ".join(flags),
                "TRANSFER_ROUTES": ", ".join(m.transfer_routes[L]),
                "INTERLINED_ROUTES": ", ".join(m.location_interline_routes.get(L, [])),
                **m.static_info(L),
                **prim["diagnostics"].get(L, {}),
                "ADDED_WALK_RIDER_FT": walk_ft if removed_now else np.nan,
            }
            for t, sg in zip(tols, sig):
                row[f"REMOVED_AT_{tol_label(t)}"] = ("Yes" if len(sg) == len(mem)
                                                     else "One side" if sg else "No")
            row["LAT"], row["LON"] = sts["LAT"].mean(), sts["LON"].mean()
            loc_rows.append(row)

            # One row per analyzed route/direction/stop, retaining each stop's own
            # coordinates. Location-level grouping remains available in All Locations.
            removed_set = set(removed_now)
            for i in mem:
                is_removed = i in removed_set
                removal = prim["removed"].get(i, {}) if is_removed else {}
                individual_rows.append({
                    "ROUTE": route,
                    "DIRECTION": m.st.at[i, "DIRECTION"],
                    "CTASTOPID": _norm_id(m.st.at[i, "CTASTOPID"]),
                    "STOP_NAME": f'{m.st.at[i, "STREET"]} & {m.st.at[i, "CROSS_ST"]}',
                    "STREET": m.st.at[i, "STREET"],
                    "CROSS_ST": m.st.at[i, "CROSS_ST"],
                    "LAT": m.st.at[i, "LAT"],
                    "LON": m.st.at[i, "LON"],
                    "AVG_BOARDINGS": m.st.at[i, "AVG_BOARDINGS"],
                    "AVG_ALIGHTINGS": m.st.at[i, "AVG_ALIGHTINGS"],
                    "AVG_TOTAL": m.base[i],
                    "STOP_DECISION": "REMOVED" if is_removed else "KEPT",
                    "LOCATION": m.label(L),
                    "LOCATION_DECISION": decision,
                    "LOCATION_REASON": reason,
                    "INTERLINED_ROUTES": ", ".join(sorted(m.interline_routes.get(i, set()))),
                    "SENSITIVITY": sens,
                    "REVIEW_FLAGS": "; ".join(flags),
                    "REMOVAL_REASON": ("REMOVED_" + removal.get("kind", "")) if is_removed else "",
                    "ADDED_WALK_FT": removal.get("walk_m", np.nan) / FT_M if is_removed else np.nan,
                    "RANK_IN_ROUTE": m.rank[L],
                    "RIDERSHIP_COMPARISON_MODE": prim["diagnostics"].get(L, {}).get("RIDERSHIP_COMPARISON_MODE", ""),
                    "ORIGINAL_RIDERSHIP_ELIGIBLE": prim["diagnostics"].get(L, {}).get("ORIGINAL_RIDERSHIP_ELIGIBLE", ""),
                    "ORDER_VALIDATION": prim["diagnostics"].get(L, {}).get("ORDER_VALIDATION", ""),
                    # Keep stop-level outcomes from every sensitivity run so the
                    # workbook and map can compare the primary result with (for example) 5%.
                    **{f"STOP_DECISION_AT_{tol_label(t)}": (
                        "REMOVED" if i in runs[t]["removed"] else "KEPT"
                    ) for t in tols},
                })

            for i in removed_now:
                r = prim["removed"][i]
                stop_rows.append({
                    "ROUTE": route, "DIRECTION": m.st.at[i, "DIRECTION"], "CTASTOPID": _norm_id(m.st.at[i, "CTASTOPID"]),
                    "STREET": m.st.at[i, "STREET"], "CROSS_ST": m.st.at[i, "CROSS_ST"],
                    "AVG_BOARDINGS": m.st.at[i, "AVG_BOARDINGS"], "AVG_ALIGHTINGS": m.st.at[i, "AVG_ALIGHTINGS"],
                    "AVG_TOTAL": m.base[i], "REASON": "REMOVED_" + r["kind"], "SENSITIVITY": sens,
                    "ADDED_WALK_FT": r["walk_m"] / FT_M, "RANK_IN_ROUTE": m.rank[L],
                    "LAT": m.st.at[i, "LAT"], "LON": m.st.at[i, "LON"],
                })

        # ---------- gaps between consecutive KEPT stops ---------- #
        cap_p = m.max_m
        route_gaps, before_over = [], 0
        for d, sq in m.seq.items():
            for x, y in zip(sq[:-1], sq[1:]):
                if m.cum[y] - m.cum[x] > cap_p:
                    before_over += 1
            ks = [i for i in sq if prim["kept"][i]]
            for x, y in zip(ks[:-1], ks[1:]):
                gap = m.cum[y] - m.cum[x]
                between = m.pos[y][1] - m.pos[x][1] - 1
                has_original_barrier_gap = any(
                    m.pos[a][0] == d and m.pos[x][1] <= m.pos[a][1] < m.pos[b][1] <= m.pos[y][1]
                    for a, b in m.original_over_limit_edges
                )
                if gap > cap_p and has_original_barrier_gap:
                    flag = "PRE_EXISTING_BARRIER_GAP"
                elif gap > cap_p:
                    flag = "OVER_LIMIT"
                elif gap > m.max_m:
                    flag = "OVER_MAX"
                elif gap < m.min_m:
                    flag = "UNDER_MIN"
                else:
                    flag = "OK"
                route_gaps.append({
                    "ROUTE": route, "DIRECTION": d,
                    "FROM_STOP_ID": _norm_id(m.st.at[x, "CTASTOPID"]),
                    "FROM_LOCATION": f"{m.st.at[x, 'STREET']} & {m.st.at[x, 'CROSS_ST']}",
                    "TO_STOP_ID": _norm_id(m.st.at[y, "CTASTOPID"]),
                    "TO_LOCATION": f"{m.st.at[y, 'STREET']} & {m.st.at[y, 'CROSS_ST']}",
                    "GAP_FT": gap / FT_M, "GAP_MI": gap / MI_M,
                    "STOPS_REMOVED_BETWEEN": between,
                    "CAUSED_BY_REMOVALS": "Yes" if between > 0 else "No",
                    "FLAG": flag,
                    "LIMIT_FT": cap_p / FT_M,
                })
        gap_rows += route_gaps

        # ---------- route summary ---------- #
        rem = prim["removed"]
        n_stops = len(m.st)
        tot_len = sum(m.length.values())
        gaps_b = sum(max(len(s) - 1, 0) for s in m.seq.values())
        gaps_a = sum(max(n - 1, 0) for n in prim["n_after"].values())
        riders_rem = sum(m.base[i] for i in rem)
        rider_ft = sum(m.base[i] * r["walk_m"] / FT_M for i, r in rem.items())
        locs_df = [r for r in loc_rows if r["ROUTE"] == route]
        srow = {
            "ROUTE": route, "TYPE": rtype,
            "SPACING_BAND_MI": f"{m.min_m / MI_M:.3g}-{m.max_m / MI_M:.3g}",
            "LOCATIONS": n_loc,
            "PROTECTED_LOCATIONS": sum(1 for r in locs_df if r["DECISION"] == "PROTECTED"),
            "LOCATIONS_REMOVED": sum(1 for r in locs_df if r["DECISION"] == "REMOVED"),
            "ONE_SIDE_REMOVED": sum(1 for r in locs_df if r["DECISION"] == "REMOVED (one side)"),
            "STOPS_BEFORE": n_stops, "STOPS_REMOVED": len(rem),
            "PCT_STOPS_REMOVED": 100.0 * len(rem) / n_stops if n_stops else np.nan,
            "AVG_SPACING_BEFORE_MI": tot_len / gaps_b / MI_M if gaps_b else np.nan,
            "AVG_SPACING_AFTER_MI": tot_len / gaps_a / MI_M if gaps_a else np.nan,
            "RIDERS_AT_REMOVED_STOPS": riders_rem,
            "PCT_ROUTE_RIDERS_AT_REMOVED": 100.0 * riders_rem / sum(m.base.values()) if m.base and sum(m.base.values()) else np.nan,
            "AVG_ADDED_WALK_FT_RIDER_WEIGHTED": rider_ft / riders_rem if riders_rem else np.nan,
            "BORDERLINE_LOCATIONS": sum(1 for r in locs_df if r["SENSITIVITY"] == "BORDERLINE"),
            "LONGEST_GAP_AFTER_FT": max((g["GAP_FT"] for g in route_gaps), default=np.nan),
            "GAPS_OVER_LIMIT_BEFORE": before_over,
            "GAPS_OVER_LIMIT_AFTER": sum(1 for g in route_gaps if g["FLAG"] == "OVER_LIMIT"),
        }
        for t in tols:
            srow[f"STOPS_REMOVED_AT_{tol_label(t)}"] = len(runs[t]["removed"])
        sum_rows.append(srow)

    return (pd.DataFrame(loc_rows), pd.DataFrame(stop_rows), pd.DataFrame(individual_rows),
            pd.DataFrame(sum_rows), pd.DataFrame(gap_rows), warnings)


# --------------------------------------------------------------------------- #
# Excel output
# --------------------------------------------------------------------------- #
FLOAT1 = ("AVG_", "PREV_TOTAL", "NEXT_TOTAL", "RIDERS_AT", "ADDED_WALK", "PCT_", "AVG_ADDED")
INT0 = ("NEAREST_NEIGHBOR_FT",)


def style_df_sheet(ws, df, row_fill=None):
    head = PatternFill("solid", fgColor="1F4E78")
    for c in range(1, len(df.columns) + 1):
        cell = ws.cell(1, c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        h = str(cell.value)
        width = len(h)
        for r in range(2, len(df) + 2):
            v = ws.cell(r, c).value
            if isinstance(v, float):
                if h in ("LAT", "LON"):
                    ws.cell(r, c).number_format = "0.000000"
                elif h.startswith("AVG_SPACING") or h == "GAP_MI":
                    ws.cell(r, c).number_format = "0.000"
                elif h == "RATIO_TO_LOWER_NEIGHBOR":
                    ws.cell(r, c).number_format = "0.00"
                elif h in INT0:
                    ws.cell(r, c).number_format = "#,##0"
                else:
                    ws.cell(r, c).number_format = "#,##0.0"
            width = max(width, len(f"{v:,.1f}") if isinstance(v, float) else len(str(v or "")))
        ws.column_dimensions[get_column_letter(c)].width = min(max(width + 2, 9), 42)
    ws.freeze_panes = "A2"
    if len(df):
        ws.auto_filter.ref = f"A1:{get_column_letter(len(df.columns))}{len(df) + 1}"
    if row_fill is not None:
        for r in range(len(df)):
            fill = row_fill(df.iloc[r])
            if fill is not None:
                for c in range(1, len(df.columns) + 1):
                    ws.cell(r + 2, c).fill = fill


def loc_fill(row):
    if row["DECISION"] == "REMOVED":
        return RED
    if row["DECISION"] == "REMOVED (one side)":
        return ORANGE
    if row["SENSITIVITY"] == "BORDERLINE":
        return YELLOW
    return None


def individual_stop_fill(row):
    if row["STOP_DECISION"] == "REMOVED":
        return RED
    if row["LOCATION_DECISION"] == "PROTECTED":
        return PatternFill("solid", fgColor="DDEBF7")
    if row["SENSITIVITY"] == "BORDERLINE":
        return YELLOW
    return PatternFill("solid", fgColor="E2F0D9")


def gap_fill(row):
    if row["FLAG"] == "PRE_EXISTING_BARRIER_GAP":
        return YELLOW
    if row["FLAG"] == "OVER_LIMIT":
        return ORANGE if row["CAUSED_BY_REMOVALS"] == "Yes" else YELLOW
    return None


def write_workbook(path, notes, summary, locs, removed, individual, gaps, changes=None, pairing_review=None):
    review = locs[locs["REVIEW_FLAGS"] != ""] if len(locs) else locs
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame({"Notes": notes}).to_excel(xw, sheet_name="Notes", index=False)
        xw.sheets["Notes"].column_dimensions["A"].width = 140
        for name, df, fill in (("Summary", summary, None), ("All Locations", locs, loc_fill),
                               ("Review Queue", review, loc_fill), ("Individual Stops", individual, individual_stop_fill),
                               ("Remaining Gaps", gaps, gap_fill), ("Removed Stops", removed, None)):
            df.to_excel(xw, sheet_name=name, index=False)
            style_df_sheet(xw.sheets[name], df, fill)
        if pairing_review is not None:
            pairing_review.to_excel(xw, sheet_name="Pairing Review", index=False)
            style_df_sheet(xw.sheets["Pairing Review"], pairing_review)
        if changes is not None and len(changes):
            changes.to_excel(xw, sheet_name="Stop ID Changes", index=False)
            style_df_sheet(xw.sheets["Stop ID Changes"], changes)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="raw data file (same format as cta_stop_cleanup.py)")
    ap.add_argument("-o", "--output", type=Path, default=Path("cta_stop_reduction.xlsx"))
    ap.add_argument("--routes", nargs="+", default=None, help="only analyze these routes, e.g. --routes 50 X49")
    ap.add_argument("--rapid-routes", nargs="*", default=["X*", "J14"],
                    help="route patterns treated as rapid/express (default X* and J14); everything else local")
    ap.add_argument("--local-spacing", nargs=2, type=float, default=[0.125, 1500 / 5280],
                    metavar=("MIN", "MAX"),
                    help="local route min/max stop spacing in miles (default 0.125 mi / 1500 ft)")
    ap.add_argument("--rapid-spacing", nargs=2, type=float, default=[0.2, 1 / 3], metavar=("MIN", "MAX"),
                    help="rapid route min/max stop spacing in miles (default 0.2 0.333)")
    ap.add_argument("--tolerance", type=float, default=5.0,
                    help="primary tolerance in percent used for decisions (default 5%%; local trigger 693 ft; max gap remains a hard 1500 ft)")
    ap.add_argument("--sensitivity", nargs="+", type=float, default=[0, 5],
                    help="tolerances (percent) to compare (default 0 5); 5%% is the primary decision run")
    ap.add_argument("--protect-pct", type=float, default=15.0, help="protect top N%% of locations per route (default 15)")
    ap.add_argument("--min-protect-riders", type=float, default=10.0,
                    help="a top-ranked location still isn't protected below this avg daily total (default 10)")
    ap.add_argument("--neighbor-logic-pct", type=float, default=50.0,
                    help="only locations in the top N%% (excluding protected top stops) use local neighbor ridership logic; bottom locations automatically pass the ridership test (default 50)")
    ap.add_argument("--ratio", type=float, default=1.0,
                    help="for locations subject to neighbor logic, removable only if total <= ratio x lower neighbor total (default 1.0 = weaker than both)")
    ap.add_argument("--low-volume", type=float, default=5.0,
                    help="locations below this avg daily total pass the ridership test automatically (default 5)")
    ap.add_argument("--transfer-radius", type=float, default=75.0,
                    help="meters; another route's stop this close makes a transfer (default 75)")
    ap.add_argument("--overlap-transfers", action="store_true",
                    help="also count routes on the SAME main street (e.g. 49 vs X49) as transfers")
    ap.add_argument("--asym-ft", type=float, default=400.0,
                    help="feet; a protected same-direction stop this close can justify removing one side of a pair")
    ap.add_argument("--protect-file", type=Path, default=None,
                    help="CSV/xlsx with ROUTE and CTASTOPID columns of stops to always keep (e.g. hospitals)")
    ap.add_argument("--beam-width", type=int, default=12, help="number of alternative configurations retained per search depth (default 12)")
    ap.add_argument("--review-pct", type=float, default=30.0,
                    help="removed locations ranked in the top N%% of the route get a review flag (default 30)")
    ap.add_argument("--max-dist", type=float, default=100.0,
                    help="meters for pairing opposite-direction stops (not the stop-spacing maximum; default 100)")
    ap.add_argument("--zero-fill", action="store_true", help="count missing picks as 0 riders (see cleanup script)")
    ap.add_argument("--merge-dist", type=float, default=100.0,
                    help="meters; a stop whose ID changed (it moved) within this distance is the same stop (default 100)")
    ap.add_argument("--no-merge-moved", action="store_true", help="do NOT combine stops whose ID changed mid-year")
    ap.add_argument("--merge-ignore-names", action="store_true",
                    help="merge moved stops even if their street/cross-street names differ")
    args = ap.parse_args()

    raw = load_raw(args.input)
    by_dir, n_picks, data_warnings = average_by_stop(
        raw, args.zero_fill, 0.0 if args.no_merge_moved else args.merge_dist, not args.merge_ignore_names)
    changes = by_dir.attrs.get("stop_id_changes")

    available = sorted(by_dir["ROUTEA"].unique(), key=route_sort_key)
    if args.routes:
        want = {r.upper() for r in args.routes}
        sel = [r for r in available if r.upper() in want]
        missing = want - {r.upper() for r in sel}
        if not sel:
            raise SystemExit(f"None of {sorted(want)} found. Routes in file include: {available[:20]} ...")
        if missing:
            data_warnings.append(f"Routes not found in data: {sorted(missing)}")
    else:
        sel = available

    selected_stops = by_dir[by_dir["ROUTEA"].isin(sel)]
    pairs = pair_stops(selected_stops, args.max_dist)
    pairing_review = pairing_review_candidates(selected_stops, args.max_dist)

    manual = set()
    if args.protect_file:
        manual = load_protect_list(args.protect_file)

    tols = sorted(set(float(t) for t in args.sensitivity) | {float(args.tolerance)})
    primary_tol = float(args.tolerance)

    locs, removed, individual, summary, gaps, warns = process(by_dir, pairs, args, tols, primary_tol,
                                            args.rapid_routes, manual, sel)

    # Make pairing anomalies visible directly on each affected stop, not only in the audit tab.
    if len(pairing_review) and len(individual):
        flagged_ids = set()
        for _, pr in pairing_review.iterrows():
            flagged_ids.add((str(pr["ROUTE"]), str(pr["STOP_ID_A"])))
            flagged_ids.add((str(pr["ROUTE"]), str(pr["STOP_ID_B"])))
        for ix, row in individual.iterrows():
            if (str(row["ROUTE"]), str(row["CTASTOPID"])) in flagged_ids:
                current = str(row.get("REVIEW_FLAGS", "") or "")
                tags = [x for x in current.split("; ") if x]
                if "PAIRING_MISMATCH_SOLO" not in tags:
                    tags.append("PAIRING_MISMATCH_SOLO")
                individual.at[ix, "REVIEW_FLAGS"] = "; ".join(tags)

    # total row
    if len(summary):
        tot = {c: summary[c].sum() for c in summary.columns
               if c.startswith(("LOCATIONS", "PROTECTED", "ONE_SIDE", "STOPS_", "BORDERLINE", "RIDERS_", "GAPS_"))}
        tot.update({"ROUTE": "ALL SELECTED", "PCT_STOPS_REMOVED": 100.0 * tot["STOPS_REMOVED"] / tot["STOPS_BEFORE"],
                    "LONGEST_GAP_AFTER_FT": summary["LONGEST_GAP_AFTER_FT"].max()})
        summary = pd.concat([summary, pd.DataFrame([tot])], ignore_index=True)

    n_loc_removed = int((locs["DECISION"] == "REMOVED").sum()) if len(locs) else 0
    notes = [
        f"Input: {args.input.name}; weekday only; {n_picks} picks averaged. Routes analyzed: {len(sel)}.",
        f"Spacing bands: local {args.local_spacing[0]:g} mi minimum / {args.local_spacing[1] * 5280:,.0f} ft maximum; "
        f"rapid {args.rapid_spacing[0]:g}-{args.rapid_spacing[1]:g} mi. "
        f"Rapid patterns: {', '.join(args.rapid_routes) or 'none'}.",
        f"Primary tolerance {primary_tol:g}% (decisions in the sheets; default 5%); comparison runs at "
        f"{', '.join(tol_label(t).replace('PCT', '%') for t in tols)}.",
        "Interlining rule: contiguous runs of at least two stops with exact matching coordinates on another route "
        "are treated as ordinary consolidation candidates, not automatic TRANSFER protection; at least one "
        "shared stop is retained in each direction-specific overlap section. Single shared intersections remain "
        "eligible for normal transfer protection. "
        f"Protection: terminals, rail/station names, transfers within {args.transfer_radius:g} m of a crossing route "
        f"({'including' if args.overlap_transfers else 'excluding'} same-street routes), top {args.protect_pct:g}% of "
        f"locations per route (min {args.min_protect_riders:g} riders), plus {len(manual)} manual stops. "
        f"Local neighbor-ridership logic applies only to the upper {args.neighbor_logic_pct:g}% of route locations "
        f"(excluding protected locations); the bottom {100 - args.neighbor_logic_pct:g}% automatically pass the "
        f"ridership test but must still meet the spacing tests.",
        f"Removal tests: nearest neighbor <= min spacing x (1+tol); merged gap <= fixed maximum spacing (no tolerance), "
        f"except where a candidate is adjacent to an ORIGINAL gap already above the maximum (treated as a likely "
        f"barrier gap); for locations subject to neighbor logic, total <= {args.ratio:g} x lower neighbor total "
        f"(or < {args.low_volume:g} riders). Bottom-ranked locations automatically pass the ridership test. "
        f"Combination optimizer: beam width {args.beam_width}; maximizes whole-location removals, then minimizes passenger-weighted added walk and removed ridership. Ridership eligibility uses immutable original CTA averages and original adjacent locations; no absorbed riders are used in eligibility.",
        f"Directional exceptions: one side of a pair removable if a protected same-direction stop is within {args.asym_ft:g} ft.",
        f"Result at primary tolerance: {n_loc_removed} whole locations removed, {len(removed)} stops removed in total.",
        "Row colors in All Locations / Review Queue: soft red = removed; orange = one side of a pair removed; yellow = kept "
        "but flips under a different tolerance (BORDERLINE).",
        "Remaining Gaps sheet: distance along the route between each pair of consecutive KEPT stops, per direction. "
        "FLAG = OVER_LIMIT (beyond the fixed maximum spacing), PRE_EXISTING_BARRIER_GAP, "
        "OVER_MAX, UNDER_MIN, or OK. PRE_EXISTING_BARRIER_GAP is yellow and identifies a remaining "
        "over-limit gap containing an original over-limit edge (likely a river, expressway, or rail yard). "
        "Original over-limit gaps are exempted from blocking a nearby stop removal; the candidate is still "
        "evaluated against its other side and the ridership test. Orange OVER_LIMIT gaps are attributable to removals.",
        "KEPT_RIDERSHIP_NOT_LOW_ENOUGH_VS_NEIGHBOR means the location is in the upper-middle ridership band "
        "and did not meet the local comparison against its lower-ridership adjacent neighbor; it does NOT mean "
        "the stop ranked highly across the whole route. Bottom-ranked locations bypass this comparison but still "
        "must meet the spacing tests. "
        "Use RANK_IN_ROUTE for route-wide ranking and ORIGINAL_LOWER_NEIGHBOR_RIDERS / "
        "ORIGINAL_RIDERSHIP_RATIO_TO_LOWER_NEIGHBOR to inspect the immutable local comparison. "
        "SENSITIVITY = BORDERLINE means the outcome changes across the tolerance runs; review those by hand.",
        "Individual Stops contains one row per analyzed route/direction/stop with its own stop ID and coordinates; STOP_DECISION_AT_* columns show the stop-level outcome at each comparison tolerance (STOP_DECISION_AT_0PCT and STOP_DECISION_AT_5PCT by default). INTERLINED_ROUTES lists routes sharing the exact coordinates as part of a contiguous interlined section.",
        "KEPT_LAST_INTERLINE_TRANSFER means this stop/location was otherwise removable, but removing it would eliminate the final remaining shared stop in an interlined section for that direction. These stops are not protected individually; the section-level minimum-one-stop rule applies.",
        "ADDED_WALK is straight-line-along-route feet to the nearest remaining stop in that direction at the time of removal.",
        "This is a screening model. It does not know about points of interest, crosswalk/signal locations, or non-CTA transfers "
        "(Pace/Metra) unless you list them in --protect-file.",
    ] + data_warnings + warns

    if changes is not None and len(changes):
        changes = changes[changes["ROUTE"].isin(sel)]
    notes.append("Opposite-direction pairing requires matching normalized STREET + CROSS_ST names. Nearby mismatched-name candidates are NOT paired; each remains a solo directional stop and is listed in Pairing Review.")
    notes.append("Neighbor-ridership comparison uses immutable original CTA averages and original adjacent locations. Paired locations use combined NB+SB totals when all original adjacent locations are paired; otherwise comparisons are direction-specific. Solo candidates always use direction-specific comparisons. Riders from removed stops are never absorbed into neighbor totals for eligibility.")
    notes.append("Order validation: every tolerance is searched with forward, reverse, and fixed-seed shuffled candidate traversal. The best objective is selected with a stable tie-break. ORDER_VALIDATION in All Locations / Individual Stops reports whether both traversals reached the same kept-stop set; DIFFERED_FORWARD_REVERSE flags a route requiring review because the bounded beam search did not converge to the same set.")
    write_workbook(args.output, notes, summary, locs, removed, individual, gaps, changes, pairing_review)

    # Plain CSV list of the removed stops (handy for mapping or comparing against another list)
    csv_path = args.output.with_name(args.output.stem + "_removed_stops.csv")
    if len(removed):
        out = pd.DataFrame({
            "ROUTE": removed["ROUTE"], "ROUTE_NAME": "", "CTASTOPID": removed["CTASTOPID"],
            "STOP_NAME": removed["STREET"] + " & " + removed["CROSS_ST"], "REMOVAL_TYPE": removed["REASON"],
            "ROUTES_STILL_SERVING": "", "LAT": removed["LAT"], "LON": removed["LON"]})
    else:
        out = pd.DataFrame(columns=["ROUTE", "ROUTE_NAME", "CTASTOPID", "STOP_NAME", "REMOVAL_TYPE",
                                    "ROUTES_STILL_SERVING", "LAT", "LON"])
    out.to_csv(csv_path, index=False)

    print("\n".join(notes))
    print(f"\nWrote {args.output} and {csv_path}")


if __name__ == "__main__":
    main()