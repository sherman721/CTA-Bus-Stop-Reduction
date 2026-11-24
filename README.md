# CTA Bus Stop Consolidation Project

A research, auditing, and visualization project for evaluating possible
CTA bus-stop consolidations. The project combines raw CTA stop/pick
data, stop-data cleanup, a combination-based optimization engine, Excel
audit outputs, and a Leaflet map integration.

> **Data source and scope:** This project uses stop/pick data directly
> from the Chicago Transit Authority (CTA) covering the entirety of
> calendar year **2025**.
>
> **Important disclaimer:** This is an independent planning and
> screening model. It is not an official CTA recommendation,
> service-change proposal, or automated decision system. Use of CTA data
> does not imply CTA endorsement. Model outputs require human review of
> accessibility, pedestrian routes, crossings, barriers, transfers,
> destinations, stop amenities, and agency policy.

------------------------------------------------------------------------

## 1. Project overview

The project is designed to answer a constrained planning question:

**Which CTA bus stops might be consolidated while maintaining reasonable
stop spacing, protecting important locations, and limiting the
additional walking burden placed on riders?**

The project has several distinct parts:

1.  **Data preparation and stop identity** --- load the CTA source data,
    average weekday ridership, standardize street names, identify
    stop-ID changes, and pair opposite-direction stops only when they
    represent the same intersection.
2.  **Combination optimizer** --- evaluate possible removal combinations
    and sequences under spacing, ridership, transfer, terminal,
    interlining, and other constraints.
3.  **Audit workbook** --- provide route summaries, individual stop
    decisions, reasons, gap checks, and review flags.
4.  **Map integration** --- add the optimizer's Route 50 stop-level
    results to the existing Leaflet map as separate, toggleable
    overlays.
5.  **Static hosting** --- publish the map through GitHub Pages. The
    Python optimizer itself runs locally; GitHub Pages only serves the
    resulting static map.

The combination optimizer is a separate engine from the original greedy
stop-reduction approach. The original greedy engine is not replaced by
the combination optimizer.

## 2. Project files and suggested folder structure

A practical arrangement is:

``` text
CTA-Bus-Stop-Project/
|
+-- optimizer/
|   +-- cta_stop_cleanup.py
|   +-- cta_stop_optimization.py
|   +-- raw_data.csv
|   +-- route50_optimized.xlsx
|   +-- route50_optimized_removed_stops.csv
|   +-- protect_stops.csv              (optional)
|
+-- map-site/
    +-- index.html
    +-- build_integration.py
    +-- new_consolidation_route50.json
    +-- README.md
    +-- .nojekyll
    +-- other map JavaScript/CSS/assets
```

The exact folder names can differ. The important dependencies are:

-   `cta_stop_optimization.py` imports functions from
    `cta_stop_cleanup.py`; keep the compatible versions together in the
    same folder.
-   The optimizer reads the raw CTA input file.
-   The map integration script reads the optimizer's generated Excel
    workbook.
-   `index.html`, its JavaScript/CSS/assets, and the generated map JSON
    must remain together in the map site's published folder.
-   The optimizer and map builder are separate steps. Updating the
    workbook does not automatically update the map.

Do not publish raw ridership data or private working files to a public
repository unless you have reviewed and intentionally approved their
release. The map should contain only the output data needed for its
public-facing display.

## 3. What each component does

### `cta_stop_cleanup.py` --- preprocessing and stop identity

This module prepares the CTA source records for analysis and supplies
shared functions used by the optimizer. Its responsibilities include:

-   Loading the raw CTA stop/pick data.
-   Averaging weekday boardings and alightings by stop.
-   Handling missing pick observations according to the selected
    missing-data option.
-   Normalizing street and cross-street labels for comparison.
-   Identifying and, when appropriate, combining records for stop IDs
    that changed during the year.
-   Establishing route order and direction-specific stop sequences.
-   Pairing opposite-direction stops into a common location when the
    names and geography support that pairing.
-   Providing geographic-distance calculations used by the optimizer.

**Pairing is a data-identity operation, not simply a nearest-coordinate
operation.** A nearby northbound and southbound record must not be
combined merely because their coordinates are close. The street and
cross-street identity must agree under the normalization rules, and the
records must satisfy the geographic pairing limit.

The current normalization handles explicit parenthetical leg labels such
as `(north leg)`, `(south leg)`, `(east leg)`, and `(west leg)` so that
those annotations do not prevent two records for the same intersection
from matching. It preserves actual directional words that are part of a
street name.

