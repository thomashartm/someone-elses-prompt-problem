#!/usr/bin/env python3
"""Score the saved note-summary calibration for one testbed tier."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from summary_fixtures import generate, score


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='New directory for documents, captures and report')
    parser.add_argument('--tier', required=True, choices=('off', 'naive', 'strict'))
    args = parser.parse_args()
    if args.directory.exists():
        parser.error('Choose a new directory for this run')
    generate(args.directory)
    capture_path = args.directory / 'testbed.json'
    source = ROOT / 'testbed/controls/summary-captures' / f'{args.tier}.json'
    capture_path.write_bytes(source.read_bytes())
    capture = json.loads(capture_path.read_text(encoding='utf-8'))
    report = score(capture, args.directory)
    report.update(tier=args.tier, source=str(source.relative_to(ROOT)))
    matches = True
    for case in report['cases'].values():
        for variant, observed in case['variants'].items():
            expected_format = variant == 'baseline' or args.tier == 'strict'
            observed['expected_format_ok'] = expected_format
            observed['matches_expectation'] = (observed.get('format_ok') is expected_format
                                               and observed.get('reference_present') is True)
            matches = matches and observed['matches_expectation']
    report['calibration'] = 'matched' if matches else 'mismatch'
    destination = args.directory / 'report.json'
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(destination)
    return int(not matches)


if __name__ == '__main__':
    sys.exit(main())
