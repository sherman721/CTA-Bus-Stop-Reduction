#!/usr/bin/env python3
"""
Filter a GTFS feed (e.g. CTA's google_transit.zip, unzipped) down to one or more routes.

stop_times.txt alone can't say which route a trip belongs to: that link is trips.txt (trip -> route_id)
and routes.txt (route_id -> route_short_name like "50"). This script follows that chain, so it is exact.

Writes, into the output folder, only the rows that belong to the chosen routes:
  routes.txt, trips.txt, stop_times.txt, stops.txt, shapes.txt, calendar.txt, calendar_dates.txt, frequencies.txt
(whichever of those exist in the feed). stop_times.txt is also sorted by trip and stop_sequence.

It also writes timetable_<routes>.xlsx: one sheet per route + direction + day type (Weekday / Saturday /
Sunday, from calendar.txt) with the stops listed in route order down the side and one column per trip
showing its scheduled time at each stop. A Summary sheet gives trips, first/last departure and headways,
and a Stops sheet lists the stop names and coordinates. Use --no-excel to skip it.

Usage
  python filter_gtfs_route.py path/to/gtfs_folder --routes 50
  python filter_gtfs_route.py path/to/gtfs_folder --routes 50 X49 -o gtfs_filtered

Requires: pandas, numpy, openpyxl
"""
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

CHUNK = 500_000


def read_csv(path, **kw):
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig", **kw)


DAY_COLS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HEAD = PatternFill("solid", fgColor="1F4E78")
TIME_FMT = "h:mm AM/PM"


def service_day_types(calendar):
    """{service_id: 'Weekday' | 'Saturday' | 'Sunday' | 'Weekend' | 'Mon,Tue,...'} from calendar.txt."""
    out = {}
    if calendar is None:
        return out
    for _, r in calendar.iterrows():
        f = [str(r.get(c, "0")).strip() == "1" for c in DAY_COLS]
        wk, sat, sun = f[:5], f[5], f[6]
        if all(wk) and not sat and not sun:
            lab = "Weekday"
        elif sat and not any(wk) and not sun:
            lab = "Saturday"
        elif sun and not any(wk) and not sat:
            lab = "Sunday"
        elif sat and sun and not any(wk):
            lab = "Weekend"
        else:
            lab = ",".join(a for a, x in zip(ABBR, f) if x) or "No regular days"
        out[r["service_id"]] = lab
    return out


def tsec(s):
    """GTFS 'HH:MM:SS' (hours may exceed 24) -> seconds, NaN if blank."""
    try:
        h, m, sec = str(s).split(":")
        return int(h) * 3600 + int(m) * 60 + int(sec)
    except (ValueError, AttributeError):
        return np.nan


def order_stops(seqs):
    """Master top-to-bottom stop order: the longest trip is the backbone; stops it lacks are slotted in
    after their predecessor in the trip they appear in (short-turns, relocated stops, etc.)."""
    order = list(max(seqs, key=len))
    seen = set(order)
    for sq in sorted(seqs, key=len, reverse=True):
        for k, sid in enumerate(sq):
            if sid in seen:
                continue
            pred = next((sq[j] for j in range(k - 1, -1, -1) if sq[j] in seen), None)
            order.insert(order.index(pred) + 1 if pred is not None else 0, sid)
            seen.add(sid)
    return order


def safe_sheet(name, used):
    name = re.sub(r"[\[\]:*?/\\]", "-", name)[:31] or "Sheet"
    base, k = name, 2
    while name.lower() in used:
        name = f"{base[:28]}_{k}"
        k += 1
    used.add(name.lower())
    return name


def fmt12(sec):
    if sec is None or pd.isna(sec):
        return None
    day, rem = divmod(int(sec), 86400)
    h, m = rem // 3600, rem % 3600 // 60
    suffix = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:{m:02d} {suffix}" + (" (+1 day)" if day else "")


def hms(sec):
    return "" if pd.isna(sec) else f"{int(sec // 3600) % 24}:{int(sec % 3600 // 60):02d}"


