#!/usr/bin/env python3
"""
validate_symbols.py
-------------------
Validates KiCad 9 .kicad_sym library files against a configurable set of
required symbol properties.

KiCad 9 stores libraries in S-expression format.  Each symbol looks like:

  (symbol "U_Something"
    ...
    (property "Reference"   "U"  ...)
    (property "Value"       "SomeIC" ...)
    (property "Footprint"   "Package_SO:SOIC-8" ...)
    (property "Datasheet"   "https://..." ...)
    (property "Description" "8-bit MCU" ...)
    (property "MPN"         "ATtiny85-20PU" ...)
    (property "Manufacturer" "Microchip" ...)
    (property "IPN"         "IC-0042" ...)
    ...
  )

Exit codes
----------
  0  – all symbols pass (or --warn-only is set)
  1  – one or more violations found AND --warn-only is NOT set
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Configuration ────────────────────────────────────────────────────────────

# Canonical list of required property names.
# Change here OR override via --required-fields CLI argument.
DEFAULT_REQUIRED_FIELDS: list[str] = [
    "Reference",
    "Value",
    "Footprint",
    "Datasheet",
    "Description",
    "MPN",
    "Manufacturer",
    "IPN",          # ← rename this to match your actual internal field name
]

# Symbols whose Reference prefix appears in this set are treated as
# "power symbols" and are exempt from the full property check.
# KiCad ships PWR_FLAG, VCC, GND, etc. as power symbols.
POWER_PREFIXES: frozenset[str] = frozenset({"#PWR", "#FLG", "PWR", "#"})

# Regex that matches a non-empty, non-placeholder value.
# A property whose value is exactly "~" is KiCad's way of marking it blank.
_BLANK_VALUE_RE = re.compile(r'^\s*$|^~$')

# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Violation:
    library: str
    symbol: str
    missing_fields: list[str] = field(default_factory=list)
    empty_fields:   list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = []
        if self.missing_fields:
            out.append(
                f"  ✗ missing properties: {', '.join(self.missing_fields)}"
            )
        if self.empty_fields:
            out.append(
                f"  ✗ empty properties:   {', '.join(self.empty_fields)}"
            )
        return out


# ── S-expression parser (minimal, single-pass) ────────────────────────────────

def _unescape(s: str) -> str:
    """Remove surrounding quotes and unescape KiCad string literals."""
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    return s


def _tokenise(text: str):
    """
    Yield tokens from an S-expression string.
    Tokens are: '(', ')', or a string atom (quoted or bare).
    This is a hand-rolled lexer because the standard `sexpdata` library is not
    available in the GitHub Actions ubuntu runner without extra install steps,
    and we want zero external dependencies.
    """
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
        elif c == '(':
            yield '('
            i += 1
        elif c == ')':
            yield ')'
            i += 1
        elif c == '"':
            # quoted string – respect escaped quotes
            j = i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2          # skip escaped char
                elif text[j] == '"':
                    j += 1
                    break
                else:
                    j += 1
            yield text[i:j]
            i = j
        else:
            # bare atom (no spaces, no parens, no quotes)
            j = i
            while j < n and text[j] not in ' \t\r\n()\"':
                j += 1
            yield text[i:j]
            i = j


def parse_symbol_properties(lib_text: str) -> dict[str, dict[str, str]]:
    """
    Return {symbol_name: {property_name: property_value}} for every
    top-level symbol in the library.

    The KiCad 9 grammar relevant here is:
      (kicad_symbol_lib
        (symbol "<name>"
          ...
          (property "<key>" "<value>" ...)
          ...
          (symbol "<name>_0_1" ...)   ← sub-symbol, skip for property purposes
        )
      )

    Sub-symbols (units / body styles) inherit their parent's properties, so we
    only inspect the top-level (depth-1) symbol nodes.
    """
    tokens = list(_tokenise(lib_text))
    symbols: dict[str, dict[str, str]] = {}

    i = 0
    depth = 0                    # paren depth relative to file root
    current_symbol: Optional[str] = None
    current_symbol_depth: int = 0

    while i < len(tokens):
        tok = tokens[i]

        if tok == '(':
            depth += 1
            # Look ahead: is the next atom "symbol" at depth 1 (top-level)?
            if (depth == 2
                    and i + 2 < len(tokens)
                    and tokens[i + 1] == 'symbol'
                    and current_symbol is None):
                name = _unescape(tokens[i + 2])
                # Skip sub-symbols (contain '_' followed by digits at the end)
                # KiCad names them "ParentName_unitIndex_bodyStyle"
                if not re.search(r'_\d+_\d+$', name):
                    current_symbol = name
                    current_symbol_depth = depth
                    symbols[name] = {}

            # Look ahead: is this a "property" node directly inside a top-level
            # symbol (depth == current_symbol_depth + 1)?
            elif (current_symbol is not None
                    and depth == current_symbol_depth + 1
                    and i + 3 < len(tokens)
                    and tokens[i + 1] == 'property'):
                key   = _unescape(tokens[i + 2])
                value = _unescape(tokens[i + 3])
                symbols[current_symbol][key] = value

            i += 1

        elif tok == ')':
            if current_symbol is not None and depth == current_symbol_depth:
                current_symbol = None
                current_symbol_depth = 0
            depth -= 1
            i += 1

        else:
            i += 1

    return symbols


# ── Validation logic ──────────────────────────────────────────────────────────

def is_power_symbol(props: dict[str, str]) -> bool:
    ref = props.get("Reference", "")
    return any(ref.startswith(p) for p in POWER_PREFIXES)


def validate_library(
    lib_path: Path,
    required_fields: list[str],
) -> list[Violation]:
    text = lib_path.read_text(encoding="utf-8")
    symbols = parse_symbol_properties(text)
    violations: list[Violation] = []

    for sym_name, props in symbols.items():
        if is_power_symbol(props):
            continue

        missing, empty = [], []
        for field_name in required_fields:
            if field_name not in props:
                missing.append(field_name)
            elif _BLANK_VALUE_RE.match(props[field_name]):
                empty.append(field_name)

        if missing or empty:
            violations.append(
                Violation(
                    library=lib_path.name,
                    symbol=sym_name,
                    missing_fields=missing,
                    empty_fields=empty,
                )
            )

    return violations


# ── Output formatting ─────────────────────────────────────────────────────────

def emit_github_annotations(violations: list[Violation], lib_path: Path):
    """
    Print GitHub Actions workflow commands so that violations appear as
    inline annotations on the Files-changed tab of a PR.
    Format: ::warning file=<path>,title=<title>::<message>
    """
    for v in violations:
        parts = []
        if v.missing_fields:
            parts.append(f"missing: {', '.join(v.missing_fields)}")
        if v.empty_fields:
            parts.append(f"empty: {', '.join(v.empty_fields)}")
        msg = " | ".join(parts)
        print(
            f"::warning file={lib_path},"
            f"title=Symbol '{v.symbol}' property violation::{msg}"
        )


def emit_summary(
    all_violations: dict[Path, list[Violation]],
    required_fields: list[str],
    warn_only: bool,
):
    total = sum(len(v) for v in all_violations.values())

    # ── GitHub Step Summary (written to $GITHUB_STEP_SUMMARY if available) ──
    summary_lines = [
        "## KiCad Symbol Validation Report\n",
        f"**Required fields:** `{'`, `'.join(required_fields)}`\n",
    ]

    if total == 0:
        summary_lines.append("### ✅ All symbols passed validation\n")
    else:
        summary_lines.append(
            f"### ⚠️ {total} symbol(s) with property violations\n"
        )
        for lib_path, violations in all_violations.items():
            if not violations:
                continue
            summary_lines.append(f"\n#### `{lib_path}`\n")
            summary_lines.append("| Symbol | Missing | Empty |\n")
            summary_lines.append("|--------|---------|-------|\n")
            for v in violations:
                m = ", ".join(v.missing_fields) or "—"
                e = ", ".join(v.empty_fields)   or "—"
                summary_lines.append(f"| `{v.symbol}` | {m} | {e} |\n")

    import os
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.writelines(summary_lines)

    # ── stdout report ────────────────────────────────────────────────────────
    print("=" * 70)
    print("KiCad 9 Symbol Validator")
    print("=" * 70)
    print(f"Required fields : {', '.join(required_fields)}")
    print(f"Mode            : {'warn-only (non-blocking)' if warn_only else 'strict (blocking)'}")
    print()

    if total == 0:
        print("✅  All symbols passed.")
    else:
        for lib_path, violations in all_violations.items():
            if not violations:
                print(f"✅  {lib_path}  — OK")
                continue
            print(f"⚠️  {lib_path}  ({len(violations)} violation(s))")
            for v in violations:
                print(f"  Symbol: {v.symbol}")
                for line in v.lines():
                    print(line)
            print()
        print(f"Total violations: {total}")

    print("=" * 70)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Validate KiCad 9 symbol library property fields."
    )
    parser.add_argument(
        "libraries",
        nargs="*",
        type=Path,
        help="Paths to .kicad_sym files.  If omitted, the current directory "
             "is scanned recursively.",
    )
    parser.add_argument(
        "--required-fields",
        nargs="+",
        default=DEFAULT_REQUIRED_FIELDS,
        metavar="FIELD",
        help="Override the list of required property names.",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        default=True,
        help="Exit with code 0 even when violations are found (default: True).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit with code 1 when violations are found (overrides --warn-only).",
    )
    args = parser.parse_args()

    warn_only = args.warn_only and not args.strict

    # Collect library files
    if args.libraries:
        lib_files = [p for p in args.libraries if p.suffix == ".kicad_sym"]
    else:
        lib_files = list(Path(".").rglob("*.kicad_sym"))

    if not lib_files:
        print("No .kicad_sym files found.")
        sys.exit(0)

    all_violations: dict[Path, list[Violation]] = {}
    for lib_path in sorted(lib_files):
        violations = validate_library(lib_path, args.required_fields)
        all_violations[lib_path] = violations
        if violations:
            emit_github_annotations(violations, lib_path)

    emit_summary(all_violations, args.required_fields, warn_only)

    total = sum(len(v) for v in all_violations.values())
    if total > 0 and not warn_only:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