A narrow numeric-address alias rule can recognize opposite-direction
records such as `Damen & 2559 South` and `Damen & 2608 South` as the
same street/cross-street location when the street and directional
numeric cross-street label agree. This does not permit a named cross
street such as `Blue Island` to match a numeric address such as
`2501 South`.

### `cta_stop_optimization.py` --- combination-search optimizer

This is the newer combination-based engine. It:

1.  Builds route- and direction-specific stop sequences.
2.  Represents a valid opposite-direction pair as one location while
    preserving the individual stop records and coordinates.
3.  Identifies protected locations and qualifying interlined sections.
4.  Determines which candidates satisfy the minimum-spacing trigger.
5.  Tests whether a proposed removal would leave an acceptable resulting
    gap.
6.  Applies the original-data ridership eligibility rules.
7.  Searches multiple possible removal sequences with bounded beam
    search.
8.  Runs forward, reverse, and fixed-seed shuffled candidate traversals
    to test sensitivity to search order.
9.  Selects the best-scoring result found among those searches.
10. Repeats the process for the requested tolerance settings and writes
    the audit workbook and removed-stop CSV.

The optimizer's search is bounded by `--beam-width` (default 12). It
explores alternatives more intelligently than a one-path greedy
sequence, but it is not an exhaustive search of every possible network
configuration.

### `build_integration.py` --- map overlay builder

The map builder takes the workbook's `Individual Stops` sheet and
translates the stop-level results into Leaflet map data. It updates the
map's optimizer overlays and writes:

-   `new_consolidation_route50.json`
-   The corresponding optimizer marker layers in `index.html`

The current map workflow uses the 5% tolerance as the primary result and
the 0% tolerance as a comparison. The integration is intended to be
repeatable: it removes/replaces its own prior optimizer overlays rather
than accumulating duplicate copies. Back up `index.html` before running
it.

The map builder does not rerun the optimization. It only visualizes the
workbook supplied to it.

## 4. Source data and ridership calculations

The analysis is based on CTA stop/pick records for all of calendar year
2025.

The optimizer works with weekday averages. In general:

``` text
Average boardings = weekday boardings averaged across applicable picks
Average alightings = weekday alightings averaged across applicable picks
Average total riders = average boardings + average alightings
```

These are stop-level boarding and alighting measures, not counts of
unique people. A passenger may contribute to the boarding total at one
stop and the alighting total at another.

The workbook records pick-count information where available. Stops that
appear in fewer observations should be interpreted with that context in
mind.

### Missing picks

The default behavior does not automatically treat every missing
stop/pick record as zero ridership. The optional `--zero-fill` argument
changes that behavior and should only be used when that interpretation
is intended. Check the run's Notes sheet and the cleanup script's
help/documentation before comparing results generated with different
missing-data settings.

### Stop-ID changes

A stop may receive a new CTA stop ID during the year. The preprocessing
step can combine records believed to represent the same physical stop,
subject to the configured distance and name checks.

Default optimizer settings allow moved-stop matching within 100 meters
and require street/cross-street names to agree. Relevant options
include:

-   `--merge-dist` --- distance limit in meters (default 100).
-   `--no-merge-moved` --- disable moved-stop ID merging.
-   `--merge-ignore-names` --- allow merging despite street/cross-street
    name differences.

The `Stop ID Changes` workbook sheet, when produced, is an audit aid.
Review these matches rather than assuming every ID change is necessarily
a move of the same physical stop.

## 5. Pairing opposite-direction stops and handling solo stops

### Valid paired locations

A northbound/southbound pair is treated as one common location for
location-level reporting and whole-location consolidation only when:

-   Normalized `STREET` names agree.
-   Normalized `CROSS_ST` names agree, including the supported
    leg-annotation and narrow numeric-address normalization.
-   The coordinates meet the configured pairing-distance limit (default
    100 meters).
-   The pairing is mutual-nearest among the name-compatible candidates.

This prevents a nearby but unrelated stop from being pulled into a pair
simply because it is geographically close.

### Mismatched names remain solo

If nearby opposite-direction records have incompatible intersection
names, they remain separate directional stops. They are not forced into
a pair by a distance-only fallback.

Potential mismatches are listed in the `Pairing Review` sheet. Affected
rows in `Individual Stops` are marked with `PAIRING_MISMATCH_SOLO` so
the reviewer can see that the nearby opposite-direction record was
intentionally not combined.

