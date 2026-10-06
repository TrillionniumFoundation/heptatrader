#!/usr/bin/env python3
"""Check local documentation links. No catalog/header/status synchronization gate.

Architecture and capability truth remain reviewed documents and executable
behavior tests, not a second generated completion/approval system.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FENCES = re.compile(r"(?ms)^\s*(```|~~~).*?^\s*\1\s*$")

def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors = []
    files = [root / 'README.md'] + sorted((root / 'docs').rglob('*.md'))
    for path in files:
        if path.is_symlink():
            errors.append(f'{path.name}: symbolic documentation source is not read')
            continue
        if not path.exists():
            errors.append(f'{path.name}: missing documentation entry')
            continue
        text = FENCES.sub('', path.read_text(encoding='utf-8'))
        for raw in LINK.findall(text):
            target = raw.strip().split(' "', 1)[0].strip('<>')
            parts = urlsplit(target)
            if parts.scheme or parts.netloc or not parts.path:
                continue
            relative = unquote(parts.path)
            resolved = (root / relative[1:] if relative.startswith('/') else path.parent / relative).resolve()
            if not resolved.is_relative_to(root):
                errors.append(f'{path.relative_to(root)}: link escapes repository: {raw}')
            elif not resolved.exists():
                errors.append(f'{path.relative_to(root)}: broken link: {raw}')
    return errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    errors = validate(args.root)
    for message in errors:
        print(message, file=sys.stderr)
    if not errors:
        print('Documentation links OK; no completeness or trading qualification implied.')
    return bool(errors)

if __name__ == '__main__':
    raise SystemExit(main())
