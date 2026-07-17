#!/usr/bin/env python3

import argparse
import os
import re
import sys

try:
    import vobject
except ImportError:
    print("Error: vobject is required. Install with: pip install vobject", file=sys.stderr)
    sys.exit(1)

WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Leaves room for a "_NNN.vcf" suffix within common 255-byte filename limits.
MAX_BASE_BYTES = 120

VCARD_BLOCK = re.compile(
    r'^BEGIN:VCARD.*?^END:VCARD',
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)


def sanitize_filename(name):
    name = re.sub(r'[\x00-\x1f\x7f]', '', name)
    name = re.sub(r'[/\\:*?"<>|]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    name = name.strip('._')
    name = name.encode('utf-8')[:MAX_BASE_BYTES].decode('utf-8', errors='ignore')
    name = name.rstrip('._')
    if name.upper() in WINDOWS_RESERVED:
        name += '_'
    return name or "unnamed"


def flatten(value):
    if isinstance(value, (list, tuple)):
        return ' '.join(str(v).strip() for v in value if v)
    return str(value).strip() if value else ''


def get_contact_name(vcard):
    if hasattr(vcard, 'fn'):
        fn = flatten(vcard.fn.value)
        if fn:
            return fn
    if hasattr(vcard, 'n'):
        n = vcard.n.value
        parts = [flatten(n.given), flatten(n.additional), flatten(n.family)]
        name = ' '.join(p for p in parts if p)
        if name:
            return name
    return None


def unique_output_path(output_dir, base, used_lower):
    candidate = base
    counter = 1
    while (candidate.lower() in used_lower
           or os.path.lexists(os.path.join(output_dir, candidate + ".vcf"))):
        counter += 1
        candidate = f"{base}_{counter}"
    used_lower.add(candidate.lower())
    return os.path.join(output_dir, candidate + ".vcf")


def split_vcard(input_path, output_dir):
    with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    blocks = VCARD_BLOCK.findall(content)
    if not blocks:
        print("No vCard entries found.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    used_lower = set()
    written = 0
    skipped = 0

    for index, block in enumerate(blocks, start=1):
        try:
            vcard = vobject.readOne(block)
            name = get_contact_name(vcard) or "unnamed"
        except Exception as e:
            skipped += 1
            print(f"Warning: skipped entry {index}: {e}", file=sys.stderr)
            continue

        base = sanitize_filename(name)
        output_path = unique_output_path(output_dir, base, used_lower)

        try:
            with open(output_path, 'x', encoding='utf-8') as f:
                f.write(block + '\n')
        except OSError as e:
            skipped += 1
            print(f"Warning: skipped entry {index} ({name}): {e}", file=sys.stderr)
            continue

        written += 1
        print(os.path.basename(output_path))

    summary = f"\nExported {written} contact(s) to '{output_dir}'."
    if skipped:
        summary += f" Skipped {skipped} entr{'y' if skipped == 1 else 'ies'} (see warnings)."
    print(summary)

    if skipped:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Split a multi-entry vCard file into individual .vcf files."
    )
    parser.add_argument("input", help="Input .vcf file containing multiple vCards")
    parser.add_argument(
        "-o", "--output",
        default=".",
        metavar="DIR",
        help="Output directory (default: current directory)"
    )
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    split_vcard(args.input, args.output)


if __name__ == "__main__":
    main()