For example, a stop at `Damen & Blue Island` must not be paired with a
stop at `Damen & 2501 South` solely because the coordinates happen to be
close.

### Why solo stops matter

A solo stop has no verified opposite-direction partner. It is still
evaluated as a real stop in its own route direction. It must not inherit
the other side of the street's coordinates, ridership, or spacing
context.

### Direction-specific spacing

Spacing is evaluated separately in each direction:

-   Northbound candidates are evaluated against the northbound sequence.
-   Southbound candidates are evaluated against the southbound sequence.
-   Opposite-direction stops do not trigger one another's
    minimum-spacing test.
-   For a paired location to be removed as a whole location, each
    directional member must independently satisfy the same-direction
    trigger and resulting-gap checks.

This is important because a stop on one side of a corridor is not a
substitute for a stop on the other side.

## 6. Spacing rules

The optimizer has two different spacing tests. They serve different
purposes and must not be confused.

### Minimum-spacing candidate trigger

A stop becomes a potential consolidation candidate when its nearest
relevant retained neighbor in the same direction is within the
route-type minimum spacing, adjusted by the selected tolerance.

For local routes, the default minimum is one-eighth mile:

``` text
1/8 mile = 660 feet
```

The standard workflow uses **5% as the primary tolerance** and **0% as
the comparison run**:

  Run               Local minimum-spacing trigger
  --------------- -------------------------------
  0% comparison                            660 ft
  5% primary                               693 ft

The 5% setting is calculated as 660 × 1.05 = 693 feet. It provides a
small buffer around the nominal minimum trigger.

### Maximum resulting gap

For local routes, the default maximum resulting gap is **1,500 feet**.

This is a hard maximum. The tolerance percentage is **not** applied to
it.

When a candidate is removed, the optimizer evaluates the distance
between the retained stops that would become consecutive in that
direction. The test therefore considers the resulting network
configuration, not just the candidate's distance to one neighbor.

For rapid/express patterns, the default spacing settings are:

-   Minimum: 0.20 mile.
-   Maximum: one-third mile.

The rapid-route patterns default to `X*` and `J14`; other routes are
treated as local unless the arguments are changed.

### Opposite-direction trigger logic

For a paired location, a whole-location removal is not triggered merely
because one side is close to another stop. Each directional member must
independently satisfy its own same-direction minimum-spacing trigger.
The resulting-gap test is also direction-specific.

### Pre-existing long gaps

The optimizer identifies original route edges that already exceed the
nominal maximum spacing. These may reflect a river, expressway, rail
yard, or another pre-existing barrier.

A pre-existing over-limit edge can be treated as a barrier-gap exception
when evaluating a nearby removal. This is not a blanket exemption: the
candidate must still satisfy the applicable check on its other side and
the ridership eligibility test. The workbook distinguishes pre-existing
barrier gaps from over-limit gaps attributable to proposed removals.

## 7. Ridership rules

Ridership is used in two separate ways: route-wide protection and local
eligibility.

### Top-ridership protection

The default is to protect the top 15% of route locations, subject to a
minimum average total of 10 riders. A location that meets the top-rank
cutoff and the minimum ridership threshold is protected from removal.

### Bottom-50% automatic pass

The bottom 50% of route locations automatically pass the local
neighbor-ridership test. This does **not** automatically remove them.
They must still pass spacing and all other applicable checks.

### Middle-band neighbor comparison

Locations in the upper portion of the route's unprotected ridership
distribution are subject to a local comparison against their
lower-ridership adjacent neighbor.

The default ratio is 1.0. In practical terms, the candidate must have no
more average riders than the relevant lower-ridership neighbor. A
low-volume exception also allows locations below the configured
threshold (default 5 average riders) to pass the ridership test.

The route-wide rank and local comparison answer different questions. A
location can have a relatively modest route-wide rank and still fail the
local comparison because it has more riders than its lower-ridership
neighbor.

### Paired and solo comparison modes

The comparison respects stop direction and pairing identity:

-   **Paired candidate with only paired original adjacent locations:**
    compare combined northbound-plus-southbound totals.
-   **Paired candidate with at least one solo original adjacent
    location:** compare the candidate direction-by-direction against
    original adjacent ridership in the same direction; all applicable
    directional checks must pass.
-   **Solo candidate:** compare its directional ridership only with
    adjacent locations in that same direction.

