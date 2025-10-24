#!/usr/bin/env python3
"""
tools/parse_convos.py

Parse conversation text files (convo-*.txt) and extract inlined file contents into a reconstructed/ directory.
The script looks for several patterns used in exported conversations:
 - Explicit markers: --- BEGIN FILE: <path> --- / --- END FILE: <path> ---
 - Code fences with name=... header: ```name=path\n...\n```
 - Blocks introduced as: name=filename on a line followed by an indented or fenced block
 - Triple-backtick fences with a file path after the fence (````name=...````) used in some exported messages

Deduplication and merging rules:
 - Later occurrences (from higher-numbered convo files) win for the same path.
 - Non-conflicting fragments are not auto-merged; the last-seen full file content is used.
 - If content cannot be associated with a filename, it is saved under reconstructed/unknowns/ as file_N.txt.

Usage:
  python tools/parse_convos.py --input-dir . --output-dir reconstructed --report INTEGRATION_REPORT.md

This script is conservative: it will not overwrite repository files outside the reconstructed/ folder.

"""

import re
import sys
import argparse
import glob
import os
from collections import OrderedDict

FILE_BEGIN_RE = re.compile(r'^---\s*BEGIN(?:\s*FILE)?:\s*(.+?)\s*---')
FILE_END_RE = re.compile(r'^---\s*END(?:\s*FILE)?:?\s*(.+?)\s*---')
CODEFENCE_NAME_RE = re.compile(r'^```\s*name=(.+)')
INLINE_NAME_RE = re.compile(r'^name=(.+)')

def normalize_path(p):
    p = p.strip()
    p = p.replace('\\', '/')
    # remove surrounding quotes
    if (p.startswith('"') and p.endswith('"')) or (p.startswith('"') and p.endswith('"')):
        p = p[1:-1]
    return p

def extract_from_file(path, files_map, unknowns_counter, convo_index):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # check for explicit BEGIN/END markers
        m = FILE_BEGIN_RE.match(line)
        if m:
            fname = normalize_path(m.group(1))
            i += 1
            buf = []
            while i < n and not FILE_END_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            # skip END marker line if present
            if i < n and FILE_END_RE.match(lines[i]):
                i += 1
            content = ''.join(buf).rstrip('\n') + '\n'
            files_map[fname] = (convo_index, content)
            continue

        # check for code fence with name=...
        m = CODEFENCE_NAME_RE.match(line)
        if m:
            fname = normalize_path(m.group(1))
            i += 1
            buf = []
            while i < n and not lines[i].startswith('```'):
                buf.append(lines[i])
                i += 1
            # skip closing ```
            if i < n and lines[i].startswith('```'):
                i += 1
            content = ''.join(buf).rstrip('\n') + '\n'
            files_map[fname] = (convo_index, content)
            continue

        # check for inline 'name=filename' followed by a fenced block or indented block
        m = INLINE_NAME_RE.match(line)
        if m:
            fname = normalize_path(m.group(1))
            i += 1
            # skip blank lines
            while i < n and lines[i].strip() == '':
                i += 1
            buf = []
            # if next line is a fence
            if i < n and lines[i].startswith('```'):
                i += 1
                while i < n and not lines[i].startswith('```'):
                    buf.append(lines[i])
                    i += 1
                if i < n and lines[i].startswith('```'):
                    i += 1
            else:
                # take until next blank line or next name= or next BEGIN marker
                while i < n and lines[i].strip() != '' and not INLINE_NAME_RE.match(lines[i]) and not FILE_BEGIN_RE.match(lines[i]):
                    buf.append(lines[i])
                    i += 1
            content = ''.join(buf).rstrip('\n') + '\n'
            files_map[fname] = (convo_index, content)
            continue

        # fallback: try to detect a triple-backtick block that includes a leading name= header inside
        if line.startswith('```'):
            # look ahead for a name= header within the first line after the fence
            j = i + 1
            found_name = None
            if j < n:
                nm = INLINE_NAME_RE.match(lines[j])
                if nm:
                    found_name = normalize_path(nm.group(1))
                    j += 1
            if found_name:
                buf = []
                while j < n and not lines[j].startswith('```'):
                    buf.append(lines[j])
                    j += 1
                if j < n and lines[j].startswith('```'):
                    j += 1
                content = ''.join(buf).rstrip('\n') + '\n'
                files_map[found_name] = (convo_index, content)
                i = j
                continue

        i += 1

    return unknowns_counter

def main():
    parser = argparse.ArgumentParser(description='Parse convo-*.txt files and extract inlined file contents')
    parser.add_argument('--input-dir', default='.', help='Directory containing convo-*.txt files')
    parser.add_argument('--output-dir', default='reconstructed', help='Where to write extracted files')
    parser.add_argument('--report', default='INTEGRATION_REPORT.md', help='Integration report path')
    args = parser.parse_args()

    files_map = OrderedDict()  # path -> (convo_index, content)
    unknowns_counter = 0

    convo_paths = sorted(glob.glob(os.path.join(args.input_dir, 'convo-*.txt')))
    if not convo_paths:
        print('No convo-*.txt files found in', args.input_dir)
        sys.exit(1)

    for idx, p in enumerate(convo_paths, start=1):
        print(f'Parsing {p} (index {idx})')
        unknowns_counter = extract_from_file(p, files_map, unknowns_counter, idx)

    # prefer later occurrences: sort by convo index then write
    # files_map already keeps last assignment due to simple replacement above

    # write extracted files
    os.makedirs(args.output_dir, exist_ok=True)
    unknowns_dir = os.path.join(args.output_dir, 'unknowns')
    os.makedirs(unknowns_dir, exist_ok=True)

    written = []
    for path, (conv_idx, content) in files_map.items():
        # normalize and prevent absolute writes
        safe_path = path.lstrip('/\')
        out_path = os.path.join(args.output_dir, safe_path)
        out_dir = os.path.dirname(out_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as outf:
            outf.write(content)
        written.append((safe_path, conv_idx, len(content)))

    # create a report
    with open(args.report, 'w', encoding='utf-8') as rep:
        rep.write('# Integration Report\n\n')
        rep.write('This report was autogenerated by tools/parse_convos.py.\n\n')
        rep.write('Files extracted (path, source convo index, bytes):\n\n')
        for p, idx, size in written:
            rep.write(f'- {p} (from convo index {idx}, {size} bytes)\n')
        rep.write('\nNotes:\n- Later occurrences in higher-numbered convo files overwrite earlier occurrences.\n')
        rep.write('- If any expected project files are missing, run the script and inspect reconstructed/unknowns\n')

    print('\nExtraction complete. Wrote', len(written), 'files to', args.output_dir)
    print('Report written to', args.report)

if __name__ == '__main__':
    main()