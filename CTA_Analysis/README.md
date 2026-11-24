# CTA stop consolidation analysis — one-command workflow

## Quick start

Python 3.10+. Install dependencies once: `python -m pip install -r requirements.txt`.

Run the complete core analysis from the toolkit folder:

```sh
python run_analysis.py
```

The runner executes all nine core scripts in dependency order, regenerates outputs, and stops on errors. It uses the same Python interpreter and toolkit working directory for every step. Allow several minutes for the break-even calculation. Known generated outputs are cleared before each full run; unrelated files are preserved. A failed run leaves partial results and `run_status.json` marked incomplete. Empty CSV outputs are truncated to zero bytes. No map HTML is modified. Optional cost analysis is skipped unless explicitly requested:

```sh
python run_analysis.py --cost-inputs my_inputs.json
```

Use `--scope scheduled` only to change the distributional inventory; permanent is the default. The individual commands below remain available for troubleshooting.

## Required inputs

Keep these paths relative to the analysis toolkit directory. The runner regenerates intermediate CSVs from these inputs; no manual extraction of the ZIP archives is required.

| Path | Contents and role |
|---|---|
| `data/raw/01_Consolidation.zip` | Completed optimizer workbook, based on CTA 2025 weekday stop-level boarding and alighting averages. |
| `data/raw/02_Field_Observations.xlsx` | Post-consolidation field observations of dwell, clearance and stopping events. |
| `data/raw/03_GTFS.zip` | September 2026 pre-consolidation route-filtered GTFS data. |
| `config.json` | Route, service date and model parameters. |
| `requirements.txt` | Python dependencies (installation only). |

### Consolidation archive

The archive must contain exactly one non-metadata file named `route50_optimized.xlsx`. The `Individual Stops` sheet must contain `ROUTE`, `DIRECTION`, `CTASTOPID`, `STOP_NAME`, `LAT`, `LON`, `AVG_BOARDINGS`, `AVG_ALIGHTINGS`, and `STOP_DECISION`. The route is filtered using `config.json`; stop keys must be unique by direction and ID. `REMOVED` identifies proposed removals. The workbook must contain both retained and removed stops. The optimizer, cleanup script and removed-stops CSV inside the archive are not executed or read by this pipeline.

### Field workbook

Observation sheets use seven columns in this order: direction, numeric stop ID, street, cross street, arrival time, measurement in seconds, notes. Column E header must be `Arrival Time`; column F must be `Dwell Time`, `Clearance Time`, or `Clearance`. Sheets with `Logging Sheet` in the name are excluded. Preserve Excel time cells, numeric measurements, blanks and notes; do not replace blanks with zero. Current quality-control rules include Route 50-specific trip notes. Field data were collected after CTA's consolidation, not before it.

### GTFS archive

The archive must contain exactly one non-metadata copy of each of `calendar.txt`, `calendar_dates.txt`, `trips.txt`, `stop_times.txt`, and `stops.txt`. Nested directories are allowed. Required trip fields include `route_id`, `service_id`, `trip_id`, `direction`, and `block_id`; the `direction` field is the route sorter's North/South label, not just standard GTFS `direction_id`. Stop times require `trip_id`, `stop_id`, `stop_sequence`, `arrival_time`, `departure_time`, and `shape_dist_traveled` (feet in this dataset). Stop coordinates come from `stop_lat` and `stop_lon`. Calendar tables determine active service on the configured date. The timetable workbooks, sorter script, shapes, routes and frequencies tables are not directly read. Use a compatible route-filtered feed, not an arbitrary unprocessed GTFS archive.

### Configuration and route-specific inputs

`config.json` currently selects route `50` and service date `20260922`. It also contains running speed, acceleration/deceleration, walking speed, door and passenger service times, roadway delay, layover, clearance distance, redistribution distance threshold and time periods. See `METHODOLOGY.md` for assumptions. Not every configuration key is an implemented switch.

The actual CTA Consolidation removal list is currently embedded in `reconcile.py` (`removed_cta`), along with Route 50 stop-ID aliases, Roosevelt exclusion and temporary Catalpa closure handling. These are additional scenario inputs, not read from the optimizer workbook. Changing the route number alone is not sufficient for another route: update and validate the workbook filename, scenario inventory, reconciliation rules and field QC as appropriate. Both consolidation scenarios are compared independently against Original.

Optional cost/energy analysis requires a separately populated JSON file based on `cost_inputs.template.json`, supplied with `--cost-inputs`. No monetary or consumption values are required for the core analysis. Literature PDFs are documentation sources, not runtime inputs. The map repository and basemap tiles are not runtime inputs either.

### Updating inputs

Replace the relevant raw file at the same path, preserve its required internal structure, review `config.json` and the route-specific reconciliation rules, then run `python run_analysis.py`. Review validation and output files before publishing. Raw files are not edited by the runner. Do not publish raw inputs without confirming permission. Outputs are generated intermediates/results, not additional manually supplied inputs.

