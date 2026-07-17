# split_vcard — Design Document

---

## Problem Statement

Contacts managers and address book applications often produce a single monolithic `.vcf` file containing all contacts. Many tools, devices, and workflows require individual per-contact files. The goal is a small, dependency-light CLI tool that performs this split reliably on Linux.

---

## Requirements

1. Accept a single vCard file as input via a positional argument.
2. Write one `.vcf` file per contact to a configurable output directory.
3. Derive the output filename from the contact's display name.
4. Handle duplicate names deterministically with sequential `_2`, `_3` suffixes.
5. Produce filenames that are safe on Linux filesystems (and portable to Windows/macOS).
6. Never overwrite existing files and never modify the input file.
7. Survive malformed entries: skip and report them rather than aborting the run.

---

## Technology Choices

### Language: Python

- Broad availability on Linux distributions without compilation.
- The `vobject` library is the most mature pure-Python vCard parser and handles the encoding quirks common in real-world exports (Apple Contacts, Google Contacts, Outlook).
- Minimal boilerplate for file I/O and argument parsing with the standard library (`argparse`, `os`, `re`).

### vCard Parsing: hybrid approach

The file is first split into entries with a regex on `^BEGIN:VCARD ... ^END:VCARD` boundaries (anchored to line starts, so folded lines and encoded payloads cannot false-match). Each block is then parsed individually with `vobject.readOne()` — but **only to extract the contact name**. The output file receives the original block text verbatim.

This hybrid design has three benefits over parsing-and-reserializing the whole file:

1. **Per-entry error recovery.** `vobject.readComponents()` is a generator; a parse error mid-stream kills the iterator and aborts everything after it. With pre-split blocks, one broken entry is skipped with a warning while the rest export normally.
2. **Byte-faithful output.** `vobject.serialize()` normalizes property order, line folding, and encodings. Copying the raw block preserves each contact exactly as exported.
3. **Nameless contacts still export.** `serialize()` validates that `FN` is present (required by vCard 3.0) and raises on nameless entries; raw copying sidesteps this, so they export as `unnamed.vcf`.

Known limitation: vCard 2.1's `AGENT` property can embed a nested vCard. In practice these are encoded inline (escaped, not on their own lines) so the anchored regex is not confused; a pathological multi-line nested vCard would be split incorrectly. Accepted as out of scope.

---

## Design Decisions

### Name Field Priority: FN over N

vCard defines two name fields:

- **`FN`** (Formatted Name) — a single display string. Required in vCard 3.0+.
- **`N`** (Structured Name) — individual components: family, given, additional, prefix, suffix.

`FN` is preferred because it represents how the contact owner intended the name to appear. `N` is the fallback for older or malformed vCards that omit `FN`. If both are absent or blank, the contact is labelled `unnamed`.

vobject may return **lists** for `N` components when a field is multi-valued; the `flatten()` helper coerces lists and non-string values into joined strings before use, preventing a `TypeError` on such inputs.

### Filename Sanitization

Applied in order:

