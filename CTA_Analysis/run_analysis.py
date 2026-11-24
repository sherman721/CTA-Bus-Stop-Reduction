#!/usr/bin/env python3
"""Run the complete core CTA analysis in dependency order."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEPS = ('prepare_data.py', 'reconcile.py', 'travel_time.py',
         'passenger_impacts.py', 'break_even.py', 'service_analysis.py',
         'field_stopping_check.py', 'operating_analysis.py', 'distributional_analysis.py')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=('permanent', 'scheduled'), default='permanent', help='Inventory for distributional outputs only')
    parser.add_argument('--cost-inputs', type=Path, help='Optional explicit cost configuration')
    args = parser.parse_args()
    steps = [(s, ['--scope', args.scope] if s == 'distributional_analysis.py' else []) for s in STEPS]
    if args.cost_inputs:
        path = args.cost_inputs.resolve()
        if not path.is_file():
            parser.error(f'Cost input file not found: {path}')
        steps.append(('optional_costs.py', ['--inputs', str(path), '--output', 'outputs/my_cost_results.json']))
    # Only remove known generated files; preserve unrelated user files.
    generated = ('barrier_review.csv', 'break_even.csv', 'canonical_sequence.csv', 'distributional_impacts.geojson', 'excluded_trips.csv', 'field_observations.csv', 'field_stopping_check.csv', 'field_stopping_matched.csv', 'field_summary.csv', 'full_schedule_summary.csv', 'full_stop_events.csv', 'full_trips.csv', 'operating_resources.csv', 'passenger_impacts.csv', 'passenger_segments.csv', 'phase2_validation.json', 'reconciled_inventory.csv', 'redistribution.csv', 'scenario_stops.csv', 'scenario_summary.csv', 'schedule_summary.csv', 'service_reinvestment.csv', 'stop_events.csv', 'stop_impacts.csv', 'stopping_sensitivity.csv', 'stops.csv', 'travel_components.csv', 'travel_summary.csv', 'travel_times.csv', 'trips.csv', 'unmatched_ridership.csv', 'validation.json', 'walking_distribution.csv')
    for name in (*generated, 'my_cost_results.json', 'run_status.json'):
        (ROOT / 'outputs' / name).unlink(missing_ok=True)
    (ROOT / 'outputs').mkdir(exist_ok=True)
    status = ROOT / 'outputs' / 'run_status.json'
    status.write_text('{"status":"incomplete"}')
    for i, (script, extra) in enumerate(steps, 1):
        print(f'\n[{i}/{len(steps)}] {script}', flush=True)
        try:
            subprocess.run([sys.executable, str(ROOT / script), *extra], cwd=ROOT, check=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f'Analysis stopped at {script}: {exc}. Outputs may be incomplete.', file=sys.stderr)
            return 1
    status.write_text('{"status":"complete"}')
    print(f'\nAnalysis complete. Results: {ROOT / "outputs"}', flush=True)
    return 0

if __name__ == '__main__':
    sys.exit(main())