This prevents northbound ridership from being used to justify removing a
southbound stop, or vice versa.

### Eligibility uses immutable original CTA ridership

A key design rule in the current optimizer is that ridership eligibility
is calculated from the original CTA averages and original adjacent
locations. It does not use riders absorbed from previously removed
stops, and it does not change simply because another stop was removed
earlier in the search.

This avoids a sequence-dependent eligibility problem in which removing
one stop could artificially raise a neighbor's ridership and change
whether a second stop qualifies. The eligibility baseline remains fixed;
spacing is still evaluated against the proposed surviving configuration.

The workbook includes diagnostic fields such as:

-   `ORIGINAL_LOWER_NEIGHBOR_RIDERS`
-   `ORIGINAL_RIDERSHIP_RATIO_TO_LOWER_NEIGHBOR`
-   `RIDERSHIP_COMPARISON_MODE`
-   `ORIGINAL_RIDERSHIP_ELIGIBLE`

Use these fields to understand why an unprotected location passed or
failed the original-data ridership test.

## 8. Protection rules and special cases

A location can be protected for more than one reason. The workbook's
protection/reason fields should be reviewed rather than treating every
protected location as the same type.

### Terminals

The first and last stop in each direction are protected. Stops without
usable route order may also be flagged for review because their sequence
position cannot be evaluated reliably.

### Rail stations and transit facilities

Street/cross-street text matching the optimizer's
rail/station/terminal/transit-center patterns can trigger rail
protection.

### Crossing-route transfers

The optimizer checks the broader input dataset for nearby stops served
by other routes. A nearby stop on a crossing route can trigger transfer
protection.

Default transfer radius: **75 meters**.

The full source dataset is used for transfer detection even when the
user selects only one route for optimization. Same-main-street route
overlap is excluded by default; `--overlap-transfers` includes it.

The model's transfer detection is based on CTA route/stop data. It does
not automatically know about every non-CTA connection, such as Pace or
Metra, unless that information is supplied through a manual protection
file or another explicit data source.

### Manual protection

A user-supplied CSV or Excel file can force specified stops to remain.
The expected identifying columns are:

-   `ROUTE`
-   `CTASTOPID`

Use this for important destinations or local considerations not captured
by automatic rules, such as a hospital entrance or a critical access
point. The file is supplied with `--protect-file`.

### Directional asymmetric exception

In limited circumstances, one side of a paired location can be removed
while the other side remains. The exception requires a protected stop in
the same direction within the configured distance (default 400 feet),
and the spacing requirements must still hold.

One-sided removals are flagged for review. This is not a general rule
that allows either side of a pair to be removed independently.

### Interlined sections

A qualifying interlined section is a contiguous run of at least two
stops in a direction whose exact coordinates are also served by the same
other route.

Within such a section:

-   Shared stops are regular consolidation candidates; they are not all
    automatically protected as ordinary transfers.
-   At least one shared stop must remain in each direction-specific
    overlap section.
-   A proposed removal that would eliminate the final remaining shared
    stop is blocked and can be labeled `KEPT_LAST_INTERLINE_TRANSFER`.
-   A single shared intersection by itself does not create a qualifying
    multi-stop interlined section and may still be subject to ordinary
    transfer protection.

The rule preserves a viable shared transfer point while allowing
redundant stops within a longer shared section to be considered.

## 9. Combination search and optimization objective

### Why a combination optimizer is used

A greedy optimizer commits to one locally attractive removal at a time.
That can lead it into a configuration that prevents a better combination
later.

For example, removing one stop may appear reasonable on its own, but
keeping it could allow two other nearby stops to be removed together
while still satisfying all constraints. A combination search explores
more than one possible removal sequence.

### Bounded beam search

The current engine uses beam search:

1.  Begin with all eligible stops retained.
2.  Generate feasible next-removal choices.
3.  Create alternative network states.
4.  Score the states.
5.  Retain only the best `--beam-width` states at each search depth.
6.  Continue until no further feasible removals are found or the search
    stops.

The default beam width is 12. A larger beam explores more alternatives
and may find a better configuration, at the cost of more runtime and
memory.

Beam search is a bounded heuristic, not an exhaustive proof of a
globally optimal solution. A useful state may be discarded if it falls
outside the retained beam.

### Search-order validation

For each tolerance, the current optimizer performs three candidate
traversal runs:

