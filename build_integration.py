#!/usr/bin/env python3
"""Add the single 5%-primary CTA Route 50 New Consolidation layer to Nik's Leaflet map.

Run from the Nik repository root:
  python build_integration.py --map index.html --workbook ../CTA_Combination_Optimizer/route50_optimized.xlsx

Requires: openpyxl, beautifulsoup4
"""
import argparse
import html
import json
from pathlib import Path

from bs4 import BeautifulSoup
from openpyxl import load_workbook

LAYER = "New Consolidation - Route 50"
# Historical names are removed from the map's overlay/search/hide controls on rebuild.
OLD_LAYER_NAMES = {
    "New Consolidation",
    "New Consolidation (5% tolerance)",
    "New Consolidation - Route 50 (5% tolerance)",
    "My Consolidation - Route 50",
    "My Consolidation - Route 50 (5% tolerance)",
    "My Consolidation - Route 50 (5% primary)",
    "My Consolidation - Route 50 (0% comparison)",
    "New Consolidation - Route 50 (5% tolerance)",
    "New Consolidation - Route 50 (5% primary)",
    "New Consolidation - Route 50 (0% comparison)",
}


def records_from_workbook(path):
    wb = load_workbook(path, data_only=True, read_only=True)
    if "Individual Stops" not in wb.sheetnames:
        raise RuntimeError("Workbook needs an 'Individual Stops' sheet. Regenerate it with the updated optimizer.")
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
        decision = r.get("STOP_DECISION_AT_5PCT")
        if decision in (None, ""):
            raise RuntimeError(
                "The Individual Stops sheet has no STOP_DECISION_AT_5PCT column. "
                "Regenerate using --tolerance 5 --sensitivity 0 5."
            )
        # Export only the primary 5% decision. Older 0/10/15% columns, if present
        # in a legacy workbook, are deliberately not copied into the map data.
        out.append({
            "route": "50",
            "direction": r.get("DIRECTION"),
            "stop_id": str(r.get("CTASTOPID", "")),
            "stop_name": r.get("STOP_NAME"),
            "street": r.get("STREET"),
            "cross_street": r.get("CROSS_ST"),
            "stop_decision": decision,
            "location": r.get("LOCATION"),
            "location_decision": r.get("LOCATION_DECISION"),
            "reason": r.get("LOCATION_REASON"),
            "interlined_routes": r.get("INTERLINED_ROUTES"),
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
    if v is None or v == "":
        return "Not available"
    if isinstance(v, (int, float)):
        return f"{v:,.{digits}f}"
    return str(v)


def popup(r):
    fields = [
        ("Stop decision", r["stop_decision"]),
        ("Stop ID", r["stop_id"]), ("Stop", r["stop_name"]),
        ("Direction", r["direction"]),
        ("Location decision", r["location_decision"]), ("Location", r["location"]),
        ("Reason", r["reason"]), ("Interlined with", r["interlined_routes"]),
        ("Removal reason", r["removal_reason"]),
        ("Review flags", r["review_flags"]),
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
    return "<div style='min-width:230px;max-width:340px'><b>New Consolidation · Route 50</b><table>" + body + "</table></div>"


def marker_call(records):
    lat = [r["lat"] for r in records]
    lon = [r["lon"] for r in records]
    colors = []
    for r in records:
        if r["stop_decision"] == "REMOVED":
            color = "#c62828"
        elif r["location_decision"] == "PROTECTED":
            color = "#1565c0"
        elif r["sensitivity"] == "BORDERLINE":
            color = "#f9a825"
        else:
            color = "#2e7d32"
        colors.append(color)
    popups = [popup(r) for r in records]
    return {
        "method": "addCircleMarkers",
        "args": [lat, lon, 8, None, LAYER,
                 {"interactive": True, "className": "new-consolidation-route50", "stroke": True,
                  "color": colors, "weight": 2, "opacity": 1, "fill": True,
                  "fillColor": colors, "fillOpacity": 0.86},
                 None, None, popups, None, None, None, None]
    }


def patch_map(map_path, records):
    map_path = Path(map_path).resolve()
    raw = map_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw, "html.parser")
    tag = soup.find("script", attrs={"type": "application/json"})
    if not tag or not tag.string:
        raise RuntimeError("Could not locate the Leaflet htmlwidget JSON in index.html")
    payload = json.loads(tag.string)
    calls = payload["x"]["calls"]

    # Remove previously generated copies, including obsolete primary/comparison layers.
    calls[:] = [c for c in calls if not (
        c.get("method") == "addCircleMarkers"
        and len(c.get("args", [])) > 4
        and c["args"][4] in OLD_LAYER_NAMES | {LAYER}
    )]

    layer_idx = next((i for i, c in enumerate(calls) if c.get("method") == "addLayersControl"), None)
    if layer_idx is None:
        raise RuntimeError("Could not find addLayersControl in map widget calls")
    calls.insert(layer_idx, marker_call(records))

    for c in calls:
        method, args = c.get("method"), c.get("args", [])
        if method == "addLayersControl" and len(args) > 1:
            overlays = args[1]
            overlays[:] = [g for g in overlays if g not in OLD_LAYER_NAMES | {LAYER}]
            overlays.insert(0, LAYER)
        elif method == "addSearchFeatures" and args:
            args[0][:] = [g for g in args[0] if g not in OLD_LAYER_NAMES | {LAYER}]
            args[0].append(LAYER)
        elif method == "hideGroup" and args:
            args[0][:] = [g for g in args[0] if g not in OLD_LAYER_NAMES | {LAYER}]
            args[0].append(LAYER)

    tag.string = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    map_path.write_text(str(soup), encoding="utf-8")
    json_path = map_path.parent / "new_consolidation_route50.json"
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="index.html")
    ap.add_argument("--workbook", required=True)
    args = ap.parse_args()
    rows = records_from_workbook(args.workbook)
    json_path = patch_map(args.map, rows)
    print(f"Added {len(rows)} Route 50 markers to the single 5%-primary New Consolidation layer.")
    print(f"Updated companion JSON: {json_path}")
