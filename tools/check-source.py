#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Validate repository text sources, symlinks, shell scripts, and safety guards."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
errors = []
count = 0

FORBIDDEN_IN_PACKAGING = [
    (re.compile(r'/run/user/1000\b'), 'hardcoded /run/user/1000'),
    (re.compile(r'\bSocketUser\s*=\s*pad\b'), 'hardcoded SocketUser=pad'),
    (re.compile(r'\brunuser\s+-u\s+pad\b'), 'hardcoded runuser -u pad'),
    (re.compile(r'7\.2\.0-tb378fc-wifi\+'), 'hardcoded legacy kernel version 7.2.0-tb378fc-wifi+'),
    (re.compile(r'GALLIUM_THREAD=false'), 'legacy GALLIUM_THREAD=false workaround'),
]

for p in sorted(ROOT.rglob('*')):
    rel = p.relative_to(ROOT)
    if not p.is_file() or any(x in rel.parts for x in ['.git', 'out', '__pycache__', 'inputs', 'private', 'cache']):
        continue
    count += 1
    raw = p.read_bytes()
    if b'\0' in raw:
        errors.append(f'{rel}: binary file in source tree')
        continue
    try:
        text = raw.decode('utf-8')
        if p.suffix == '.py':
            ast.parse(text, filename=str(rel))
        if p.suffix == '.json':
            json.loads(text)
    except (SyntaxError, UnicodeError, ValueError) as e:
        errors.append(f'{rel}: {e}')
        continue
    if b'\r\n' in raw:
        errors.append(f'{rel}: use LF line endings')
    if re.search(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----', text):
        errors.append(f'{rel}: private key content')
    if rel.parts[:2] == ('packaging', 'device-support') and p.name != 'source-files.json':
        for pattern, desc in FORBIDDEN_IN_PACKAGING:
            if pattern.search(text):
                errors.append(f'{rel}: {desc}')
    if p.suffix == '.md':
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if re.match(r'[a-z]+:|#', target):
                continue
            path = target.split('#', 1)[0]
            if not (p.parent / path).exists():
                errors.append(f'{rel}: missing Markdown target {target}')
    if sys.platform != 'win32' and (text.startswith('#!/bin/sh') or text.startswith('#!/system/bin/sh')):
        r = subprocess.run(['sh', '-n', str(p)], capture_output=True, text=True)
        if r.returncode:
            errors.append(f'{rel}: {r.stderr}')
    elif sys.platform != 'win32' and (text.startswith('#!/bin/bash') or text.startswith('#!/usr/bin/env bash')):
        r = subprocess.run(['bash', '-n', str(p)], capture_output=True, text=True)
        if r.returncode:
            errors.append(f'{rel}: {r.stderr}')

# Validate packaging/device-support/symlinks.json targets & relative-path hygiene
sym_path = ROOT / 'packaging/device-support/symlinks.json'
tree_root = ROOT / 'packaging/device-support/tree'
if sym_path.is_file():
    sym_map = json.loads(sym_path.read_text(encoding='utf-8'))
    for link_rel, target in sym_map.items():
        if target.startswith('/'):
            errors.append(f'symlinks.json: {link_rel} uses absolute target {target}; use relative path')
            continue
        resolved = (tree_root / link_rel).parent.joinpath(target).resolve()
        if not resolved.exists() or not str(resolved).startswith(str(tree_root.resolve())):
            errors.append(f'symlinks.json: {link_rel} -> {target} does not resolve inside tree/')

if errors:
    print('\n'.join(errors))
    raise SystemExit(1)
print(f'Source syntax, JSON, shell syntax, symlinks, and hygiene checks passed: {count} files')