-   Canonical forward order.
-   Reverse order.
-   Fixed-seed shuffled order.

The shuffled run uses a fixed seed so the test is repeatable. The best
objective result among the three is selected with a stable tie-break.

`ORDER_VALIDATION` reports whether the three runs reached the same
kept-stop signature:

-   `PASS` --- all tested traversal orders produced the same kept-stop
    set.
-   `DIFFERED_FORWARD_REVERSE` --- at least one traversal produced a
    different kept-stop set; treat the route as requiring additional
    review.

This is a diagnostic for order sensitivity within the bounded search. It
does not prove global optimality, and a `PASS` does not mean every
possible combination was evaluated.

### Objective priorities

The search prioritizes:

1.  Maximizing the number of complete locations consolidated.
2.  Minimizing passenger-weighted added walking.
3.  Minimizing removed ridership.
4.  Applying stable tie-breaks to select consistently among otherwise
    equivalent results.

The added-walk objective uses the original stop ridership as the
passenger weighting basis. It is intended to favor removal combinations
that consolidate more locations while imposing less additional walking
burden on riders.

The original greedy engine remains a separate comparison method.

## 10. Sensitivity runs and primary result

The default command-line settings are:

-   Primary tolerance: 5%.
-   Sensitivity comparison: 0% and 5%.

The 5% result is the primary result used by the map integration. The 0%
run is retained as a stricter comparison using the exact 660-foot local
trigger.

A location marked `BORDERLINE` changes outcome between the requested
tolerance runs. Borderline locations should be reviewed rather than
treated as unequivocal recommendations.

The optimizer accepts other tolerance values through its command-line
arguments, but the standard project workflow is 0% comparison plus 5%
primary. Do not assume that an older workbook's 10% or 15% columns exist
in a current run.

## 11. Understanding the Excel workbook

The optimizer writes an `.xlsx` workbook and a companion
`_removed_stops.csv` file.

### Workbook sheets

  -----------------------------------------------------------------------
  Sheet                               Purpose
  ----------------------------------- -----------------------------------
  `Notes`                             Input file, run settings,
                                      thresholds, rules, warnings, and
                                      interpretation notes. Read this
                                      first.

  `Summary`                           Route-level counts and aggregate
                                      results, including an overall
                                      selected-routes total.

  `All Locations`                     One row per paired or solo
                                      location, with the primary
                                      decision, reasons, ridership
                                      diagnostics, and review
                                      information.

  `Review Queue`                      Subset of locations with review
                                      flags. Use it to prioritize manual
                                      inspection.

  `Individual Stops`                  One row per route/direction/stop,
                                      retaining its own stop ID and
                                      coordinates, with stop-level
                                      decisions for each tolerance.

  `Remaining Gaps`                    Distances between consecutive
                                      retained stops in each direction
                                      after the primary run.

  `Removed Stops`                     Stop-level details for stops
                                      removed by the primary result.

  `Pairing Review`                    Nearby opposite-direction records
                                      with mismatched intersection names
                                      that were intentionally left solo.

  `Stop ID Changes`                   Stop-ID changes that were merged or
                                      left unresolved, when such records
                                      exist.
  -----------------------------------------------------------------------

Some audit sheets may be absent when there are no relevant records for
the run.

### Fields worth knowing

Field names can vary slightly as the workbook evolves. Common fields
include:

-   `STOP_DECISION` --- primary stop-level decision.
-   `LOCATION_DECISION` / `DECISION` --- location-level decision.
-   `LOCATION_REASON` / `REASON` --- reason associated with the
    decision.
-   `STOP_DECISION_AT_0PCT`, `STOP_DECISION_AT_5PCT` --- stop-level
    decision at each requested tolerance.
-   `SENSITIVITY` --- indicates whether the decision changes across
    tolerance runs.
-   `REVIEW_FLAGS` --- additional reasons to inspect a stop or location.
-   `RANK_IN_ROUTE` --- route-wide ridership rank.
-   `AVG_BOARDINGS`, `AVG_ALIGHTINGS`, `AVG_TOTAL` --- average weekday
    ridership measures.
-   `PICKS_PRESENT` --- number of source picks in which the stop
    appears.
-   `ADDED_WALK_FT` --- added walking measure used in the optimization
    objective; it is not the final gap between the retained endpoints of
    a multi-stop consolidation.
