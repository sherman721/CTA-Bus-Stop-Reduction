# CTA Bus Stop Combination Optimizer & Interactive Map

A research and screening tool for evaluating possible CTA bus-stop
consolidations. The project combines:

1.  A Python preprocessing and stop-cleanup module.
2.  A separate combination-search optimization engine.
3.  An Excel workbook for auditing decisions and comparing sensitivity
    runs.
4.  A Leaflet map integration that overlays the optimizer's stop-level
    results on the existing CTA Bus Stop Reduction map.
5.  GitHub Pages hosting for sharing the static interactive map.

> **Important:** This is a planning and screening model, not an official
> CTA recommendation or an automated service-change decision. Every
> proposed removal should be reviewed against field conditions,
> pedestrian access, accessibility, transfers, nearby destinations, and
> agency policy.

------------------------------------------------------------------------

## Contents

-   [Project layout](#project-layout)
-   [What each script does](#what-each-script-does)
-   [How the optimization works](#how-the-optimization-works)
-   [Pairing, solo stops, and direction
    handling](#pairing-solo-stops-and-direction-handling)
-   [Protection rules](#protection-rules)
-   [Spacing and ridership rules](#spacing-and-ridership-rules)
-   [Combination search and
    objective](#combination-search-and-objective)
-   [Interlining and special cases](#interlining-and-special-cases)
-   [Understanding the Excel
    workbook](#understanding-the-excel-workbook)
-   [Using the interactive map](#using-the-interactive-map)
-   [Map dot colors and popups](#map-dot-colors-and-popups)
-   [Running the optimizer](#running-the-optimizer)
-   [Updating the map from a new
    workbook](#updating-the-map-from-a-new-workbook)
-   [Publishing the map with GitHub
    Pages](#publishing-the-map-with-github-pages)
-   [Updating the public map](#updating-the-public-map)
-   [Limitations and recommended
    review](#limitations-and-recommended-review)

------------------------------------------------------------------------

## Project layout

A suggested working layout is:

``` text
CTA-Bus-Stop-Project/
│
├── optimizer/
│   ├── cta_stop_optimization.py
│   ├── cta_stop_cleanup.py
│   ├── raw_data.csv
│   ├── route50_optimized.xlsx
│   └── route50_optimized_removed_stops.csv
│
└── map-site/
    ├── index.html
    ├── my_consolidation_route50.json
    ├── build_integration.py
    ├── README.md
    └── .nojekyll
```

The names and folders can differ. The important requirements are:

-   `cta_stop_optimization.py` and `cta_stop_cleanup.py` must be in the
    same folder.
-   The optimizer needs the raw stop/ridership data CSV.
-   `build_integration.py` must be able to read the generated workbook.
-   The map's `index.html` and its companion assets/data files must be
    kept together.
-   The published GitHub Pages folder must contain `index.html` at its
    top level.

Do not publish the raw ridership dataset or private working files unless
you have confirmed that they are appropriate for public release.

## What each script does

### `cta_stop_cleanup.py` --- preprocessing and stop identity

This is the shared preprocessing dependency. It prepares the raw CTA
stop/pick data for analysis. Among other things, the project uses it to:

-   Load and normalize the source data.
-   Average boardings and alightings over the available weekday picks.
-   Standardize street and cross-street names for comparisons.
-   Merge verified stop-ID changes where the records indicate that a
    stop ID moved or was replaced.
-   Identify route order and direction-specific stop sequences.
-   Pair opposite-direction stops into a common intersection/location
    when the pairing rules are satisfied.
-   Calculate geographic distances used by the optimizer.

Keep this file in sync with the version supplied for the optimizer. The
optimization script imports its preprocessing functions; it is not a
standalone replacement for this module.

### `cta_stop_optimization.py` --- combination-search engine

This is the newer optimization engine. It is separate from the original
greedy `cta_stop_reduction.py` script so the two approaches can be
compared.

The optimizer:

1.  Builds route- and direction-specific stop sequences.
2.  Identifies protected stops and locations.
3.  Finds stops that are close enough to be consolidation candidates.
4.  Tests whether removing a candidate would leave an acceptable gap.
5.  Applies ridership and special-case rules.
6.  Explores multiple possible removal sequences rather than committing
    to the first locally attractive removal.
7.  Selects the best configuration found within the configured search
    width.
8.  Repeats the analysis for each requested sensitivity tolerance.
9.  Writes an Excel workbook and a stop-level CSV export.

### `build_integration.py` --- map overlay builder

This script reads the workbook's `Individual Stops` sheet and inserts
two additional Leaflet marker layers into the existing map:

-   `My Consolidation - Route 50`
-   `My Consolidation - Route 50 (5% tolerance)`

It also writes `my_consolidation_route50.json` beside the map. The JSON
contains the stop-level attributes used by the popups and is a companion
data export; the map integration script creates it automatically.

The integration script is designed to preserve the existing map and add
separate toggleable overlays. It edits `index.html` in place, so make a
backup before running it.

------------------------------------------------------------------------

## How the optimization works

### 1. Weekday ridership averages

The model uses the available weekday picks in the source dataset.
Boardings and alightings are averaged over the picks in which each stop
appears. Stops that appear in fewer picks should be interpreted with
that lower observation count in mind; the workbook includes pick-count
information where available.

Average total ridership is:

``` text
Average total riders = average boardings + average alightings
```

This is a stop-level average, not a count of unique passengers. A person
boarding and alighting at different stops may contribute to both stop
totals.

### 2. Candidate trigger: minimum spacing

A stop is considered for consolidation when its nearest relevant
neighbor is within the route type's minimum-spacing trigger.

For a local route, the default minimum is one-eighth of a mile:

``` text
1/8 mile = 660 feet
```

The sensitivity tolerance scales this candidate trigger:

    Tolerance   Local candidate trigger
  ----------- -------------------------
           0%                    660 ft
           5%                    693 ft
          10%                    726 ft
          15%                    759 ft

The tolerance is a way to test how the model behaves when the
minimum-spacing trigger is relaxed. It is not permission to exceed the
maximum resulting gap.

Rapid-route spacing uses its own defaults: 0.20 mile minimum and
one-third mile maximum. The minimum candidate trigger is
tolerance-adjusted; the maximum remains a hard limit.

### 3. Maximum resulting gap

For local routes, the default maximum resulting gap is **1,500 feet**.

The model tests the gap between the retained stops that would become
consecutive after a removal. This is important when multiple nearby
stops are removed: the relevant measurement is the final gap between the
remaining stops, not merely the distance from the candidate to one
neighbor.

The 1,500-foot maximum is a hard cap. **The tolerance percentage is not
applied to this maximum.**

A gap that already exceeded the maximum before the proposed removal may
be identified as a pre-existing barrier gap. This exception is intended
for existing discontinuities that may be caused by a river, expressway,
rail yard, or another physical barrier. It does not make every removal
next to a long gap acceptable: the candidate must still pass the
applicable checks on its other side and the ridership test.

### 4. Ridership protection and neighbor comparison

The model uses two different ridership concepts:

-   **Route-wide protection:** high-ridership locations can be protected
    based on their rank across the route.
-   **Local neighbor comparison:** for eligible middle-ranked locations,
    the candidate is compared with a lower-ridership adjacent location.

The carried-forward defaults are:

-   Protect the top 15% of route locations, subject to the minimum
    average-ridership threshold of 10 riders.
-   The bottom 50% of route locations automatically pass the
    neighbor-ridership test.
-   The remaining middle band must satisfy the local lower-neighbor
    comparison.
-   The low-volume exception remains available as configured in the
    engine.

Passing the ridership test does **not** guarantee removal. A candidate
must still pass spacing, protection, interlining, and other applicable
rules.

A stop described as `KEPT_RIDERSHIP_NOT_LOW_ENOUGH_VS_NEIGHBOR` is not
necessarily a high-ridership stop route-wide. It means that the location
was in the band subject to local comparison and did not meet the
comparison against its lower-ridership neighbor. Use `RANK_IN_ROUTE` to
understand its route-wide rank.

### 5. Removed ridership is redistributed

When a stop is removed, its ridership is transferred to adjacent
retained stops using the existing inverse-distance weighting approach.

This matters because the optimizer does not treat every removal as
independent. A retained neighbor that absorbs riders becomes more
difficult to remove later. The search therefore accounts for how one
consolidation changes the ridership context for subsequent candidates.

The workbook's `ADDED_WALK_FT` field represents the added walk distance
used by the model for the removed stop at the time it was removed. It
should not be mistaken for the final gap between the two retained stops
surrounding a series of removals.

------------------------------------------------------------------------

## Pairing, solo stops, and direction handling

### Pairing opposite-direction stops

Opposite-direction stops may be represented as one location for
coordinated location-level decisions and reporting. However, a pair must
represent the same physical intersection.

The updated pairing rule requires:

-   Matching normalized `STREET` names.
-   Matching normalized `CROSS_ST` names.
-   The required geographic proximity.

A distance-only match is not sufficient.

### Mismatched intersection names remain solo

If two opposite-direction records are nearby but their
street/cross-street names do not match, they are not silently combined.
They remain separate directional stops and are identified in the
workbook's `Pairing Review` sheet. The affected stop rows receive the
`PAIRING_MISMATCH_SOLO` review flag.

For example, a northbound record named `ASHLAND & BRYN MAWR` must not
automatically be grouped with a southbound record named
`CLARK & BRYN MAWR` merely because their coordinates are close.

### Direction-specific spacing and ridership

The optimizer evaluates each route direction independently:

-   Northbound spacing uses northbound stop coordinates and sequence
    order.
-   Southbound spacing uses southbound stop coordinates and sequence
    order.
-   Northbound ridership is not added to southbound neighbor ridership.
-   Southbound ridership is not added to northbound neighbor ridership.

A valid opposite-direction pair can still be represented as a common
location for reporting, but the directional stop records remain distinct
for directional spacing and neighbor-ridership calculations.

This prevents a stop on one side of a corridor from acting as a
substitute for a stop on the other side of the corridor.

------------------------------------------------------------------------

## Protection rules

The following protection mechanisms are carried forward by the
optimizer.

### Terminals

The first and last stop in a route direction are protected from removal.

### Rail, station, and transit-center protection

Stops whose street/cross-street text identifies a rail station,
terminal, or transit center are protected according to the
text-detection rules in the code.

### Crossing-route transfer protection

The model checks the broader source dataset for stops served by other
routes near the candidate. A nearby stop on a crossing route can trigger
transfer protection.

-   The default transfer radius is controlled by `--transfer-radius`.
-   The test uses the full input dataset even when only one route is
    selected for optimization.
-   Same-street routes are handled according to the
    `--overlap-transfers` setting.
-   Exact-coordinate stops in a contiguous interlined section are
    handled by the interline logic rather than being automatically
    protected as ordinary crossing-route transfers.

### Top-ridership protection

The top configured percentage of route locations is protected, subject
to the minimum-ridership threshold. Defaults are 15% and 10 average
riders.

### Manual protection file

A user-supplied protection file can list stops or locations that must
remain, such as hospitals, major destinations, or other locally
important stops not captured by the automatic rules. The exact accepted
format is defined by the script's `--protect-file` parser; use the
example/template supplied with the project or inspect the script's help
before preparing a custom file.

### Directional asymmetric exception

A stop on one side of a paired location may be removable while the other
side remains, when a protected stop in the same direction is within the
configured asymmetric distance (default carried forward as 400 feet) and
spacing still works.

The resulting one-sided decision is flagged as asymmetric. This is an
exception, not a general permission to remove one side of any pair.

------------------------------------------------------------------------

## Interlining and special cases

### Interlined sections

A contiguous interlined section is identified when at least two
consecutive stops in a direction have exact matching coordinates served
by the same other route.

Within such a section:

-   Shared stops remain eligible for ordinary consolidation.
-   The model does not automatically protect every shared stop as a
    transfer.
-   At least one shared stop must remain in each direction-specific
    overlap section.
-   A stop that would eliminate the final remaining shared stop is
    retained and can be identified as `KEPT_LAST_INTERLINE_TRANSFER`.

A single shared intersection by itself is not enough to classify a
stretch as an interlined section; ordinary transfer protection may still
apply.

### Moved stop IDs

The preprocessing step attempts to merge stop-ID changes when the
records support treating them as the same physical stop. The workbook
includes a `Stop ID Changes` sheet so merged and unmerged cases can be
audited. A possible ID change is not automatically merged if the street
names differ or the IDs overlap in a pick in a way that makes the match
ambiguous.

### Pre-existing barrier gaps

If an original route segment already exceeded the maximum spacing, the
model can mark a resulting gap as `PRE_EXISTING_BARRIER_GAP`. This is an
informational exception for an existing gap, not a claim that the gap is
desirable or accessible.

------------------------------------------------------------------------

## Combination search and objective

The optimizer uses a bounded **beam search** over alternative feasible
removal sequences.

A purely greedy process makes one locally attractive removal and then
proceeds from that decision. That can miss a better overall combination:
removing stop A first may prevent a better combination involving stops B
and C, even when B and C together produce a better network.

Beam search instead:

1.  Starts from the original set of retained stops.
2.  Generates feasible next-removal choices.
3.  Creates alternative network states for those choices.
4.  Scores the resulting states.
5.  Retains only the best configured number of states (the beam width).
6.  Repeats until no useful feasible removal sequence remains or the
    search limit is reached.

The default beam width is 12. Increasing it explores more alternative
states and can improve the search, but increases run time and memory
use.

### State scoring order

The search ranks candidate configurations in this order:

1.  **Maximize complete locations consolidated.**
2.  **Minimize cumulative passenger-weighted added walk.**
3.  **Minimize total ridership removed.**
4.  **Maximize total stop removals as a final tie-break.**

This prioritizes the number of whole locations consolidated, then favors
configurations that impose less passenger-weighted additional walking
and remove less ridership.

### Important limitation

Beam search is a bounded heuristic. It is not a mathematical proof that
the globally optimal network configuration has been found. A potentially
useful state can be discarded when it falls outside the retained beam. A
wider beam may find a better configuration, but there is no guarantee
that any particular width finds the global optimum.

The original greedy engine remains separate. Use it as a comparison
baseline rather than assuming the combination optimizer must always
produce the preferred answer.

------------------------------------------------------------------------

## Understanding the Excel workbook

The optimizer produces an `.xlsx` workbook and a companion
`_removed_stops.csv` file.

The workbook's key sheets include:

  -----------------------------------------------------------------------
  Sheet                               What it is for
  ----------------------------------- -----------------------------------
  `Notes`                             Run settings, thresholds, rules,
                                      warnings, and interpretation notes.
                                      Read this first.

  `Summary`                           Route-level totals and overall
                                      results.

  `All Locations`                     Location-level decisions and
                                      reasons.

  `Review Queue`                      Locations that warrant closer
                                      inspection, including sensitivity
                                      changes and exceptions.

  `Individual Stops`                  One record per analyzed
                                      route/direction/stop, including
                                      stop ID and its own coordinates.

  `Remaining Gaps`                    Distance between consecutive
                                      retained stops in each direction
                                      after the primary run.

  `Removed Stops`                     Stop-level removal details and
                                      added-walk information.

  `Pairing Review`                    Nearby opposite-direction records
                                      with mismatched intersection names
                                      that were left as solo stops.

  `Stop ID Changes`                   Stop-ID changes that were merged or
                                      left unresolved.
  -----------------------------------------------------------------------

Some sheet names may be omitted when there is no relevant data for a
particular run.

### Important fields

-   `STOP_DECISION`: primary-tolerance stop-level decision.
-   `LOCATION_DECISION`: location-level decision.
-   `LOCATION_REASON` / `REMOVAL_REASON`: explanation or rule associated
    with the decision.
-   `STOP_DECISION_AT_0PCT`, `STOP_DECISION_AT_5PCT`, etc.: outcome at
    each sensitivity tolerance.
-   `SENSITIVITY = BORDERLINE`: the decision changes across the
    requested tolerance runs.
-   `REVIEW_FLAGS`: additional warnings that should be checked manually.
-   `RANK_IN_ROUTE`: route-wide ridership rank.
-   `AVG_BOARDINGS`, `AVG_ALIGHTINGS`, `AVG_TOTAL`: average weekday
    ridership measures.
-   `ADDED_WALK_FT`: added walk measure used by the model at removal
    time; not the final gap between retained endpoints.
-   `PICKS_PRESENT`: number of source picks in which the stop appears.
-   `INTERLINED_ROUTES`: other routes sharing exact coordinates in a
    qualifying interlined section.

### Remaining Gaps flags

The `Remaining Gaps` sheet measures the distance between each pair of
consecutive retained stops in the same direction.

  -----------------------------------------------------------------------
  Flag                                Meaning
  ----------------------------------- -----------------------------------
  `OK`                                The resulting gap passes the
                                      configured spacing checks.

  `OVER_LIMIT`                        The resulting gap exceeds the hard
                                      maximum and is attributable to the
                                      proposed removals.

  `PRE_EXISTING_BARRIER_GAP`          The over-limit gap contains an
                                      original over-limit edge and is
                                      treated as a pre-existing
                                      barrier-gap exception.

  `OVER_MAX`                          The gap exceeds the maximum spacing
                                      check as represented by the
                                      workbook's current rule
                                      configuration.

  `UNDER_MIN`                         The retained stops are closer than
                                      the minimum-spacing threshold.
  -----------------------------------------------------------------------

Always inspect the actual gap distance, route direction, endpoint stop
IDs, and skipped-stop count---not just the flag.

------------------------------------------------------------------------

## Using the interactive map

The map is based on the existing Leaflet map project. The optimizer adds
its own overlays without replacing the original layers.

### Turn layers on and off

1.  Open the map in a browser.
2.  Find the layer control, normally in the upper-right corner.
3.  Expand the overlay list if it is collapsed.
4.  Toggle `My Consolidation - Route 50` to show the primary optimizer
    result.
5.  Toggle `My Consolidation - Route 50 (5% tolerance)` to show the 5%
    sensitivity result.
6.  Toggle either layer off to compare it with the original map or with
    the other optimizer run.

The primary and 5% layers are separate so you can compare which stop
decisions change when the minimum-spacing candidate trigger is relaxed.

### Click a dot

Click a marker to open its popup. The integration script includes these
fields when available:

-   Primary stop decision.
-   Stop ID and stop name.
-   Route direction.
-   Location-level decision and location name.
-   Decision reason.
-   Routes sharing the stop as part of interlining.
-   Removal reason.
-   Sensitivity status.
-   Review flags.
-   Decision at each tolerance.
-   Average boardings, alightings, and total riders.
-   Added walk in feet.
-   Coordinates.

The popup is intended to let you inspect the evidence behind an
individual marker without having to search the workbook for every stop.

### Compare layers carefully

The two optimizer overlays are alternative model outputs, not two
different categories of physical stop. A stop may be red in one
tolerance layer and green or yellow in another because its decision
changed between runs.

The base map's original layers are preserved. Their colors, symbols, and
meanings are controlled by the original map project; they should be
interpreted using that project's own legend and popup information. The
color key below applies specifically to the two `My Consolidation`
overlays added by `build_integration.py`.

------------------------------------------------------------------------

## Map dot colors and popups

The integration script assigns colors to the optimizer's circle markers
using this precedence:

  -----------------------------------------------------------------------
  Dot color               Hex color               Meaning in the
                                                  optimizer overlay
  ----------------------- ----------------------- -----------------------
  **Red**                 `#c62828`               The stop is marked
                                                  `REMOVED` for that
                                                  overlay's tolerance.

  **Blue**                `#1565c0`               The stop belongs to a
                                                  protected location.

  **Yellow / amber**      `#f9a825`               The stop is kept, but
                                                  its outcome changes
                                                  across sensitivity runs
                                                  (`BORDERLINE`).

  **Green**               `#2e7d32`               The stop is kept and is
                                                  not classified as
                                                  protected or
                                                  borderline.
  -----------------------------------------------------------------------

The color precedence is important:

1.  If the stop decision for that overlay is `REMOVED`, it is red.
2.  Otherwise, if its location is `PROTECTED`, it is blue.
3.  Otherwise, if its sensitivity is `BORDERLINE`, it is yellow/amber.
4.  Otherwise, it is green.

Therefore a protected stop that is removed in a particular overlay would
display red because the removal decision takes precedence. If the same
stop is retained in another overlay, it may display blue.

These colors are not ridership categories. A green dot does not mean low
ridership, and a red dot does not mean the stop has no riders. The popup
provides the ridership figures and decision details.

------------------------------------------------------------------------

## Running the optimizer

### Requirements

Recommended:

-   Python 3.10 or newer.
-   `pandas`
-   `numpy`
-   `openpyxl`

Install dependencies:

``` bash
python -m pip install pandas numpy openpyxl
```

### Basic Route 50 run

From the optimizer folder:

``` bash
python cta_stop_optimization.py raw_data.csv \
  --routes 50 \
  -o route50_optimized.xlsx \
  --tolerance 0 \
  --sensitivity 0 5 10 15
```

This runs Route 50 with 0% as the primary decision tolerance and
produces comparison results at 0%, 5%, 10%, and 15%.

### Run multiple routes or all routes

Specify multiple route identifiers after `--routes`:

``` bash
python cta_stop_optimization.py raw_data.csv \
  --routes 50 9 X49 \
  -o selected_routes.xlsx \
  --tolerance 0 \
  --sensitivity 0 5 10 15
```

To analyze all routes in the input file, omit `--routes`:

``` bash
python cta_stop_optimization.py raw_data.csv \
  -o all_routes.xlsx \
  --tolerance 0 \
  --sensitivity 0 5 10 15
```

Even when only one route is analyzed, the full source dataset is used
for crossing-route transfer detection.

### Increase search width

The default beam width is 12. To explore more alternative sequences:

``` bash
python cta_stop_optimization.py raw_data.csv \
  --routes 50 \
  -o route50_wider_search.xlsx \
  --beam-width 24 \
  --tolerance 0 \
  --sensitivity 0 5 10 15
```

A wider beam can take substantially longer. Compare runtime and results
before choosing a production setting.

### Check available options

Use:

``` bash
python cta_stop_optimization.py --help
```

This is the authoritative list of command-line options for the exact
script version you are running.

------------------------------------------------------------------------

## Updating the map from a new workbook

The map integration reads the `Individual Stops` sheet from the
generated workbook.

### 1. Back up the map

From the map repository folder:

``` bash
cp index.html index_before_optimizer_update.html
```

### 2. Install map-builder dependencies

``` bash
python -m pip install openpyxl beautifulsoup4
```

### 3. Build the overlays

Run the integration script from the map folder. For example, if
`optimizer` and `map-site` are sibling folders:

``` bash
python build_integration.py \
  --map index.html \
  --workbook ../optimizer/route50_optimized.xlsx
```

The script:

-   Reads Route 50 records from `Individual Stops`.
-   Requires the 5% sensitivity decision column.
-   Creates the primary and 5%-tolerance marker layers.
-   Adds both layers to the map's layer control and search
    configuration.
-   Writes `my_consolidation_route50.json` beside `index.html`.

The integration is designed to be repeatable: it removes the previous
copies of its own two overlays before inserting updated ones.

### 4. Test locally

Open `index.html` in a browser or run a simple local server from the map
folder:

``` bash
python -m http.server 8000
```

Then visit:

``` text
http://localhost:8000
```

Check that:

-   The map loads.
-   The original map layers still work.
-   Both `My Consolidation` overlays appear in the layer control.
-   Markers are at the expected locations.
-   Clicking markers opens the correct popups.
-   The primary and 5% layers show the expected different decisions.

If something is wrong, restore the backup:

``` bash
cp index_before_optimizer_update.html index.html
```

------------------------------------------------------------------------

## Publishing the map with GitHub Pages

GitHub Pages can host the Leaflet map as a static website. You do not
need a paid web host or a server-side Python runtime for the map itself.

The simplest arrangement is a dedicated public GitHub repository
containing the map files.

### 1. Create a GitHub repository

On GitHub:

1.  Sign in to your GitHub account.
2.  Click **New repository**.
3.  Give it a name, for example `CTA-Bus-Stop-Optimizer-Map`.
4.  Choose **Public** if you are using GitHub Free and want the normal
    public Pages workflow.
5.  Create the repository.

A project site URL follows this pattern:

``` text
https://YOUR-USERNAME.github.io/REPOSITORY-NAME/
```

For example, a repository named `CTA-Bus-Stop-Optimizer-Map` would
publish at:

``` text
https://YOUR-USERNAME.github.io/CTA-Bus-Stop-Optimizer-Map/
```

### 2. Put the map files in the repository

The publishing folder must contain `index.html` at its top level.
Include all files the map needs, not just the HTML file.

At minimum, include:

-   `index.html`
-   `my_consolidation_route50.json`
-   Any local JavaScript, CSS, images, or data files referenced by the
    map.
-   `README.md`
-   `.nojekyll` (an empty file; useful when publishing prebuilt static
    assets).

Do not upload the raw source ridership CSV, the optimizer's private
working files, or sensitive data simply because the map needs a small
output file. Publish only the files required by the map and the
information you intend to make public.

### 3. Upload using Git

If Git is installed, open Terminal in the folder containing the map
files:

``` bash
git init
git add .
git commit -m "Initial CTA stop optimizer map"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/CTA-Bus-Stop-Optimizer-Map.git
git push -u origin main
```

Replace `YOUR-USERNAME` with your GitHub username and use the repository
URL GitHub gives you.

If the repository already exists and has files or commits, follow
GitHub's instructions for connecting an existing local repository rather
than blindly repeating `git init` or `git remote add`.

You can also upload files through the GitHub website using **Add file →
Upload files**, although Git is more convenient for repeated updates.

### 4. Enable GitHub Pages

In the GitHub repository:

1.  Open **Settings**.
2.  Select **Pages** in the left sidebar.
3.  Under **Build and deployment**, set **Source** to **Deploy from a
    branch**.
4.  Select branch **main**.
5.  Select folder **/(root)**.
6.  Click **Save**.

GitHub will build and deploy the site. The Pages settings screen will
show the published URL when deployment is ready.

If you prefer to keep the repository's source code separate from the
published site, you can publish from a `/docs` folder instead. In that
case, put the complete website---including `index.html` and its required
assets---inside `docs/`, then select `main` and `/docs` in Pages
settings.

### 5. Open the published map

Visit the URL shown under **Settings → Pages**. It will normally have
this form:

``` text
https://YOUR-USERNAME.github.io/CTA-Bus-Stop-Optimizer-Map/
```

The first deployment can take a little time. If the site does not
appear, check the repository's **Actions** tab and the Pages settings
for build or deployment errors.

### 6. Keep the repository updated

Whenever you rebuild the optimizer results:

1.  Run the Python optimizer locally.
2.  Generate the new workbook.
3.  Run `build_integration.py` to update `index.html` and the JSON
    companion file.
4.  Test the map locally.
5.  Commit and push the updated map files:

``` bash
git add index.html my_consolidation_route50.json
git commit -m "Update Route 50 consolidation results"
git push
```

GitHub Pages will redeploy the site from the updated branch.

### What GitHub Pages does not do

GitHub Pages serves static files. It does not run
`cta_stop_optimization.py` on the website visitor's behalf and does not
provide a Python backend.

The workflow is:

``` text
Raw CTA data
    |
    v
Run Python optimizer on your computer
    |
    v
Excel workbook + removed-stop CSV
    |
    v
Run map integration script
    |
    v
Updated index.html + map JSON
    |
    v
Push map files to GitHub
    |
    v
GitHub Pages publishes the interactive map
```

If you eventually want visitors to choose routes, change tolerances, and
run the optimizer directly from the website, that would require a
separate backend or a browser-based reimplementation of the optimization
logic. GitHub Pages alone cannot run the Python script.

------------------------------------------------------------------------

## Limitations and recommended review

The optimizer is a structured screening model, but it cannot fully
represent every real-world consideration.

It does not inherently know:

-   Whether a stop is near a hospital entrance, senior housing, a
    school, or another important destination unless protected through
    the manual protection file or another explicit rule.
-   Exact sidewalk conditions, pedestrian barriers, crosswalks, signal
    timing, or safe crossing routes.
-   All Pace, Metra, or other non-CTA transfer opportunities unless they
    are represented in the input or manual protections.
-   Whether a straight-line or route-sequence distance corresponds to
    the actual walking path a passenger would take.
-   Temporary construction, stop closures, planned development, or
    operational constraints not represented in the source data.

A model result should therefore be reviewed using:

1.  The route and direction.
2.  The stop's own coordinates and street/cross-street identity.
3.  Its average boardings and alightings.
4.  Its route-wide ridership rank.
5.  Its same-direction neighbors.
6.  The final resulting gap between retained stops.
7.  Any protection, pairing, interline, or sensitivity flags.
8.  Local field conditions and important destinations.

Do not interpret `ADDED_WALK_FT` as the final gap between the retained
stops after a chain of removals. Use `Remaining Gaps` for the resulting
retained-stop spacing.

The most useful review cases are generally:

-   Stops marked `BORDERLINE`.
-   Stops with pairing or data-quality flags.
-   Stops near the maximum allowed resulting gap.
-   Locations where one direction is removed but the other remains.
-   Interlined sections with only one shared stop remaining.
-   Stops with low pick counts or unusual ridership patterns.
-   Any result that conflicts with local knowledge.

------------------------------------------------------------------------

## Credits and project lineage

The interactive map builds on the existing CTA Bus Stop Reduction
Leaflet project by Nik Hunder:

https://nikhunder.github.io/CTA-Bus-Stop-Reduction/

The optimizer is a separate analysis workflow that adds its own Route 50
result layers to that map. Preserve attribution and the original map's
license/terms when reusing or modifying its code and assets. Confirm the
source repository's license before redistributing modified copies.

## GitHub Pages references

-   GitHub Pages overview: https://docs.github.com/en/pages
-   Creating a GitHub Pages site:
    https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site
-   Configuring a publishing source:
    https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
