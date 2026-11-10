#!/usr/bin/env python3
"""Create a minimal raw RAM disk for the mockfs mount-only checkpoint.

This is deliberately not an executable or an iOS image. The diagnostic
polaris-root-check=1 boot stops after a real mockfs mount, before exec.
"""
import argparse
import hashlib
import json
from pathlib import Path


def build():
    label = b'Polaris mockfs mount-only probe; no userland executable.\n'
    return label + bytes(4096 - len(label))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    args.output.with_suffix('.json').write_text(json.dumps({
        'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
        'purpose': 'mockfs mount-only checkpoint; no executable userland',
        'required_arguments': ['rd=md0', 'polaris-root-check=1'],
        'expected_checkpoint': 'POLARIS_ROOT_MOUNTED mockfs',
        'hardware_tested': False,
    }, indent=2) + '\n')
    print(f'{args.output}: {len(data)} byte mount-only RAM disk')


if __name__ == '__main__':
    main()