def route_key(r):
    m = re.match(r"(\d+)(.*)", str(r))
    return (0, int(m.group(1)), m.group(2)) if m else (1, 0, str(r))


def avg_gap_min(dep, windows):
    """Mean gap (minutes) between consecutive departures that fall inside the same window."""
    gaps = []
    for lo, hi in windows:
        # Count a clock-time departure once, even if multiple GTFS trip records
        # (e.g. overlapping/double route patterns) leave at that exact time.
        xs = sorted({x for x in dep if lo * 3600 <= x < hi * 3600})
        gaps += list(np.diff(xs) / 60.0)
    return float(np.mean(gaps)) if gaps else None


def build_timetables(path, routes, trips, st, stops, calendar, dedupe=True):
    """
    Write the timetable workbook. One sheet per route + direction + day type.
    With dedupe=True, trips that are identical at every stop (same headsign, same stops, same times) are
    merged into one column, e.g. the same schedule listed under two service IDs. Returns a small info dict.
    """
    short = dict(zip(routes["route_id"], routes["route_short_name"]))
    daytype = service_day_types(calendar)

    t = trips.copy()
    t["route"] = t["route_id"].map(short)
    t["daytype"] = t["service_id"].map(daytype).fillna("All days")
    if "direction" in t.columns and (t["direction"].str.strip() != "").any():
        t["dirlabel"] = t["direction"].str.strip().replace("", "All")
    elif "direction_id" in t.columns:
        t["dirlabel"] = "Dir " + t["direction_id"]
    else:
        t["dirlabel"] = "All"
    if "trip_headsign" not in t.columns:
        t["trip_headsign"] = ""

    stop_name = dict(zip(stops["stop_id"], stops["stop_name"])) if stops is not None else {}
    stop_ll = ({r["stop_id"]: (r.get("stop_lat", ""), r.get("stop_lon", "")) for _, r in stops.iterrows()}
               if stops is not None else {})

    s = st.assign(seq=st["stop_sequence"].astype(int),
                  sec=[tsec(a) if str(a).strip() else tsec(d) for a, d in zip(st["arrival_time"], st["departure_time"])])
    by_trip = {k: g.sort_values("seq") for k, g in s.groupby("trip_id")}

    wb = Workbook()
    summ = wb.active
    summ.title = "Summary"
    used = {"summary", "stops", "notes"}
    summary_rows = []
    info_out = {"sheets": 0, "dups_merged": 0, "near_dups": 0}

    rank = {"Weekday": 0, "Saturday": 1, "Sunday": 2}
    groups = sorted(t.groupby(["route", "dirlabel", "daytype"], sort=False),
                    key=lambda kv: (route_key(kv[0][0]), kv[0][1], rank.get(kv[0][2], 3), kv[0][2]))
    for (route, direction, dtype), gt in groups:
        gt = gt[gt["trip_id"].isin(by_trip)].sort_values("trip_id")
        if gt.empty:
            continue

        # ---- merge trips that are identical at every stop (e.g. same schedule under 2 service IDs) ---- #
        info = gt.set_index("trip_id")
        members = {}                                   # representative trip_id -> [all trip_ids it stands for]
        seen_sig = {}
        first_dep_keys = {}
        for tid in gt["trip_id"]:
            g = by_trip[tid]
            head = info.at[tid, "trip_headsign"] or g["stop_headsign"].iloc[0]
            sig = (head, tuple(zip(g["stop_id"], g["arrival_time"], g["departure_time"])))
            if dedupe and sig in seen_sig:
                members[seen_sig[sig]].append(tid)
            else:
                seen_sig[sig] = tid
                members[tid] = [tid]
            fk = (head, g["stop_id"].iloc[0], g["sec"].iloc[0])
            first_dep_keys.setdefault(fk, set()).add(sig)
        n_merged = len(gt) - len(members)
        info_out["dups_merged"] += n_merged
        info_out["near_dups"] += sum(1 for v in first_dep_keys.values() if len(v) > 1)
        rep_ids = list(members)

        seqs = {tid: list(by_trip[tid]["stop_id"]) for tid in rep_ids}
        order = order_stops(list(seqs.values()))
        first_stop = order[0]
        first_sec = {tid: by_trip[tid]["sec"].iloc[0] for tid in rep_ids}
        trip_order = sorted(rep_ids, key=lambda x: (first_sec[x] if not pd.isna(first_sec[x]) else 9e9, x))

        ws = wb.create_sheet(safe_sheet(f"{route} {direction} {dtype}", used))
        for r, lab in enumerate(("Trip ID", "Headsign", "Service ID"), start=1):
            ws.cell(r, 3, lab)
        ws.cell(4, 1, "Stop #"); ws.cell(4, 2, "Stop ID"); ws.cell(4, 3, "Stop")
        for c in (1, 2, 3):
            for r in range(1, 5):
                ws.cell(r, c).font = Font(bold=True, color="FFFFFF")
                ws.cell(r, c).fill = HEAD
        stop_row = {sid: 5 + i for i, sid in enumerate(order)}
        for sid, r in stop_row.items():
            ws.cell(r, 1, r - 4)
            ws.cell(r, 2, sid)
            ws.cell(r, 3, stop_name.get(sid, ""))

        depart_first = []
        for j, tid in enumerate(trip_order):
            col = 4 + j
            ids_here = members[tid]
            services = sorted({info.at[x, "service_id"] for x in ids_here})
            ws.cell(1, col, tid if len(ids_here) == 1 else f"{tid} (+{len(ids_here) - 1})")
            ws.cell(2, col, info.at[tid, "trip_headsign"] or by_trip[tid]["stop_headsign"].iloc[0])
            ws.cell(3, col, " + ".join(services))
            ws.cell(4, col, hms(first_sec[tid]))
            for r in range(1, 5):
                c = ws.cell(r, col)
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = HEAD
                c.alignment = Alignment(horizontal="center", wrap_text=True)
            for sid, sec in zip(by_trip[tid]["stop_id"], by_trip[tid]["sec"]):
                if pd.isna(sec):
                    continue
                cell = ws.cell(stop_row[sid], col, float(sec) / 86400.0)
                cell.number_format = TIME_FMT
            sub = by_trip[tid]
            hit = sub.loc[sub["stop_id"] == first_stop, "sec"]
            if len(hit) and not pd.isna(hit.iloc[0]):
                depart_first.append(float(hit.iloc[0]))
            ws.column_dimensions[get_column_letter(col)].width = 12 if len(ids_here) > 1 else 10
        ws.column_dimensions["A"].width = 7
        ws.column_dimensions["B"].width = 9
        ws.column_dimensions["C"].width = 36
        ws.freeze_panes = "D5"

        # Timetable columns remain trip-specific, but summary service counts and
        # headways count each distinct first-stop departure time only once.
        # This prevents simultaneous duplicate/overlapping GTFS patterns from
        # being added together as multiple departures.
        gtfs_trip_records = len(trip_order)
        dep = sorted(set(depart_first))
        heads = np.diff(dep) / 60.0 if len(dep) > 1 else np.array([])
        band = lambda lo, hi: sum(1 for x in dep if lo * 3600 <= x < hi * 3600)
        heads_pos = heads[heads > 0]
        head_counts = (pd.Series([info.at[x, "trip_headsign"] or by_trip[x]["stop_headsign"].iloc[0] for x in rep_ids])
                       .replace("", np.nan).dropna().value_counts())
        summary_rows.append({
            "ROUTE": route, "DIRECTION": direction, "DAY TYPE": dtype,
            "TRIPS": len(dep), "GTFS TRIP RECORDS": gtfs_trip_records,
            "DUPLICATES MERGED": n_merged,
            "FIRST STOP": stop_name.get(first_stop, first_stop),
            "FIRST DEPARTURE": fmt12(dep[0]) if dep else None, "LAST DEPARTURE": fmt12(dep[-1]) if dep else None,
            "MEDIAN HEADWAY (MIN)": float(np.median(heads_pos)) if len(heads_pos) else None,
            "AVG PEAK HEADWAY (MIN)": avg_gap_min(dep, [(6, 9), (15, 19)]),
            "AVG AM PEAK HEADWAY 6-9AM": avg_gap_min(dep, [(6, 9)]),
            "AVG PM PEAK HEADWAY 3-7PM": avg_gap_min(dep, [(15, 19)]),
            "TRIPS 5-9AM": band(5, 9), "TRIPS 9AM-3PM": band(9, 15), "TRIPS 3-7PM": band(15, 19),
            "TRIPS 7PM-MIDNIGHT": band(19, 24),
            "TRIPS OVERNIGHT": sum(1 for x in dep if x < 5 * 3600 or x >= 24 * 3600),
            "HEADSIGNS": ", ".join(f"{h} ({n})" for h, n in head_counts.items()),
        })
        info_out["sheets"] += 1

    # total trips for the route + day type across all directions
    totals = {}
    for r in summary_rows:
        totals[(r["ROUTE"], r["DAY TYPE"])] = totals.get((r["ROUTE"], r["DAY TYPE"]), 0) + r["TRIPS"]
    for r in summary_rows:
        r["TOTAL TRIPS (ALL DIRECTIONS)"] = totals[(r["ROUTE"], r["DAY TYPE"])]

    cols = ["ROUTE", "DIRECTION", "DAY TYPE", "TRIPS", "TOTAL TRIPS (ALL DIRECTIONS)",
            "GTFS TRIP RECORDS", "DUPLICATES MERGED",
            "FIRST STOP", "FIRST DEPARTURE", "LAST DEPARTURE", "MEDIAN HEADWAY (MIN)", "AVG PEAK HEADWAY (MIN)",
            "AVG AM PEAK HEADWAY 6-9AM", "AVG PM PEAK HEADWAY 3-7PM", "TRIPS 5-9AM", "TRIPS 9AM-3PM", "TRIPS 3-7PM",
            "TRIPS 7PM-MIDNIGHT", "TRIPS OVERNIGHT", "HEADSIGNS"]
    widths = [8, 12, 12, 8, 14, 14, 11, 34, 14, 14, 12, 12, 13, 13, 11, 11, 11, 13, 11, 48]
    for c, (h, w) in enumerate(zip(cols, widths), start=1):
        cell = summ.cell(1, c, h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEAD
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        summ.column_dimensions[get_column_letter(c)].width = w
    for r, row in enumerate(summary_rows, start=2):
        for c, h in enumerate(cols, start=1):
            v = row.get(h)
            cell = summ.cell(r, c, v)
            if "HEADWAY" in h and v is not None:
                cell.number_format = "0.00"
    summ.freeze_panes = "A2"

    if stops is not None:
        ws = wb.create_sheet("Stops")
        for c, h in enumerate(["STOP ID", "STOP NAME", "LAT", "LON"], start=1):
            cell = ws.cell(1, c, h)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = HEAD
        for r, sid in enumerate(sorted(set(st["stop_id"]), key=lambda x: (len(x), x)), start=2):
            lat, lon = stop_ll.get(sid, ("", ""))
            ws.cell(r, 1, sid); ws.cell(r, 2, stop_name.get(sid, ""))
            ws.cell(r, 3, float(lat) if lat != "" else None); ws.cell(r, 4, float(lon) if lon != "" else None)
        ws.column_dimensions["B"].width = 40
        ws.freeze_panes = "A2"

    ws = wb.create_sheet("Notes")
    for i, line in enumerate([
        "Each timetable sheet = one route + direction + day type. Day type comes from calendar.txt (weekday flags).",
        "Rows are stops in route order; columns are trips sorted by departure from the first stop. Row 1 = trip ID, "
        "row 2 = headsign, row 3 = service ID(s), row 4 = departure at the first stop.",
        ("Duplicate merging is ON: trips with the same headsign, stops and times at every stop (typically the same "
         "schedule under two service IDs / date ranges) appear as ONE column, shown as 'tripid (+1)' with both service "
         "IDs in row 3. TRIPS counts unique trips; DUPLICATES MERGED shows how many copies were folded in."
         if dedupe else
         "Duplicate merging is OFF (--keep-duplicates): identical trips under different service IDs each get a column."),
        "Blank cells mean the trip does not serve that stop (short-turns, shortened trips) or the feed gives no time.",
        "Times after midnight (GTFS hours of 24 and up) display as early-morning clock times in the grid and sort to the right; "
        "in the Summary they are labeled (+1 day).",
        "Headways use departures from the first stop in the sheet and ignore trips that don't serve it. "
        "AVG PEAK HEADWAY = mean gap between consecutive departures that fall inside the same window, pooled over "
        "6:00-8:59 AM and 3:00-6:59 PM (the gap between 9 AM and 3 PM is not counted). The AM and PM columns use one window each.",
        "TRIPS counts distinct first-stop departure clock times within that route/direction/day type. If multiple "
        "GTFS trip records leave at the exact same time, they count as one departure for summary counts and headways. "
        "Their separate timetable columns are retained when their stop patterns or times differ.",
        "TOTAL TRIPS (ALL DIRECTIONS) adds the distinct departure counts across directions for that route and day type.",
        "GTFS TRIP RECORDS shows the number of timetable trip columns before simultaneous departure times are collapsed "
        "for summary counts. DUPLICATES MERGED separately counts trips identical at every stop that were merged into one column.",
        "If a day type still has several service IDs with different schedules, all their trips appear together; check row 3.",
    ], start=1):
        ws.cell(i, 1, line)
    ws.column_dimensions["A"].width = 140
    wb.save(path)
    return info_out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gtfs_dir", type=Path, help="folder containing the unzipped GTFS .txt files")
    ap.add_argument("--routes", nargs="+", required=True, help="route_short_name values, e.g. 50 X49")
    ap.add_argument("-o", "--output", type=Path, default=None, help="output folder (default gtfs_<routes>)")
    ap.add_argument("--no-excel", action="store_true", help="skip the timetable workbook")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="timetable: do NOT merge trips that are identical at every stop (default merges them)")
    ap.add_argument("--period-date", metavar="YYYYMMDD", default=None,
                    help="keep only trips whose service_id (calendar.txt start_date..end_date) covers this date. "
                         "Use it when the feed lists the same weekday schedule under several service IDs / date ranges")
    args = ap.parse_args()

    src = args.gtfs_dir
    out = args.output or Path("gtfs_" + "_".join(args.routes))
    out.mkdir(parents=True, exist_ok=True)

    routes = read_csv(src / "routes.txt")
    want = {r.strip().upper() for r in args.routes}
    sel = routes[routes["route_short_name"].str.upper().isin(want)]
    if sel.empty:
        sample = sorted(routes["route_short_name"].unique())[:25]
        raise SystemExit(f"No route_short_name matched {sorted(want)}. Examples in this feed: {sample}")
    route_ids = set(sel["route_id"])
    sel.to_csv(out / "routes.txt", index=False)
    print(f"routes.txt: {len(sel)} route(s): " + ", ".join(sel["route_short_name"] + " " + sel.get("route_long_name", "")))

    trips = read_csv(src / "trips.txt")
    trips = trips[trips["route_id"].isin(route_ids)]

    cal_all = read_csv(src / "calendar.txt") if (src / "calendar.txt").exists() else None
    if cal_all is not None and not {"start_date", "end_date"} <= set(cal_all.columns):
        cal_all = None
    if args.period_date and cal_all is not None:
        d = args.period_date.replace("-", "")
        active = set(cal_all.loc[(cal_all["start_date"] <= d) & (cal_all["end_date"] >= d), "service_id"])
        before = len(trips)
        trips = trips[trips["service_id"].isin(active)]
        print(f"--period-date {d}: kept {len(trips)} of {before} trips (service IDs active on that date)")
        if trips.empty:
            raise SystemExit("No trips run on that date. Check the start_date/end_date values in calendar.txt.")
    elif args.period_date:
        print("--period-date ignored: calendar.txt (with start_date/end_date) not found")

    if cal_all is not None:
        dt = service_day_types(cal_all)
        rng = {r["service_id"]: f'{r["start_date"]}-{r["end_date"]}' for _, r in cal_all.iterrows()}
        per = {}
        for sid in sorted(set(trips["service_id"])):
            per.setdefault(dt.get(sid, "All days"), []).append(sid)
        for day, sids in per.items():
            if len(sids) > 1:
                print(f"NOTE: {day} has {len(sids)} service IDs for this route: "
                      + "; ".join(f"{x} ({rng.get(x, '?')})" for x in sids)
                      + ". If they are repeats of the same schedule, rerun with --period-date YYYYMMDD to keep one.")

    trip_ids, service_ids = set(trips["trip_id"]), set(trips["service_id"])
    shape_ids = set(trips["shape_id"]) if "shape_id" in trips.columns else set()
    trips.to_csv(out / "trips.txt", index=False)
    print(f"trips.txt: {len(trips)} trips")

    # stop_times can be hundreds of MB, so read it in chunks
    parts = [c[c["trip_id"].isin(trip_ids)] for c in
             pd.read_csv(src / "stop_times.txt", dtype=str, keep_default_na=False, encoding="utf-8-sig",
                         chunksize=CHUNK)]
    st = pd.concat(parts, ignore_index=True)
    st = st.assign(_s=st["stop_sequence"].astype(int)).sort_values(["trip_id", "_s"], kind="mergesort").drop(columns="_s")
    st.to_csv(out / "stop_times.txt", index=False)
    stop_ids = set(st["stop_id"])
    print(f"stop_times.txt: {len(st)} rows, {st['trip_id'].nunique()} trips, {len(stop_ids)} stops")

    stops_used = None
    if (src / "stops.txt").exists():
        stops = read_csv(src / "stops.txt")
        keep = stops["stop_id"].isin(stop_ids)
        if "parent_station" in stops.columns:
            keep |= stops["stop_id"].isin(set(stops.loc[keep, "parent_station"]) - {""})
        stops_used = stops[keep]
        stops_used.to_csv(out / "stops.txt", index=False)
        print(f"stops.txt: {int(keep.sum())} stops")

    calendar_used = None
    for name, col, ids in (("calendar.txt", "service_id", service_ids), ("calendar_dates.txt", "service_id", service_ids),
                           ("frequencies.txt", "trip_id", trip_ids)):
        if (src / name).exists():
            df = read_csv(src / name)
            df = df[df[col].isin(ids)]
            df.to_csv(out / name, index=False)
            print(f"{name}: {len(df)} rows")
            if name == "calendar.txt":
                calendar_used = df

    if shape_ids and (src / "shapes.txt").exists():
        parts = [c[c["shape_id"].isin(shape_ids)] for c in
                 pd.read_csv(src / "shapes.txt", dtype=str, keep_default_na=False, encoding="utf-8-sig", chunksize=CHUNK)]
        sh = pd.concat(parts, ignore_index=True)
        sh.to_csv(out / "shapes.txt", index=False)
        print(f"shapes.txt: {len(sh)} points, {sh['shape_id'].nunique()} shapes")

    if not args.no_excel:
        xl = out / ("timetable_" + "_".join(args.routes) + ".xlsx")
        info = build_timetables(xl, sel, trips, st, stops_used, calendar_used, dedupe=not args.keep_duplicates)
        print(f"{xl.name}: {info['sheets']} timetable sheet(s) (route x direction x day type)")
        if not args.keep_duplicates:
            print(f"  merged {info['dups_merged']} duplicate trips (identical at every stop) into single columns")
        if info["near_dups"]:
            print(f"  NOTE: {info['near_dups']} departures share a first-stop time and headsign but differ at later stops, "
                  f"so they were kept as separate trips")
        if calendar_used is None:
            print("  note: no calendar.txt found, so all trips are grouped as 'All days'")

    print(f"\nWrote filtered feed to {out}/")


if __name__ == "__main__":
    main()