1. Remove ASCII control characters (`\x00`–`\x1f`, `\x7f`) — a null byte would otherwise raise `ValueError` at `open()`.
2. Remove `/ \ : * ? " < > |` — Linux only forbids `/` and NUL, but this wider set keeps names shell-friendly and portable to Windows/macOS filesystems.
3. Collapse whitespace to underscores (preserves readability of multi-word names).
4. Strip leading/trailing dots and underscores (avoids hidden files; also collapses hostile names like `..` to empty, which then becomes `unnamed` — this plus step 2 neutralizes path traversal from untrusted input).
5. Truncate to 120 UTF-8 **bytes** (cut at a character boundary), leaving headroom for a `_NNN.vcf` suffix under the common 255-byte filename limit.
6. Append `_` to Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`) so `CON.vcf` never gets created.

### Collision-Proof Naming

Earlier iterations counted occurrences of each base name in a dict, which had two overwrite bugs: a real contact named "John Smith 2" collided with the generated suffix for a duplicate "John Smith", and files already on disk (including the input file itself, with the default output directory `.`) were silently clobbered.

The current `unique_output_path()` instead tests each **candidate filename** against:

- the set of names written earlier in this run (compared lowercase, so case-insensitive filesystems can't collide), and
- the output directory on disk via `os.path.lexists()` (`lexists` so broken symlinks count as taken).

It increments the suffix until a free name is found. As a final race guard, files are opened with mode `'x'` (exclusive create), which fails rather than overwrites if the file appeared between the check and the write. Consequences: nothing is ever overwritten, re-runs produce additional numbered files, and the input file cannot be destroyed.

### Error Handling and Exit Codes

Each entry is processed in its own `try/except`: parse failures and write failures produce a warning on stderr naming the entry index, and processing continues. The summary reports both written and skipped counts. Exit code is 0 only if every entry exported; any skip yields exit 1 so scripts can detect partial success.

### Encoding

Input is read as UTF-8 with `errors='replace'` so mixed or broken encodings don't abort the run; output is written as UTF-8. Trade-off: undecodable bytes become `�` silently rather than failing loudly.

---

## Code

```python
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
```

---

## Testing

Verified against a fixture containing: two contacts named "John Smith", one named "John Smith 2", one malformed entry (property line without a colon), one nameless entry, and one named "CON". Results:

- First run: `John_Smith.vcf`, `John_Smith_2.vcf`, `John_Smith_3.vcf`, `unnamed.vcf`, `CON_.vcf` — all five distinct, no overwrites, malformed entry skipped with a warning and exit code 1.
- Second run into the same directory: five new files (`John_Smith_4.vcf`, `John_Smith_2_2.vcf`, ...) with the originals untouched.
- Output file content confirmed byte-identical to the corresponding input block.

---

## Revision History

### v2 — 2026-07-17 (post-review fixes)

A code review of v1 found two data-loss bugs, several robustness gaps, and one documentation inaccuracy. All were fixed in v2:

| # | Severity | Issue in v1 | Fix in v2 |
|---|----------|-------------|-----------|
| 1 | High | Duplicate numbering collided with real names: contacts "John Smith", "John Smith 2", "John Smith" produced `John_Smith_2.vcf` twice — the second silently overwrote the first. The counter tracked base-name occurrences, never the actual filename. | `unique_output_path()` tests each candidate filename against names written this run and files on disk, incrementing until free. |
| 2 | High | Files opened with mode `'w'` silently overwrote anything already in the output directory — including, with the default `-o .`, the input file itself if a contact's name matched it. Untrusted input controlled output filenames, making this the main security finding. | On-disk existence check (`os.path.lexists`) plus exclusive-create mode `'x'`; existing files are never touched, re-runs add numbered files instead. |
| 3 | Medium | One malformed entry aborted the entire run with a raw `ParseError` traceback and zero output, because `readComponents()` is a generator that dies mid-stream. | Input is pre-split into `BEGIN`/`END` blocks; each is parsed independently. Bad entries are warned and skipped; exit code 1 signals partial success. |
| 4 | Medium | vobject can return lists for `N` components, crashing `' '.join()` with `TypeError`. | `flatten()` helper coerces lists and non-strings. |
| 5 | Low | Windows reserved device names (`CON`, `NUL`, ...) passed through; case-insensitive filesystems could collide; control characters (e.g. `\x00`) survived sanitization; overlong names raised `OSError`. | Reserved names get a trailing underscore; uniqueness is compared lowercase; control characters stripped; names truncated to 120 UTF-8 bytes. |
| 6 | Doc | Documentation claimed output was preserved "exactly," but `serialize()` normalizes property order, folding, and encoding — and rejects nameless vCards outright. | Output now copies the original block text verbatim; vobject is used only for name extraction. Docs updated to match. |

Fixes were verified with a fixture exercising the collision trio, a malformed entry, a nameless entry, a contact named "CON", and a repeat run into a populated directory (see Testing above).

### v1 — 2026-07-17 (initial implementation)

Parse-and-reserialize design using `vobject.readComponents()` with occurrence-count duplicate numbering. Superseded by v2.

---

## Considered Alternatives

### Parse-and-reserialize with `vobject.readComponents()` (original design)

Rejected after review: a mid-stream parse error aborts all remaining entries, `serialize()` rejects nameless vCards and normalizes the output, and the occurrence-counting duplicate scheme allowed silent overwrites (see Collision-Proof Naming above).

### Custom vCard parser (no vobject)

A pure regex splitter alone can't extract names reliably — line folding, quoted-printable encoding, and charset parameters on `FN`/`N` need real parsing. vobject is retained for name extraction only.

### Overwrite or skip on filename collision

Skipping silently loses contacts; overwriting destroys data. Sequential numbering is the only option that guarantees every contact is preserved.
