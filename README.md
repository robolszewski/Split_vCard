# split_vcard — User Documentation

Splits a single vCard file containing multiple contacts into individual `.vcf` files, one per contact, named after each contact. The tool never overwrites existing files and never modifies the input.

---

## Requirements

- Python 3.6+
- [vobject](https://eventable.github.io/vobject/) library

Install the dependency:

```bash
pip install vobject
```

---

## Usage

```
python split_vcard.py <input> [-o DIR]
```

### Positional Arguments

| Argument | Description |
|----------|-------------|
| `input`  | Path to the source `.vcf` file containing one or more vCard entries |

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `-o DIR`, `--output DIR` | `.` (current directory) | Directory where individual `.vcf` files will be written. Created automatically if it does not exist. |
| `-h`, `--help` | — | Show usage information and exit |

---

## Output

Each contact is written to its own `.vcf` file. The contact's original text is copied **verbatim** from the input — entries are not re-serialized, so property order, folding, and encoding parameters are preserved exactly.

The filename is derived from the contact's display name with spaces replaced by underscores.

Progress is printed to stdout as each file is written, followed by a summary line:

```
Jane_Doe.vcf
John_Smith.vcf
Alice_Wang.vcf

Exported 3 contact(s) to './contacts_out'.
```

---

## Duplicate and Collision Handling

Filenames are guaranteed unique. Before writing, each candidate name is checked against both the files written earlier in the same run **and** the files already present in the output directory. If the name is taken, an underscore and the next free sequential number are appended.

**Example** — three contacts all named "John Smith":

```
John_Smith.vcf
John_Smith_2.vcf
John_Smith_3.vcf
```

Notes:

- Existing files are never overwritten. Re-running the tool into the same directory produces additional numbered files (`John_Smith_4.vcf`, ...). This also means the input file itself can never be clobbered.
- Uniqueness is checked case-insensitively, so "john smith" and "John Smith" never collide even on case-insensitive filesystems (Windows, macOS).
- If a contact is genuinely named "John Smith 2" and a duplicate "John Smith" also wants that filename, whichever comes second is bumped to the next free number — both contacts are always preserved.

---

## Name Resolution

The tool resolves the contact name in the following order:

1. **`FN` field** (Formatted Name) — the preferred, human-readable display name present in most modern vCards.
2. **`N` field** (Structured Name) — if `FN` is absent or blank, the given name, additional name, and family name components are joined with spaces.
3. **Fallback** — if neither field yields a non-empty value, the contact is named `unnamed` (with duplicates following the same `_2`, `_3` pattern).

---

## Filename Sanitization

The following transformations are applied to produce a safe, portable filename:

| Input | Output |
|-------|--------|
| Control characters (`\x00`–`\x1f`, `\x7f`) | Removed |
| `/ \ : * ? " < > \|` | Removed |
| Whitespace (spaces, tabs, newlines) | Replaced with `_` |
| Leading or trailing `.` or `_` | Stripped |
| Names longer than 120 bytes (UTF-8) | Truncated |
| Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`) | Underscore appended (e.g. `CON_.vcf`) |

A name that reduces to an empty string after sanitization is treated as `unnamed`.

---

## Error Conditions

| Condition | Behavior |
|-----------|----------|
| Input file not found | Error to stderr, exit code 1, nothing written |
| No vCard entries found | Error to stderr, exit code 1, nothing written |
| A malformed entry fails to parse | Warning to stderr identifying the entry number; the entry is skipped and processing continues. Exit code is 1 if any entries were skipped. |
| Undecodable bytes in source file | Replaced with the Unicode replacement character (`�`) and processing continues |

Exit code 0 means every entry was exported successfully.

---

## Changelog

### v2 — 2026-07-17

- **Fixed:** duplicate contacts could overwrite a genuinely different contact whose name matched a generated suffix (e.g. "John Smith 2" vs. a second "John Smith"). Filenames are now guaranteed unique.
- **Fixed:** existing files in the output directory — including the input file itself — could be silently overwritten. The tool now never overwrites; re-runs add numbered files instead.
- **Fixed:** a single malformed entry aborted the whole run. Bad entries are now skipped with a warning and the rest export normally (exit code 1 signals partial success).
- **Changed:** output files now contain the contact's original text verbatim instead of a re-serialized version; contacts without any name now export as `unnamed.vcf` instead of failing.
- **Added:** filename hardening — control characters stripped, names truncated at 120 bytes, Windows reserved names (`CON`, `NUL`, ...) escaped, case-insensitive uniqueness.

### v1 — 2026-07-17

- Initial release: split multi-entry vCard file into per-contact `.vcf` files with sequential numbering for duplicates.

---

## Examples

```bash
# Split contacts.vcf into the current directory
python split_vcard.py contacts.vcf

# Split contacts.vcf into a subdirectory called 'out'
python split_vcard.py contacts.vcf -o out

# Use an absolute path for the output directory
python split_vcard.py /home/user/exports/all_contacts.vcf -o /home/user/exports/split
```
