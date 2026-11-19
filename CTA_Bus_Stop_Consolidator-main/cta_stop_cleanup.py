#!/usr/bin/env python3
"""
CTA bus stop boardings/alightings cleanup.

Steps:
  1. Load raw data (tab/comma separated text, or .xlsx)
  2. Keep weekday rows only
  3. Average boardings and alightings per stop across all picks (months)
  4. Pair each stop with its opposite-direction stop (matched by coordinates)
  5. Write an Excel workbook:
       - One sheet per route, each holding two tables side by side:
           left  = every stop separate (one row per direction/stop)
           right = opposite-direction stops merged; unpaired stops kept solo and labeled
       - "Notes" sheet: settings used and data warnings

Usage:
  python cta_stop_cleanup.py raw_data.txt
  python cta_stop_cleanup.py raw_data.csv -o cta_clean.xlsx --max-dist 100 --zero-fill

Requires: pandas, numpy, openpyxl
"""
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


# --------------------------------------------------------------------------- #
# Loading and averaging
# --------------------------------------------------------------------------- #
def load_raw(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path)
    else:
        # sep=None lets pandas sniff tabs vs commas
        df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [str(c).strip().upper() for c in df.columns]

    needed = {"PICKNAME", "DAYTYPE", "ROUTEA", "DIRECTION", "CTASTOPID",
              "STREET", "CROSS_ST", "BOARDINGS", "ALIGHTINGS", "LAT", "LON"}
    missing = needed - set(df.columns)
    if missing:
        raise SystemExit(f"Missing expected columns: {sorted(missing)}")

    # Routes can be alphanumeric (J14, X9), so keep as text
    for col in ("ROUTEA", "DIRECTION", "DAYTYPE", "PICKNAME", "STREET", "CROSS_ST"):
        df[col] = df[col].astype(str).str.strip()
    for col in ("BOARDINGS", "ALIGHTINGS", "LAT", "LON"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "SORTORDER" not in df.columns:
        df["SORTORDER"] = np.nan
    return df


def hav(lat1, lon1, lat2, lon2):
    """Element-wise haversine distance in meters."""
    R = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def _pick_order(wk):
    """{pickname: chronological index}. Parses names like 'Jan-25'; falls back to file order."""
    names = list(dict.fromkeys(wk["PICKNAME"]))
    parsed = pd.to_datetime(pd.Series(names), format="%b-%y", errors="coerce")
    if parsed.notna().all():
        names = [n for _, n in sorted(zip(parsed, names))]
    return {n: i for i, n in enumerate(names)}


def _pick_span(picks, inv):
    picks = sorted(picks)
    return f"{inv[picks[0]]} to {inv[picks[-1]]} ({len(picks)} picks)"


def merge_moved_stops(wk, merge_dist, require_names=True):
    """
    A stop that moves (e.g. near side -> far side of an intersection) gets a new CTASTOPID. Treat two IDs
    on the same route + direction as ONE stop when ALL of these hold:
      - they never appear in the same pick (a real second stop would coexist with the first)
      - they are within merge_dist meters
      - their street/cross-street names agree (unless require_names is False)
    Returns (wk with CTASTOPID replaced by the newest ID, audit DataFrame of every candidate considered).
    """
    order = _pick_order(wk)
    inv = {i: n for n, i in order.items()}
    wk = wk.copy()
    wk["_P"] = wk["PICKNAME"].map(order)
    wk = wk.sort_values(["ROUTEA", "DIRECTION", "_P"], kind="mergesort")
    mapping, audit = {}, []

    for (route, direction), g in wk.groupby(["ROUTEA", "DIRECTION"], sort=False):
        info = g.groupby("CTASTOPID", sort=False).agg(
            picks=("_P", lambda x: frozenset(x)), last=("_P", "max"),
            lat=("LAT", "last"), lon=("LON", "last"),
            street=("STREET", "last"), cross=("CROSS_ST", "last"))
        ids = list(info.index)
        if len(ids) < 2 or info["lat"].isna().all():
            continue
        D = haversine_matrix(info["lat"].values, info["lon"].values, info["lat"].values, info["lon"].values)
        cand = []
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                if D[x, y] <= merge_dist and not (info["picks"].iloc[x] & info["picks"].iloc[y]):
                    cand.append((D[x, y], x, y))
        if not cand:
            continue
        cand.sort()
        parent = list(range(len(ids)))
        gpicks = [set(p) for p in info["picks"]]

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for dist, x, y in cand:
            names_ok = (norm_streets(info["street"].iloc[x], info["cross"].iloc[x])
                        == norm_streets(info["street"].iloc[y], info["cross"].iloc[y]))
            rx, ry = find(x), find(y)
            if require_names and not names_ok:
                status = "Not merged: street names differ"
            elif rx == ry:
                status = "Merged"
            elif gpicks[rx] & gpicks[ry]:
                status = "Not merged: would put two stops in the same pick"
            else:
                parent[ry] = rx
                gpicks[rx] |= gpicks[ry]
                status = "Merged"
            audit.append({
                "ROUTE": route, "DIRECTION": direction,
                "STOP_ID_A": ids[x], "PICKS_A": _pick_span(info["picks"].iloc[x], inv),
                "LOCATION_A": f'{info["street"].iloc[x]} & {info["cross"].iloc[x]}',
                "STOP_ID_B": ids[y], "PICKS_B": _pick_span(info["picks"].iloc[y], inv),
                "LOCATION_B": f'{info["street"].iloc[y]} & {info["cross"].iloc[y]}',
                "DISTANCE_M": round(float(dist), 1),
                "NAMES_AGREE": "Yes" if names_ok else "No",
                "RESULT": status,
            })

        groups = {}
        for i in range(len(ids)):
            groups.setdefault(find(i), []).append(i)
        for members in groups.values():
            if len(members) > 1:
                canon = max(members, key=lambda i: (info["last"].iloc[i], len(info["picks"].iloc[i])))
                for i in members:
                    mapping[(route, direction, ids[i])] = ids[canon]

    if mapping:
        keys = list(zip(wk["ROUTEA"], wk["DIRECTION"], wk["CTASTOPID"]))
        wk["CTASTOPID"] = [mapping.get(k, k[2]) for k in keys]
    audit = pd.DataFrame(audit, columns=["ROUTE", "DIRECTION", "STOP_ID_A", "PICKS_A", "LOCATION_A",
                                         "STOP_ID_B", "PICKS_B", "LOCATION_B", "DISTANCE_M",
                                         "NAMES_AGREE", "RESULT"])
    return wk, audit


def consensus_sortorder(wk):
    """
    One reliable SORTORDER per (route, direction, stop). The raw numbers can shift between picks, so
    instead of a median we take the NEWEST pick's order as the backbone and slot every stop that isn't
    in it (removed or relocated earlier) in right after its predecessor in the last pick it appeared in.
    """
    out = {}
    for (route, direction), g in wk.groupby(["ROUTEA", "DIRECTION"], sort=False):
        g = g.dropna(subset=["SORTORDER"])
        if g.empty:
            continue
        latest = g["_P"].max()
        ref = g[g["_P"] == latest].sort_values("SORTORDER", kind="mergesort")
        so = dict(zip(ref["CTASTOPID"], ref["SORTORDER"]))
        missing = [sid for sid in g["CTASTOPID"].unique() if sid not in so]
        extras = {}
        for sid in missing:
            p = g.loc[g["CTASTOPID"] == sid, "_P"].max()
            gp = g[g["_P"] == p].sort_values("SORTORDER", kind="mergesort")
            ids = list(gp["CTASTOPID"])
            k = ids.index(sid)
            pred = next((ids[j] for j in range(k - 1, -1, -1) if ids[j] in so), None)
            extras.setdefault(pred, []).append((p, k, sid))
        for pred, lst in extras.items():
            base = so[pred] if pred is not None else (min(so.values()) - 1 if so else 0)
            for n, (_, _, sid) in enumerate(sorted(lst)):
                out[(route, direction, sid)] = base + 0.001 * (n + 1)
        for sid, v in so.items():
            out[(route, direction, sid)] = v
    return out


def average_by_stop(df: pd.DataFrame, zero_fill: bool, merge_dist: float = 100.0, merge_names: bool = True):
    """
    Weekday only; one row per route + direction + stop with averaged ridership.
    Stops whose ID changed because they moved (see merge_moved_stops) are combined into one stop.
    The audit of ID changes is attached as result.attrs['stop_id_changes'].
    """
    wk = df[df["DAYTYPE"].str.lower() == "weekday"].copy()
    n_picks = wk["PICKNAME"].nunique()

    warnings = []
    if n_picks != 12:
        warnings.append(f"Found {n_picks} distinct picks (expected 12): "
                        f"{sorted(wk['PICKNAME'].unique())}")

    if merge_dist and merge_dist > 0:
        wk, audit = merge_moved_stops(wk, merge_dist, require_names=merge_names)
    else:
        order = _pick_order(wk)
        wk["_P"] = wk["PICKNAME"].map(order)
        wk = wk.sort_values(["ROUTEA", "DIRECTION", "_P"], kind="mergesort")
        audit = pd.DataFrame()
    n_merged = int((audit["RESULT"] == "Merged").sum()) if len(audit) else 0
    if n_merged:
        warnings.append(f"{n_merged} moved-stop ID changes were merged into single stops "
                        f"(see the Stop ID Changes sheet to verify).")
    n_unmerged = int(audit["RESULT"].str.startswith("Not merged").sum()) if len(audit) else 0
    if n_unmerged:
        warnings.append(f"{n_unmerged} possible ID changes were NOT merged (street names differ or the IDs "
                        f"overlap in a pick); see the Stop ID Changes sheet.")

    keys = ["ROUTEA", "DIRECTION", "CTASTOPID"]
    # wk is in pick order, so "last" = the newest pick's value (current street names / location)
    g = wk.groupby(keys, as_index=False, sort=False).agg(
        STREET=("STREET", "last"),
        CROSS_ST=("CROSS_ST", "last"),
        LAT=("LAT", "last"),
        LON=("LON", "last"),
        SUM_BOARD=("BOARDINGS", "sum"),
        SUM_ALIGHT=("ALIGHTINGS", "sum"),
        MEAN_BOARD=("BOARDINGS", "mean"),
        MEAN_ALIGHT=("ALIGHTINGS", "mean"),
        PICKS_PRESENT=("PICKNAME", "nunique"),
    )
    so = consensus_sortorder(wk)
    g["SORTORDER"] = [so.get(k, np.nan) for k in zip(g["ROUTEA"], g["DIRECTION"], g["CTASTOPID"])]

    if zero_fill:
        # Stop missing from a pick is treated as 0 riders that pick
        g["AVG_BOARDINGS"] = g["SUM_BOARD"] / n_picks
        g["AVG_ALIGHTINGS"] = g["SUM_ALIGHT"] / n_picks
    else:
        # Average only over picks where the stop actually appears
        g["AVG_BOARDINGS"] = g["MEAN_BOARD"]
        g["AVG_ALIGHTINGS"] = g["MEAN_ALIGHT"]

    g["AVG_TOTAL"] = g["AVG_BOARDINGS"] + g["AVG_ALIGHTINGS"]
    g = g.drop(columns=["SUM_BOARD", "SUM_ALIGHT", "MEAN_BOARD", "MEAN_ALIGHT"])

    partial = g[g["PICKS_PRESENT"] < n_picks]
    if len(partial):
        warnings.append(
            f"{len(partial)} stops appear in fewer than {n_picks} picks "
            f"(see PICKS_PRESENT column). "
            f"Averages are {'zero-filled' if zero_fill else 'over picks present only'}.")

    g = g.sort_values(["ROUTEA", "DIRECTION", "SORTORDER"]).reset_index(drop=True)
    g = g[["ROUTEA", "DIRECTION", "CTASTOPID", "STREET", "CROSS_ST", "SORTORDER", "LAT", "LON",
           "PICKS_PRESENT", "AVG_BOARDINGS", "AVG_ALIGHTINGS", "AVG_TOTAL"]]
    g.attrs["stop_id_changes"] = audit
    return g, n_picks, warnings


# --------------------------------------------------------------------------- #
# Pairing opposite-direction stops
# --------------------------------------------------------------------------- #
def haversine_matrix(lat1, lon1, lat2, lon2):
    """Pairwise distance in meters between two sets of points."""
    R = 6371000.0
    p1, p2 = np.radians(lat1)[:, None], np.radians(lat2)[None, :]
    dphi = p2 - p1
    dlmb = np.radians(lon2)[None, :] - np.radians(lon1)[:, None]
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def norm_streets(street, cross):
    """Rough normalized set of street names, used only as a sanity check on matches."""
    def clean(s):
        s = re.sub(r"\b(STREET|ST|AVENUE|AVE|ROAD|RD|BLVD|BOULEVARD)\b", "", s.upper())
        s = re.sub(r"\b(STATION|LINE)\b", "", s)
        return re.sub(r"[^A-Z0-9 ]", " ", s).split()
    return frozenset(clean(street) + clean(cross))


def pair_stops(stops: pd.DataFrame, max_dist: float) -> pd.DataFrame:
    """Pair opposite-direction stops only when street AND cross-street names agree.

    Nearby stops with mismatched intersection names are deliberately left as solo
    directional stops. The optimizer writes those candidates to a Pairing Review sheet.
    """
    pairs = []
    for route, rt in stops.groupby("ROUTEA"):
        rt = rt.dropna(subset=["LAT", "LON"])
        idx = rt.index.to_numpy()
        lat, lon = rt["LAT"].to_numpy(), rt["LON"].to_numpy()
        dirs = rt["DIRECTION"].to_numpy()
        names = [norm_streets(a, b) for a, b in zip(rt["STREET"], rt["CROSS_ST"])]
        D = haversine_matrix(lat, lon, lat, lon)
        D[dirs[:, None] == dirs[None, :]] = np.inf
        upper = np.triu(np.ones_like(D, dtype=bool), k=1)
        cand = [(D[i, j], i, j) for i, j in np.argwhere(np.isfinite(D) & upper)
                if D[i, j] <= max_dist and names[i] == names[j]]
        cand.sort()
        used = set()
        for d, i, j in cand:
            if i in used or j in used:
                continue
            used.update((i, j))
            pairs.append((idx[i], idx[j], d, "Name + distance"))
    return pd.DataFrame(pairs, columns=["IDX_A", "IDX_B", "DIST_M", "METHOD"])


def route_positions(rt):
    """
    Position (meters along the route) of every stop of ONE route, so rows can be listed in route order.
    The spine is the North/East direction (else the direction with the most stops), measured by summing
    distances between its stops in SORTORDER. Stops of the other direction(s) are projected onto that line.
    Works for routes that bend, unlike sorting by latitude/longitude.
    """
    pos = pd.Series(np.nan, index=rt.index, dtype=float)
    dirs = list(dict.fromkeys(rt["DIRECTION"]))
    pref = [d for d in dirs if str(d).strip().upper() in ("NORTH", "EAST", "NORTHBOUND", "EASTBOUND", "NB", "EB")]
    prim = pref[0] if pref else rt["DIRECTION"].value_counts().idxmax()
    p = rt[rt["DIRECTION"] == prim].dropna(subset=["SORTORDER", "LAT", "LON"]).sort_values("SORTORDER", kind="mergesort")
    if p.empty:
        return pos
    lat, lon = p["LAT"].values, p["LON"].values
    seg = hav(lat[:-1], lon[:-1], lat[1:], lon[1:]) if len(p) > 1 else np.array([])
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    pos.loc[p.index] = cum

    rest = rt.loc[~rt.index.isin(p.index)].dropna(subset=["LAT", "LON"])
    if rest.empty:
        return pos
    if len(p) == 1:
        pos.loc[rest.index] = 0.0
        return pos
    ky, kx = 110574.0, 111320.0 * np.cos(np.radians(lat.mean()))
    X, Y = (lon - lon[0]) * kx, (lat - lat[0]) * ky
    ABx, ABy = X[1:] - X[:-1], Y[1:] - Y[:-1]
    L2 = np.maximum(ABx ** 2 + ABy ** 2, 1e-9)
    for i, r in rest.iterrows():
        px, py = (r["LON"] - lon[0]) * kx, (r["LAT"] - lat[0]) * ky
        t = np.clip(((px - X[:-1]) * ABx + (py - Y[:-1]) * ABy) / L2, 0.0, 1.0)
        d2 = (px - (X[:-1] + t * ABx)) ** 2 + (py - (Y[:-1] + t * ABy)) ** 2
        j = int(np.argmin(d2))
        pos.at[i] = cum[j] + t[j] * seg[j]
    return pos


def build_combined(stops: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    paired_idx = set(pairs["IDX_A"]) | set(pairs["IDX_B"])
    posmap = {}
    for _, rt in stops.groupby("ROUTEA"):
        posmap.update(route_positions(rt).to_dict())

    def side(r, n):
        return {f"DIRECTION_{n}": r["DIRECTION"],
                f"STOPID_{n}": r["CTASTOPID"],
                f"BOARD_{n}": r["AVG_BOARDINGS"],
                f"ALIGHT_{n}": r["AVG_ALIGHTINGS"]}

    for _, p in pairs.iterrows():
        a, b = stops.loc[p["IDX_A"]], stops.loc[p["IDX_B"]]
        a, b = sorted([a, b], key=lambda r: r["DIRECTION"])   # stable ordering
        same_names = norm_streets(a["STREET"], a["CROSS_ST"]) == \
                     norm_streets(b["STREET"], b["CROSS_ST"])
        rows.append({
            "ROUTE": a["ROUTEA"],
            "STATUS": "Paired",
            "LOCATION": f'{a["STREET"]} & {a["CROSS_ST"]}',
            "LOCATION_OPPOSITE": f'{b["STREET"]} & {b["CROSS_ST"]}',
            "NAMES_AGREE": "Yes" if same_names else "Check",
            "MATCH_METHOD": p["METHOD"],
            "DIST_BETWEEN_M": round(p["DIST_M"], 1),
            **side(a, 1), **side(b, 2),
            "AVG_BOARDINGS": a["AVG_BOARDINGS"] + b["AVG_BOARDINGS"],
            "AVG_ALIGHTINGS": a["AVG_ALIGHTINGS"] + b["AVG_ALIGHTINGS"],
            "LAT": (a["LAT"] + b["LAT"]) / 2,
            "LON": (a["LON"] + b["LON"]) / 2,
            "PICKS_PRESENT_MIN": min(a["PICKS_PRESENT"], b["PICKS_PRESENT"]),
            "_POS": np.nanmean([posmap.get(a.name, np.nan), posmap.get(b.name, np.nan)])
                    if not (np.isnan(posmap.get(a.name, np.nan)) and np.isnan(posmap.get(b.name, np.nan))) else np.nan,
        })

    for i, r in stops.drop(index=list(paired_idx)).iterrows():
        rows.append({
            "ROUTE": r["ROUTEA"],
            "STATUS": f'Solo - {r["DIRECTION"]} only',
            "LOCATION": f'{r["STREET"]} & {r["CROSS_ST"]}',
            "LOCATION_OPPOSITE": "",
            "NAMES_AGREE": "",
            "MATCH_METHOD": "",
            "DIST_BETWEEN_M": np.nan,
            **side(r, 1),
            "AVG_BOARDINGS": r["AVG_BOARDINGS"],
            "AVG_ALIGHTINGS": r["AVG_ALIGHTINGS"],
            "LAT": r["LAT"],
            "LON": r["LON"],
            "PICKS_PRESENT_MIN": r["PICKS_PRESENT"],
            "_POS": posmap.get(i, np.nan),
        })

    out = pd.DataFrame(rows)
    out["AVG_TOTAL"] = out["AVG_BOARDINGS"] + out["AVG_ALIGHTINGS"]

    # List locations in route order (distance along the route), not by latitude/longitude
    out = out.sort_values(["ROUTE", "_POS", "LOCATION"], kind="mergesort", na_position="last")
    out = out.drop(columns="_POS").reset_index(drop=True)
    return out


# --------------------------------------------------------------------------- #
# Excel output
# --------------------------------------------------------------------------- #
def style_table(ws, first_col, n_cols, n_rows):
    """Style one table whose header is on row 1, starting at column first_col (1-based)."""
    head_fill = PatternFill("solid", fgColor="1F4E78")
    for c in range(first_col, first_col + n_cols):
        cell = ws.cell(1, c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header = str(cell.value or "")
        width = len(header)
        for r in range(2, n_rows + 2):
            v = ws.cell(r, c).value
            width = max(width, len(f"{v:,.1f}") if isinstance(v, float) else len(str(v or "")))
            if header.startswith(("AVG_", "BOARD_", "ALIGHT_")):
                ws.cell(r, c).number_format = "#,##0.0"
            elif header in ("LAT", "LON"):
                ws.cell(r, c).number_format = "0.000000"
        ws.column_dimensions[get_column_letter(c)].width = min(max(width + 2, 10), 45)


def safe_sheet_name(route, used):
    name = re.sub(r"[\[\]:*?/\\]", "-", str(route))[:31] or "Route"
    base, k = name, 2
    while name.lower() in used:
        name = f"{base[:28]}_{k}"
        k += 1
    used.add(name.lower())
    return name


def route_sort_key(r):
    m = re.match(r"(\d+)(.*)", str(r))
    return (0, int(m.group(1)), m.group(2)) if m else (1, 0, str(r))


def write_workbook(path, by_dir, combined, notes, changes=None):
    by_cols = ["DIRECTION", "SORTORDER", "CTASTOPID", "STREET", "CROSS_ST",
               "AVG_BOARDINGS", "AVG_ALIGHTINGS", "AVG_TOTAL",
               "PICKS_PRESENT", "LAT", "LON"]
    comb_cols = ["STATUS", "LOCATION", "LOCATION_OPPOSITE", "NAMES_AGREE", "MATCH_METHOD", "DIST_BETWEEN_M",
                 "AVG_BOARDINGS", "AVG_ALIGHTINGS", "AVG_TOTAL",
                 "DIRECTION_1", "STOPID_1", "BOARD_1", "ALIGHT_1",
                 "DIRECTION_2", "STOPID_2", "BOARD_2", "ALIGHT_2",
                 "PICKS_PRESENT_MIN", "LAT", "LON"]

    routes = sorted(by_dir["ROUTEA"].unique(), key=route_sort_key) if by_dir is not None else []
    used = {"notes"}

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame({"Notes": notes}).to_excel(xw, sheet_name="Notes", index=False)
        xw.sheets["Notes"].column_dimensions["A"].width = 120

        if changes is not None and len(changes):
            changes.to_excel(xw, sheet_name="Stop ID Changes", index=False)
            ws = xw.sheets["Stop ID Changes"]
            style_table(ws, 1, len(changes.columns), len(changes))
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            used.add("stop id changes")

        for route in routes:
            left = by_dir[by_dir["ROUTEA"] == route][by_cols].reset_index(drop=True)
            right = combined[combined["ROUTE"] == route].reindex(columns=comb_cols).reset_index(drop=True)
            name = safe_sheet_name(route, used)

            # Left table: every stop separate. Right table: opposite stops merged.
            left.to_excel(xw, sheet_name=name, index=False, startcol=0)
            right_start = len(by_cols) + 1          # one blank spacer column between
            right.to_excel(xw, sheet_name=name, index=False, startcol=right_start)

            ws = xw.sheets[name]
            style_table(ws, 1, len(by_cols), len(left))
            style_table(ws, right_start + 1, len(comb_cols), len(right))
            # Dark red divider column between the two tables
            div_letter = get_column_letter(right_start)
            div_fill = PatternFill("solid", fgColor="8B0000")
            ws.column_dimensions[div_letter].width = 3
            ws.column_dimensions[div_letter].fill = div_fill     # whole column, incl. empty rows
            for r in range(1, max(len(left), len(right)) + 2):
                ws[f"{div_letter}{r}"].fill = div_fill
            ws.freeze_panes = "A2"
            # Filter on the Combined table (handy for STATUS = Solo / Paired)
            ws.auto_filter.ref = (f"{get_column_letter(right_start + 1)}1:"
                                  f"{get_column_letter(right_start + len(comb_cols))}{len(right) + 1}")



# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="raw data file (.txt/.csv/.tsv/.xlsx)")
    ap.add_argument("-o", "--output", type=Path, default=Path("cta_stops_clean.xlsx"))
    ap.add_argument("--max-dist", type=float, default=100.0,
                    help="max meters between opposite-direction stops to pair them (default 100)")
    ap.add_argument("--zero-fill", action="store_true",
                    help="treat a stop missing from a pick as 0 riders instead of ignoring that pick")
    ap.add_argument("--merge-dist", type=float, default=100.0,
                    help="meters; a stop whose ID changed (it moved) within this distance is treated as the "
                         "same stop (default 100)")
    ap.add_argument("--no-merge-moved", action="store_true",
                    help="do NOT combine stops whose ID changed mid-year")
    ap.add_argument("--merge-ignore-names", action="store_true",
                    help="merge moved stops even if their street/cross-street names differ")
    args = ap.parse_args()

    raw = load_raw(args.input)
    by_dir, n_picks, warnings = average_by_stop(
        raw, args.zero_fill, 0.0 if args.no_merge_moved else args.merge_dist, not args.merge_ignore_names)
    pairs = pair_stops(by_dir, args.max_dist)
    combined = build_combined(by_dir, pairs)

    n_paired = (combined["STATUS"] == "Paired").sum()
    n_solo = len(combined) - n_paired
    n_check = (combined["NAMES_AGREE"] == "Check").sum()

    notes = [
        f"Input: {args.input.name}; weekday rows only; {n_picks} picks averaged.",
        f"Averaging: {'zero-filled for missing picks' if args.zero_fill else 'over picks where the stop appears'}.",
        f"Pairing: opposite-direction stops on the same route within {args.max_dist:.0f} m, "
        f"matched one-to-one. Pairs with matching street names are made first (closest first), "
        f"then remaining stops are paired by distance only.",
        f"Stops that changed ID because they moved (same route/direction, never in the same pick, within "
        f"{args.merge_dist:g} m{'' if args.merge_ignore_names else ', matching street names'}) are combined into one stop; "
        f"coordinates/names are from the newest pick." if not args.no_merge_moved else
        "Moved-stop merging is OFF (--no-merge-moved).",
        "Rows are listed in route order: stops by SORTORDER (taken from the newest pick, with older/removed stops "
        "slotted in beside their neighbors); combined rows by distance along the route.",
        f"Result: {n_paired} paired locations, {n_solo} solo stops.",
        f"{n_check} pairs have NAMES_AGREE = 'Check' (street names differ); review these in the sheet.",
        "On each route sheet, the right-hand (combined) table's AVG_BOARDINGS/AVG_ALIGHTINGS are the sum of both directions; "
        "per-direction values are in BOARD_1/2 and ALIGHT_1/2.",
    ] + warnings

    write_workbook(args.output, by_dir, combined, notes, by_dir.attrs.get("stop_id_changes"))
    print("\n".join(notes))
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()