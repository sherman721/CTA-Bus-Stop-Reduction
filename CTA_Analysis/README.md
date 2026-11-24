# CTA stop consolidation analysis — Phase 1

Independent, reproducible data preparation for Route 50. Existing optimizer and GTFS sorter are untouched. Python 3.10+.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python prepare_data.py
```

On Windows activate with `.venv\Scripts\activate`. The three original input archives/workbook are included in `data/raw`. CSV outputs are regenerated in `outputs/`; open them in Excel or use them in Python. `validation.json` is the run audit. Do not publish raw inputs without confirming permission. This release does not estimate travel-time savings or claim the CTA scenario is fully reconciled.

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

## Next development stage

Reconcile directional stop IDs, verify actual CTA removals and relocations, confirm barrier crossings, verify passenger service rates, then implement shared redistribution and three travel-time models. No unmatched stop is silently assigned zero ridership. No scenario estimates should be published before reconciliation.

Phase 2: after `python prepare_data.py`, run `python reconcile.py`. It produces full-length trip tables, reconciled inventory, scenario stop probabilities, redistribution audit and scenario summaries. Travel-time models are not yet implemented. CTA relocation treatment is provisional; see methodology.

## Phase 3
After `python prepare_data.py` and `python reconcile.py`, run `python travel_time.py`. Outputs: travel_components.csv, travel_times.csv, travel_summary.csv. Passenger time and service analysis are not yet implemented.

Phase 4: run `python passenger_impacts.py` after `travel_time.py`. Outputs: passenger_impacts.csv and passenger_segments.csv. Requires NumPy.

Phase 5: run `python service_analysis.py` after passenger_impacts.py. See SOURCES.md and methodology for limitations.

### Phase 6
After the existing pipeline, run `python break_even.py` and `python field_stopping_check.py`. Requires SciPy in addition to prior dependencies. See outputs/break_even.csv, stopping_sensitivity.csv, field_stopping_check.csv and field_stopping_matched.csv. Primary outputs are not overwritten; Phase 6 reinvestment results supersede the Phase 5 fixed-ride approximation. Run from the toolkit directory.

### Phase 7
Run `python operating_analysis.py` after Phase 6. Produces `outputs/operating_resources.csv`: daily revenue mileage, theoretical resource hours, fixed-departure concurrency lower bounds and energy-intensity break-even thresholds. No actual fleet assignment or fuel savings is asserted.