## Outputs

- `stops`: directional stop ID, coordinates, 2025 weekday boarding/alighting means, optimizer decision.
- `trips`: active scheduled trip IDs, block, direction, start/end seconds after service-day midnight, runtime minutes, period, stop count, shape distance feet.
- `stop_events`: trip-level sequence, scheduled times, cumulative GTFS distance feet and exact directional ridership match.
- `canonical_sequence`: most common ordered stop-ID pattern per direction, not a substitute for actual trip sequences.
- `barrier_review`: canonical adjacent gaps over 1500 ft; candidates only, not confirmed barriers.
- `field_observations`: raw sheet and row, stop, direction, arrival, measurement, trip-start period, inclusion flag and QC reason.
- `field_summary`: pooled valid measurements by type and trip-start period, count, mean, median, sample SD, range (seconds).
- `schedule_summary`: physical trip count, distinct departures, average runtime and within-period headway. `other` combines disconnected windows, so its headway is not interpretable; use trip-level data for later analysis.
- `unmatched_ridership`: directional records not present on the selected service date.

## Pipeline stages and additional outputs

The master runner executes these scripts in order. Individual commands remain available for debugging; downstream stages require current upstream outputs.

| Script | Main additional outputs (in `outputs/`) |
|---|---|
| `prepare_data.py` | Normalized data and validation listed above |
| `reconcile.py` | Full-length trips, reconciled inventory, scenario stops, redistribution, scenario summaries and regenerated `phase2_validation.json` |
| `travel_time.py` | `travel_components.csv`, `travel_times.csv`, `travel_summary.csv` |
| `passenger_impacts.py` | `passenger_impacts.csv`, `passenger_segments.csv` |
| `break_even.py` | `break_even.csv`, `stopping_sensitivity.csv`; canonical frequency and passenger-impact calculations |
| `service_analysis.py` | `service_reinvestment.csv`; regular-Poisson reinvestment summary from alpha-zero sensitivity results |
| `field_stopping_check.py` | `field_stopping_check.csv`, `field_stopping_matched.csv` |
| `operating_analysis.py` | `operating_resources.csv`; physical resources and energy-intensity thresholds, not verified fleet assignments or fuel savings |
| `distributional_analysis.py` | `stop_impacts.csv`, `walking_distribution.csv`, `distributional_impacts.geojson` |

## Phase 8 — Optional cost and energy inputs
Core analysis ends with physical resource quantities; no financial or consumption assumptions are required. Copy `cost_inputs.template.json` to a separate input file and fill in method (`anchored`, `standalone`, `field`), scenario (`cta`, `proposed`), reinvest (true/false), annual equivalent weekdays, currency and source (document provenance and date for every supplied parameter). Enable only the modules you can support by replacing their null with an object containing the following keys:

| Module | Required keys |
|---|---|
| labor | paid_hours_saved_per_day, marginal_cost_per_paid_hour |
| vehicle_maintenance | marginal_nonenergy_maintenance_cost_per_mile |
| energy | unit, baseline_units_per_mile, scenario_units_per_mile, price_per_unit |
| stop_maintenance | net_annual_stop_maintenance_savings |
| capital | one_time_removal_cost, one_time_amenity_cost |

Run `python optional_costs.py --inputs my_inputs.json --output outputs/my_cost_results.json`. Missing inputs in enabled modules are errors; disabled modules are explicitly not evaluated. Zero is valid only when deliberately supplied. Use separate runs for propulsion types with appropriate route-wide weighted intensities, or separate fleet-specific resource data; do not sum full-route runs. No automatic annualization defaults, labor recovery assumptions, or aggregate cost totals. See methodology.

## Distributional analysis
Run `python distributional_analysis.py` after reconciliation. Outputs: stop_impacts.csv, walking_distribution.csv, distributional_impacts.geojson. Default permanent scope matches the map; `--scope scheduled` is available for comparison (absolute burden including temporary closure). Copy GeoJSON to map repository and run its separate build_impacts.py script.

## Audit corrections
`scenario_summary.csv` reports designated `stops`, `expected_interior_stops_per_trip` and `expected_total_stops_per_trip` (interior plus two mandatory terminals). The former ambiguous `expected_stops_per_trip` column is retired. Stop-level `probability` remains the raw demand-based Poisson probability; runtime calculations override terminal probabilities to one. `service_reinvestment.csv` now reports fully recalculated ride, walk, wait and net hours; the fixed-ride approximation column is retired. Run the full pipeline to refresh all dependent outputs. Fixed assumptions: 100% retention, inverse-distance redistribution, Poisson primary model, no confirmed barrier override, AM proxy for PM, and always-run break-even sensitivity; the corresponding config keys are not implemented switches.