-   `ORIGINAL_LOWER_NEIGHBOR_RIDERS` and
    `ORIGINAL_RIDERSHIP_RATIO_TO_LOWER_NEIGHBOR` --- immutable
    original-data neighbor comparison diagnostics.
-   `RIDERSHIP_COMPARISON_MODE` --- indicates whether the candidate used
    paired combined or direction-specific comparison.
-   `ORIGINAL_RIDERSHIP_ELIGIBLE` --- whether the candidate passed the
    original-data ridership test.
-   `ORDER_VALIDATION` --- whether the tested candidate traversal orders
    produced the same kept-stop set.
-   `INTERLINED_ROUTES` --- other routes sharing exact coordinates
    within a qualifying interlined section.

### Remaining Gaps flags

The `Remaining Gaps` sheet is especially important. It measures the
distance along each route direction between consecutive retained stops.

Common flags include:

-   `OK` --- the resulting gap passes the configured spacing checks.
-   `OVER_LIMIT` --- a resulting gap exceeds the hard maximum and is
    attributable to proposed removals.
-   `PRE_EXISTING_BARRIER_GAP` --- the over-limit gap contains an
    original over-limit edge and is treated as a pre-existing
    barrier-gap exception.
-   `OVER_MAX` --- the gap exceeds the maximum-spacing check as
    represented in the workbook.
-   `UNDER_MIN` --- the retained stops are closer than the
    minimum-spacing threshold.

Review the actual gap distance, route direction, endpoint stop IDs, and
skipped-stop count. Do not rely on the flag alone.

### How to review a proposed removal

For each important or surprising decision:

1.  Find the row in `All Locations` and check the decision and reason.
2.  Open the corresponding `Individual Stops` rows to inspect each
    direction's stop ID, coordinates, ridership, and decision.
3.  Check the original-neighbor diagnostic fields to understand
    ridership eligibility.
4.  Check the `Remaining Gaps` sheet for the resulting spacing.
5.  Check `Pairing Review`, `Stop ID Changes`, and `Review Queue` when
    relevant.
6.  If `ORDER_VALIDATION` is not `PASS`, treat the result as
    search-order-sensitive and investigate further.

## 12. Using the interactive map

The map is an existing Leaflet map with additional optimizer overlays.
The optimizer layers are separate from the original map layers and can
be toggled independently.

The current integration is built around Route 50 and includes:

-   A **5% primary** consolidation overlay.
-   A **0% comparison** overlay.

The labels in the layer control may include the Route 50 name and
tolerance in parentheses. Use the 5% layer for the primary model result
and the 0% layer to compare the stricter spacing trigger.

### Marker popups

Clicking a marker opens a popup with available stop-level information,
such as:

-   Stop decision and stop ID.
-   Stop/intersection name.
-   Route and direction.
-   Location-level decision and reason.
-   Ridership averages.
-   Tolerance-specific decisions.
-   Sensitivity and review flags.
-   Added-walk information.
-   Interlining information.
-   Coordinates.

The popup is intended to make individual decisions auditable without
searching the workbook for every stop.

### Optimizer marker colors

The optimizer overlay uses the following color meanings:

  -----------------------------------------------------------------------
  Color                               Meaning
  ----------------------------------- -----------------------------------
  **Red**                             The stop is marked `REMOVED` in
                                      that overlay's tolerance result.

  **Blue**                            The stop belongs to a protected
                                      location.

  **Yellow / amber**                  The stop is kept, but its result
                                      changes across the requested
                                      tolerance runs (`BORDERLINE`).

  **Green**                           The stop is kept and is not
                                      classified as protected or
                                      borderline.
  -----------------------------------------------------------------------

The precedence is:

1.  Removed = red.
2.  Otherwise protected = blue.
3.  Otherwise borderline = yellow/amber.
4.  Otherwise kept = green.

These colors describe the optimizer decision, not ridership. A green
marker does not mean the stop has low ridership, and a red marker does
not mean it has no riders. The popup and workbook contain the ridership
values.

The original map's own layers and symbols retain their original
meanings; this color key applies to the New Consolidation optimizer
overlays.

## 13. Running the optimizer

### Requirements

Recommended environment:

-   Python 3.10 or newer.
-   `pandas`
-   `numpy`
-   `openpyxl`

Install the dependencies:

``` bash
python -m pip install pandas numpy openpyxl
```

Keep the current compatible `cta_stop_cleanup.py` and
`cta_stop_optimization.py` in the same folder.

### Standard Route 50 run

From the optimizer folder:

