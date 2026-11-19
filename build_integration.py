#!/usr/bin/env python3
"""Add individual CTA stop results as a separate Leaflet overlay.

Usage from the Nik repository root:
  python build_integration.py --map index.html --workbook ../CTA_Bus_Stop_Consolidator-main/damen.xlsx

Requires: openpyxl, beautifulsoup4
"""
import argparse, json, html
from pathlib import Path
from bs4 import BeautifulSoup
from openpyxl import load_workbook

GROUP = "My Consolidation - Route 50"


def records_from_workbook(path):
    wb = load_workbook(path, data_only=True, read_only=True)
    if "Individual Stops" not in wb.sheetnames:
        raise RuntimeError("Workbook needs an 'Individual Stops' sheet. Regenerate it with the updated cta_stop_reduction.py.")
    ws = wb["Individual Stops"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    out = []
    for row in rows:
        r = dict(zip(headers, row))
        if str(r.get("ROUTE", "")).strip() != "50":
            continue
        lat, lon = r.get("LAT"), r.get("LON")
        if lat is None or lon is None:
            continue
        out.append({
            "route": str(r.get("ROUTE", "50")),
            "direction": r.get("DIRECTION"),
            "stop_id": str(r.get("CTASTOPID", "")),
            "stop_name": r.get("STOP_NAME"),
            "street": r.get("STREET"),
            "cross_street": r.get("CROSS_ST"),
            "stop_decision": r.get("STOP_DECISION"),
            "location": r.get("LOCATION"),
            "location_decision": r.get("LOCATION_DECISION"),
            "reason": r.get("LOCATION_REASON"),
            "removal_reason": r.get("REMOVAL_REASON"),
            "sensitivity": r.get("SENSITIVITY"),
            "review_flags": r.get("REVIEW_FLAGS"),
            "avg_boardings": r.get("AVG_BOARDINGS"),
            "avg_alightings": r.get("AVG_ALIGHTINGS"),
            "avg_total": r.get("AVG_TOTAL"),
            "added_walk_ft": r.get("ADDED_WALK_FT"),
            "rank_in_route": r.get("RANK_IN_ROUTE"),
            "lat": float(lat), "lon": float(lon),
        })
    return out


def fmt(v, digits=1):
    if v is None or v == "": return "Not available"
    if isinstance(v, (int, float)): return f"{v:,.{digits}f}"
    return str(v)


def popup(r):
    fields = [
        ("Stop decision", r["stop_decision"]), ("Stop ID", r["stop_id"]),
        ("Stop", r["stop_name"]), ("Direction", r["direction"]),
        ("Location decision", r["location_decision"]), ("Location", r["location"]),
        ("Reason", r["reason"]), ("Removal reason", r["removal_reason"]),
        ("Sensitivity", r["sensitivity"]), ("Review flags", r["review_flags"]),
        ("Average boardings", fmt(r["avg_boardings"])),
        ("Average alightings", fmt(r["avg_alightings"])),
        ("Average total riders", fmt(r["avg_total"])),
        ("Added walk (ft)", fmt(r["added_walk_ft"])),
        ("Coordinates", f'{r["lat"]:.6f}, {r["lon"]:.6f}'),
    ]
    body = "".join(
        f"<tr><th style='text-align:left;padding:2px 8px 2px 0'>{html.escape(str(k))}</th>"
        f"<td>{html.escape(str(v if v not in (None, '') else '—'))}</td></tr>"
        for k, v in fields
    )
    return "<div style='min-width:230px;max-width:320px'><b>My Consolidation · Route 50</b><table>" + body + "</table></div>"


def patch_map(map_path, records):
    raw = Path(map_path).read_text(encoding="utf-8")
    soup = BeautifulSoup(raw, "html.parser")
    tag = soup.find("script", attrs={"type": "application/json"})
    if not tag or not tag.string:
        raise RuntimeError("Could not locate the Leaflet htmlwidget JSON in index.html")
    payload = json.loads(tag.string)
    calls = payload["x"]["calls"]
    calls[:] = [c for c in calls if not (c.get("method") == "addCircleMarkers" and len(c.get("args", [])) > 4 and c["args"][4] == GROUP)]
    lat = [r["lat"] for r in records]
    lon = [r["lon"] for r in records]
    colors = [
        "#c62828" if r["stop_decision"] == "REMOVED" else
        ("#1565c0" if r["location_decision"] == "PROTECTED" else
         ("#f9a825" if r["sensitivity"] == "BORDERLINE" else "#2e7d32"))
        for r in records
    ]
    popups = [popup(r) for r in records]
    marker_call = {
        "method": "addCircleMarkers",
        "args": [lat, lon, 8, None, GROUP,
                 {"interactive": True, "className": "my-consolidation-route50", "stroke": True,
                  "color": colors, "weight": 2, "opacity": 1, "fill": True,
                  "fillColor": colors, "fillOpacity": 0.86},
                 None, None, popups, None, None, None, None]
    }
    layer_idx = next((i for i, c in enumerate(calls) if c.get("method") == "addLayersControl"), None)
    if layer_idx is None:
        raise RuntimeError("Could not find addLayersControl in map widget calls")
    calls.insert(layer_idx, marker_call)
    for c in calls:
        method, args = c.get("method"), c.get("args", [])
        if method == "addLayersControl" and len(args) > 1 and GROUP not in args[1]: args[1].append(GROUP)
        elif method == "addSearchFeatures" and args and GROUP not in args[0]: args[0].append(GROUP)
        elif method == "hideGroup" and args and GROUP not in args[0]: args[0].append(GROUP)
    tag.string = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    Path(map_path).write_text(str(soup), encoding="utf-8")
    Path("my_consolidation_route50.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="index.html")
    ap.add_argument("--workbook", required=True)
    args = ap.parse_args()
    rows = records_from_workbook(args.workbook)
    patch_map(args.map, rows)
    print(f"Added {len(rows)} individual Route 50 stop markers in the '{GROUP}' overlay.")
