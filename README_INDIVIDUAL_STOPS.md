# CTA Route 50 — Individual Stops and Leaflet Overlay

This update preserves the existing location-level consolidation analysis and adds an `Individual Stops` worksheet with one row for every analyzed Route 50 stop.

## Workbook changes

- `Individual Stops` contains 171 rows: 121 retained stops and 50 proposed removals.
- Each row keeps its route, direction, CTA stop ID, stop name, street/cross street, latitude, longitude, ridership averages, decision, location-level context, removal reason, sensitivity, and added-walk estimate.
- Red rows are proposed removals; green rows are retained; blue rows belong to protected locations; yellow rows are borderline.
- The stop IDs and coordinates are the model's analyzed stop records after its existing moved-stop ID reconciliation. For a reconciled ID, the coordinate is the latest observed coordinate used by the algorithm. The `Stop ID Changes` worksheet remains the audit trail for reconciliations.
- Some different stops may share an identical coordinate; rows are kept separate by route, direction, and stop ID rather than artificially jittering coordinates.

## Rebuild the workbook

From the consolidator folder:

```bash
python cta_stop_reduction.py raw_data.csv --routes 50 -o damen.xlsx
```

## Update Nik Hunder's Leaflet map

From the map repository root, with `openpyxl` and `beautifulsoup4` installed:

```bash
python build_integration.py --map index.html --workbook ../CTA_Bus_Stop_Consolidator-main/damen.xlsx
```

The script reads `Individual Stops` and creates 171 individual markers in the separate, toggleable `New Consolidation - Route 50` overlay. Nik's original map layers are retained. The overlay is hidden by default and can be enabled in the existing layer control.

## Marker colors

- Red: proposed stop removal
- Blue: stop at a protected location
- Yellow: borderline sensitivity result
- Green: retained stop

Click a marker to inspect its individual stop ID, direction, coordinates, ridership, decision, and reason.