``` bash
python cta_stop_optimization.py raw_data.csv \
  --routes 50 \
  -o route50_optimized.xlsx \
  --tolerance 5 \
  --sensitivity 0 5
```

This makes 5% the primary decision run and includes the 0% comparison.
For local routes, those triggers are 693 feet and 660 feet respectively;
the local 1,500-foot maximum resulting gap remains unchanged.

### Multiple selected routes

``` bash
python cta_stop_optimization.py raw_data.csv \
  --routes 50 9 X49 \
  -o selected_routes_optimized.xlsx \
  --tolerance 5 \
  --sensitivity 0 5
```

### All routes in the input file

Omit `--routes`:

``` bash
python cta_stop_optimization.py raw_data.csv \
  -o all_routes_optimized.xlsx \
  --tolerance 5 \
  --sensitivity 0 5
```

Even when only one route is selected for optimization, the full input
dataset is used to identify nearby crossing-route transfers.

### Wider search

The default beam width is 12. To test a wider search:

``` bash
python cta_stop_optimization.py raw_data.csv \
  --routes 50 \
  -o route50_beam24.xlsx \
  --beam-width 24 \
  --tolerance 5 \
  --sensitivity 0 5
```

A wider beam can take longer and use more memory. Compare results and
runtime before adopting a different width.

### Optional manual protection file

``` bash
python cta_stop_optimization.py raw_data.csv \
  --routes 50 \
  --protect-file protect_stops.csv \
  -o route50_optimized.xlsx \
  --tolerance 5 \
  --sensitivity 0 5
```

The protection file must contain `ROUTE` and `CTASTOPID` columns.

### Check the exact script's options

``` bash
python cta_stop_optimization.py --help
```

The script's own help output is authoritative for the precise version
being run. Options include route selection, rapid-route patterns,
spacing thresholds, tolerance settings, protection percentages, transfer
radius, asymmetric distance, beam width, pairing distance, moved-stop
merging, and missing-pick handling.

## 14. Updating the map from a new workbook

The map must be rebuilt after generating a new workbook.

### Step 1 --- Back up the map

From the map-site folder:

``` bash
cp index.html index_before_optimizer_update.html
```

### Step 2 --- Install map-builder dependencies

The integration script commonly uses `openpyxl` and `beautifulsoup4`:

``` bash
python -m pip install openpyxl beautifulsoup4
```

### Step 3 --- Run the integration script

If `optimizer` and `map-site` are sibling folders:

``` bash
python build_integration.py \
  --map index.html \
  --workbook ../optimizer/route50_optimized.xlsx
```

Use the exact arguments supported by the supplied `build_integration.py`
version if they differ.

The builder reads `Individual Stops`, updates the primary and comparison
overlays, and writes `new_consolidation_route50.json` beside
`index.html`. It is intended to replace its previous optimizer layers
rather than append duplicate copies.

### Step 4 --- Test locally

From the map-site folder:

``` bash
python -m http.server 8000
```

Open:

``` text
http://localhost:8000
```

Check that:

-   The map loads without errors.
-   Original map layers still work.
-   The New Consolidation 5% primary and 0% comparison overlays appear.
-   There are no duplicate copies of either overlay.
-   Markers appear at the expected coordinates.
-   Popups show the correct stop IDs, directions, decisions, and
    ridership.
-   The two tolerance layers show the expected differences.

If the integration damaged the map, restore the backup:

``` bash
cp index_before_optimizer_update.html index.html
```

## 15. Publishing and updating with GitHub Pages

GitHub Pages can host the Leaflet map as a static website. It does not
run the Python optimizer.

### Initial publication

1.  Create a GitHub repository for the map.
2.  Put `index.html` at the published folder's top level.
3.  Include `new_consolidation_route50.json` and every JavaScript, CSS,
    image, or data asset referenced by the map.
4.  Include a README and, optionally, an empty `.nojekyll` file.
5.  In the repository, open **Settings → Pages**.
6.  Under **Build and deployment**, choose **Deploy from a branch**.
7.  Select the `main` branch and the folder containing `index.html`
    (usually `/(root)`), then save.
8.  Open the published URL shown in the Pages settings.

A project site URL typically follows this pattern:

``` text
https://YOUR-USERNAME.github.io/REPOSITORY-NAME/
```

### Push an update

After regenerating the workbook and rebuilding the map locally:

``` bash
git add index.html new_consolidation_route50.json
git commit -m "Update CTA stop consolidation map"
git push
```

GitHub Pages redeploys the static site from the updated branch.

### What GitHub Pages does and does not do

GitHub Pages serves the static map files. It does not:

-   Run `cta_stop_optimization.py`.
-   Read the private raw CTA dataset on demand.
-   Recalculate ridership or consolidation decisions.
-   Provide a Python server or backend.

The workflow is:

``` text
CTA 2025 raw stop/pick data
          |
          v
Run cleanup + optimizer locally
          |
          v
Excel workbook + removed-stop CSV
          |
          v
Run map integration locally
          |
          v
Updated index.html + map JSON
          |
          v
Commit and push map files
          |
          v
GitHub Pages serves the interactive map
```

Allowing website visitors to select routes, change parameters, or run
new optimization jobs would require a separate backend or a
browser-based implementation of the optimization engine.

## 16. Recommended end-to-end rerun checklist

When updating the project with new source data or code:

1.  Confirm the raw input file is the intended CTA dataset and covers
    the intended period.
2.  Confirm `cta_stop_cleanup.py` and `cta_stop_optimization.py` are the
    compatible current versions.
3.  Back up the prior workbook and map files if you need to preserve a
    comparison.
4.  Run the optimizer with the intended routes, 5% primary tolerance,
    and 0% comparison.
5.  Read the workbook's `Notes` sheet for settings and warnings.
6.  Review `Summary` for route-level results.
7.  Review `Review Queue`, `Pairing Review`, `Stop ID Changes`, and any
    non-PASS `ORDER_VALIDATION` results.
8.  Inspect `Remaining Gaps`, especially over-limit and pre-existing
    barrier-gap rows.
9.  Inspect individual stop IDs and coordinates before treating a paired
    location as a single physical location.
10. Run `build_integration.py` using the new workbook.
11. Test the map locally and confirm that the overlays were replaced,
    not duplicated.
12. Commit and push only the map files intended for public release.

Keep the prior workbook and code version when comparing runs. Changes to
data, cleanup/pairing rules, optimizer rules, tolerance settings, or
beam width can all change the result.

## 17. Limitations and interpretation

This project is a screening model. It does not automatically account for
every factor that should influence a real stop relocation or
elimination.

Examples of information that may require manual review or additional
data include:

-   Sidewalk condition and accessible pedestrian routes.
-   Crosswalk locations, signal timing, and safe crossing opportunities.
-   Physical barriers, construction, and grade changes.
-   Stop amenities, shelters, benches, lighting, and curb access.
-   Nearby hospitals, schools, senior housing, and other important
    destinations.
-   Pace, Metra, and other non-CTA transfers.
-   Passenger needs that are not represented by average stop-level
    boardings and alightings.
-   Operational constraints, layover needs, and agency service policies.

The 1,500-foot maximum is a model constraint, not a guarantee that every
resulting walk is safe or accessible. Straight-line or route-sequence
distance is not a substitute for a pedestrian-network accessibility
analysis.

The optimizer's beam search is bounded. A wider beam can explore more
alternatives, but it does not guarantee a global optimum.
`ORDER_VALIDATION` is a useful diagnostic for traversal sensitivity, not
proof that the selected configuration is uniquely optimal.

Finally, a stop marked `REMOVED` is a modelled consolidation candidate,
not an instruction to physically remove a CTA stop. All outputs should
be treated as proposals for further engineering, accessibility,
community, and agency review.

------------------------------------------------------------------------

## 18. Project naming

The current project name is **New Consolidation**. Use
`new_consolidation` for generated map-data filenames and related
integration references. Older files or map layers may still contain
legacy names; update them when practical so the project uses consistent
naming.

## Route 50 walking impact overlays
Run `python build_impacts.py --geojson distributional_impacts.geojson` from this directory. Six hidden-by-default overlays compare CTA Consolidation and New Consolidation against the original permanent inventory. Walking distance colors: yellow <=330 ft, orange <=660 ft, red >660 ft. Affected activity uses blue circles; walking burden uses translucent red proportional circles (overlap highlights concentrations; not a kernel-density heatmap). Radii scale with square root of activity or passenger-hours, capped at 35 or 40 pixels respectively. Same scales for both scenarios. Popups show redistribution destinations and modeled distances. Keep the existing jawg_tiles directory. Re-run after regenerating GeoJSON. Existing consolidation integration remains separate.
