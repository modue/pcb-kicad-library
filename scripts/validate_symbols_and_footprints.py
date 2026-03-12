#!/usr/bin/env python3
"""
validate_symbols.py
-------------------
Validates KiCad 9 .kicad_sym library files and related assets.

Checks performed
----------------
1. **Symbol property check** – every non-power symbol must have the required
   property fields (Reference, Value, Footprint, Datasheet, Description,
   Manufacturer, Manufacturer PN, modue PN, JLC PN by default) with
   non-empty values.

2. **Symbol → footprint reference check** – the Footprint property of each
   symbol must refer to a footprint that actually exists in the repo's
   footprints/ directory (format: LibraryName:FootprintName).
   Skipped when footprints/ contains no .kicad_mod files.

3. **Footprint → 3-D model check** – every footprint (.kicad_mod) in the
   repo's footprints/ directory must have a matching .step file (same stem
   name) anywhere under the repo's 3dmodels/ directory.
   Skipped when footprints/ contains no .kicad_mod files.

4. **Footprint 3-D model embed check** – every footprint must have an
   embedded 3D model (embedded_files section present) and the (model ...)
   path must start with "kicad-embed://".
   Skipped when footprints/ contains no .kicad_mod files.

Exit codes
----------
  0  – all checks pass (or --warn-only is set)
  1  – one or more violations found AND --warn-only is NOT set
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_REQUIRED_FIELDS: list[str] = [
    "Reference",
    "Value",
    "Footprint",
    "Datasheet",
    "Description",
    "Manufacturer",
    "Manufacturer PN",
    "modue PN",
    "JLC PN",
]

# Symbols whose Reference prefix appears here are exempt from property checks.
POWER_PREFIXES: frozenset[str] = frozenset({"#PWR", "#FLG", "PWR", "#"})

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
            out.append(f"  ✗ missing properties: {', '.join(self.missing_fields)}")
        if self.empty_fields:
            out.append(f"  ✗ empty properties:   {', '.join(self.empty_fields)}")
        return out


@dataclass
class FootprintRefViolation:
    library: str
    symbol: str
    footprint_value: str
    reason: str   # "bad_format" | "missing_library" | "missing_footprint"


@dataclass
class Model3dViolation:
    footprint_lib: str
    footprint_name: str


@dataclass
class FootprintEmbedViolation:
    footprint_lib: str
    footprint_name: str
    issues: list[str] = field(default_factory=list)


# ── S-expression parser (minimal, single-pass) ────────────────────────────────

def _unescape(s: str) -> str:
    """Remove surrounding quotes and unescape KiCad string literals."""
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    return s


def _tokenise(text: str):
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
            j = i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2
                elif text[j] == '"':
                    j += 1
                    break
                else:
                    j += 1
            yield text[i:j]
            i = j
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()\"':
                j += 1
            yield text[i:j]
            i = j


def parse_symbol_properties(lib_text: str) -> dict[str, dict[str, str]]:
    """
    Return {symbol_name: {property_name: property_value}} for every
    top-level symbol in the library.
    """
    tokens = list(_tokenise(lib_text))
    symbols: dict[str, dict[str, str]] = {}

    i = 0
    depth = 0
    current_symbol: Optional[str] = None
    current_symbol_depth: int = 0

    while i < len(tokens):
        tok = tokens[i]

        if tok == '(':
            depth += 1
            if (depth == 2
                    and i + 2 < len(tokens)
                    and tokens[i + 1] == 'symbol'
                    and current_symbol is None):
                name = _unescape(tokens[i + 2])
                if not re.search(r'_\d+_\d+$', name):
                    current_symbol = name
                    current_symbol_depth = depth
                    symbols[name] = {}

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


# ── Check 1: Symbol property validation ───────────────────────────────────────

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
            violations.append(Violation(
                library=lib_path.name, symbol=sym_name,
                missing_fields=missing, empty_fields=empty,
            ))

    return violations


# ── Check 2 & 3: Footprint / 3-D model discovery ─────────────────────────────

def find_available_footprints(repo_root: Path) -> dict[str, set[str]]:
    """
    Returns {lib_name: {footprint_stem, ...}} for all .kicad_mod files found
    under repo_root/footprints/**/*.pretty/
    """
    libs: dict[str, set[str]] = {}
    fp_root = repo_root / "footprints"
    if not fp_root.is_dir():
        return libs
    for kicad_mod in sorted(fp_root.rglob("*.kicad_mod")):
        lib_name = kicad_mod.parent.stem   # "modue_QFN_DFN" from "modue_QFN_DFN.pretty"
        libs.setdefault(lib_name, set()).add(kicad_mod.stem)
    return libs


def find_available_3d_models(repo_root: Path) -> set[str]:
    """
    Returns the set of .step file stems found anywhere under repo_root/3dmodels/
    """
    models_root = repo_root / "3dmodels"
    if not models_root.is_dir():
        return set()
    return {p.stem for p in models_root.rglob("*.step")}


def validate_symbol_footprint_refs(
    lib_path: Path,
    symbols: dict[str, dict[str, str]],
    available_footprints: dict[str, set[str]],
) -> list[FootprintRefViolation]:
    """
    Check that each symbol's Footprint property references a footprint that
    exists in the repo's footprints/ directory.
    """
    violations: list[FootprintRefViolation] = []
    for sym_name, props in symbols.items():
        if is_power_symbol(props):
            continue
        fp_value = props.get("Footprint", "").strip()
        if not fp_value or _BLANK_VALUE_RE.match(fp_value):
            continue  # blank already caught by property validator

        if ":" not in fp_value:
            violations.append(FootprintRefViolation(
                library=lib_path.name, symbol=sym_name,
                footprint_value=fp_value, reason="bad_format",
            ))
            continue

        lib_name, fp_name = fp_value.split(":", 1)
        if lib_name not in available_footprints:
            violations.append(FootprintRefViolation(
                library=lib_path.name, symbol=sym_name,
                footprint_value=fp_value, reason="missing_library",
            ))
        elif fp_name not in available_footprints[lib_name]:
            violations.append(FootprintRefViolation(
                library=lib_path.name, symbol=sym_name,
                footprint_value=fp_value, reason="missing_footprint",
            ))

    return violations


def validate_footprint_3d_models(
    available_footprints: dict[str, set[str]],
    available_3d_models: set[str],
) -> list[Model3dViolation]:
    """
    Check that every footprint in footprints/ has a matching .step file
    (by stem name) anywhere in 3dmodels/.
    """
    violations: list[Model3dViolation] = []
    for lib_name, fps in sorted(available_footprints.items()):
        for fp_name in sorted(fps):
            if fp_name not in available_3d_models:
                violations.append(Model3dViolation(
                    footprint_lib=lib_name, footprint_name=fp_name,
                ))
    return violations


_MODEL_PATH_RE = re.compile(r'\(model\s+"([^"]+)"', re.IGNORECASE)


def validate_footprint_3d_embeds(
    repo_root: Path,
    available_footprints: dict[str, set[str]],
) -> list[FootprintEmbedViolation]:
    """
    Check that every footprint:
      1. Has embedded 3D model data (embedded_files section present).
      2. References the model via kicad-embed:// path.
    """
    violations: list[FootprintEmbedViolation] = []
    fp_root = repo_root / "footprints"
    for lib_name, fps in sorted(available_footprints.items()):
        for fp_name in sorted(fps):
            fp_path = fp_root / f"{lib_name}.pretty" / f"{fp_name}.kicad_mod"
            if not fp_path.exists():
                continue
            content = fp_path.read_text(encoding="utf-8")
            issues: list[str] = []

            has_embedded = "(embedded_files" in content
            if not has_embedded:
                issues.append("no embedded_files section (3D model not embedded)")

            models = _MODEL_PATH_RE.findall(content)
            if not models:
                issues.append("no (model ...) entry found")
            else:
                bad_paths = [m for m in models if not m.startswith("kicad-embed://")]
                if bad_paths:
                    issues.append(
                        "model path(s) not using kicad-embed://: "
                        + ", ".join(bad_paths)
                    )

            if issues:
                violations.append(FootprintEmbedViolation(
                    footprint_lib=lib_name,
                    footprint_name=fp_name,
                    issues=issues,
                ))
    return violations


# ── Output formatting ─────────────────────────────────────────────────────────

def emit_github_annotations(violations: list[Violation], lib_path: Path):
    for v in violations:
        parts = []
        if v.missing_fields:
            parts.append(f"missing: {', '.join(v.missing_fields)}")
        if v.empty_fields:
            parts.append(f"empty: {', '.join(v.empty_fields)}")
        print(
            f"::warning file={lib_path},"
            f"title=Symbol '{v.symbol}' property violation::"
            + " | ".join(parts)
        )


def emit_fp_ref_annotations(violations: list[FootprintRefViolation]):
    reason_label = {
        "bad_format":        "Footprint reference has no ':' separator",
        "missing_library":   "Footprint library not found in footprints/",
        "missing_footprint": "Footprint not found in library directory",
    }
    for v in violations:
        print(
            f"::warning file=symbols/{v.library},"
            f"title=Symbol '{v.symbol}' footprint reference invalid::"
            f"{reason_label.get(v.reason, v.reason)}: {v.footprint_value}"
        )


def emit_embed_annotations(violations: list[FootprintEmbedViolation]):
    for v in violations:
        fp_path = f"footprints/{v.footprint_lib}.pretty/{v.footprint_name}.kicad_mod"
        print(
            f"::warning file={fp_path},"
            f"title=3D embed issue in '{v.footprint_name}'::"
            + "; ".join(v.issues)
        )


def emit_3d_annotations(violations: list[Model3dViolation]):
    for v in violations:
        fp_path = f"footprints/{v.footprint_lib}.pretty/{v.footprint_name}.kicad_mod"
        print(
            f"::warning file={fp_path},"
            f"title=Missing 3D model for '{v.footprint_name}'::"
            f"Expected {v.footprint_name}.step in 3dmodels/"
        )


def emit_summary(
    all_prop_violations:   dict[Path, list[Violation]],
    all_fp_ref_violations: list[FootprintRefViolation],
    all_3d_violations:     list[Model3dViolation],
    all_embed_violations:  list[FootprintEmbedViolation],
    required_fields: list[str],
    warn_only: bool,
    fp_check_ran: bool,
):
    import os

    prop_total   = sum(len(v) for v in all_prop_violations.values())
    total_issues = prop_total + len(all_fp_ref_violations) + len(all_3d_violations) + len(all_embed_violations)

    summary_lines = [
        "## KiCad Symbol & Footprint Validation Report\n\n",
        f"**Required fields:** `{'`, `'.join(required_fields)}`  \n",
        f"**Mode:** {'warn-only (non-blocking)' if warn_only else 'strict (blocking)'}  \n\n",
    ]

    # ── 1. Property check ─────────────────────────────────────────────────────
    summary_lines.append("### 1. Symbol property check\n")
    if prop_total == 0:
        summary_lines.append("✅ All symbols passed.\n\n")
    else:
        summary_lines.append(f"⚠️ {prop_total} symbol(s) with property violations\n\n")
        for lib_path, violations in all_prop_violations.items():
            if not violations:
                continue
            summary_lines.append(f"#### `{lib_path}`\n")
            summary_lines.append("| Symbol | Missing | Empty |\n")
            summary_lines.append("|--------|---------|-------|\n")
            for v in violations:
                m = ", ".join(v.missing_fields) or "—"
                e = ", ".join(v.empty_fields)   or "—"
                summary_lines.append(f"| `{v.symbol}` | {m} | {e} |\n")
        summary_lines.append("\n")

    # ── 2. Footprint reference check ──────────────────────────────────────────
    summary_lines.append("### 2. Symbol → footprint reference check\n")
    if not fp_check_ran:
        summary_lines.append("ℹ️ Skipped — no .kicad_mod files found in footprints/.\n\n")
    elif not all_fp_ref_violations:
        summary_lines.append("✅ All footprint references are valid.\n\n")
    else:
        summary_lines.append(f"⚠️ {len(all_fp_ref_violations)} invalid footprint reference(s)\n\n")
        summary_lines.append("| Library | Symbol | Footprint reference | Issue |\n")
        summary_lines.append("|---------|--------|---------------------|-------|\n")
        for v in all_fp_ref_violations:
            label = {
                "bad_format":        "bad format (missing `:`)",
                "missing_library":   "library not in footprints/",
                "missing_footprint": "footprint not found in library",
            }.get(v.reason, v.reason)
            summary_lines.append(
                f"| `{v.library}` | `{v.symbol}` | `{v.footprint_value}` | {label} |\n"
            )
        summary_lines.append("\n")

    # ── 3. 3-D model check ────────────────────────────────────────────────────
    summary_lines.append("### 3. Footprint → 3-D model check\n")
    if not fp_check_ran:
        summary_lines.append("ℹ️ Skipped — no .kicad_mod files found in footprints/.\n\n")
    elif not all_3d_violations:
        summary_lines.append("✅ All footprints have a matching .step model.\n\n")
    else:
        summary_lines.append(f"⚠️ {len(all_3d_violations)} footprint(s) missing a .step model\n\n")
        summary_lines.append("| Footprint library | Footprint | Expected model |\n")
        summary_lines.append("|-------------------|-----------|----------------|\n")
        for v in all_3d_violations:
            summary_lines.append(
                f"| `{v.footprint_lib}` | `{v.footprint_name}` | `{v.footprint_name}.step` |\n"
            )
        summary_lines.append("\n")

    # ── 4. 3-D model embed check ──────────────────────────────────────────────
    summary_lines.append("### 4. Footprint → 3-D model embed check\n")
    if not fp_check_ran:
        summary_lines.append("ℹ️ Skipped — no .kicad_mod files found in footprints/.\n\n")
    elif not all_embed_violations:
        summary_lines.append("✅ All footprints have an embedded 3D model with kicad-embed:// path.\n\n")
    else:
        summary_lines.append(f"⚠️ {len(all_embed_violations)} footprint(s) with 3D embed issues\n\n")
        summary_lines.append("| Footprint library | Footprint | Issues |\n")
        summary_lines.append("|-------------------|-----------|--------|\n")
        for v in all_embed_violations:
            summary_lines.append(
                f"| `{v.footprint_lib}` | `{v.footprint_name}` | {'; '.join(v.issues)} |\n"
            )
        summary_lines.append("\n")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.writelines(summary_lines)

    # ── stdout ────────────────────────────────────────────────────────────────
    print("=" * 70)
    print("KiCad 9 Symbol & Footprint Validator")
    print("=" * 70)
    print(f"Required fields : {', '.join(required_fields)}")
    print(f"Mode            : {'warn-only (non-blocking)' if warn_only else 'strict (blocking)'}")
    print()

    # 1
    if prop_total == 0:
        print("✅  [1] All symbols passed property check.")
    else:
        for lib_path, violations in all_prop_violations.items():
            if not violations:
                print(f"✅  [1] {lib_path}  — OK")
                continue
            print(f"⚠️  [1] {lib_path}  ({len(violations)} violation(s))")
            for v in violations:
                print(f"  Symbol: {v.symbol}")
                for line in v.lines():
                    print(line)
        print(f"Property violations total: {prop_total}")
    print()

    # 2
    if not fp_check_ran:
        print("ℹ️  [2] Footprint reference check skipped (no .kicad_mod files).")
    elif not all_fp_ref_violations:
        print("✅  [2] All footprint references valid.")
    else:
        print(f"⚠️  [2] {len(all_fp_ref_violations)} invalid footprint reference(s):")
        for v in all_fp_ref_violations:
            print(f"  {v.library} / {v.symbol}: {v.footprint_value} [{v.reason}]")
    print()

    # 3
    if not fp_check_ran:
        print("ℹ️  [3] 3-D model check skipped (no .kicad_mod files).")
    elif not all_3d_violations:
        print("✅  [3] All footprints have matching .step models.")
    else:
        print(f"⚠️  [3] {len(all_3d_violations)} footprint(s) missing .step model:")
        for v in all_3d_violations:
            print(f"  {v.footprint_lib}:{v.footprint_name} → {v.footprint_name}.step missing")
    print()

    # 4
    if not fp_check_ran:
        print("ℹ️  [4] 3-D model embed check skipped (no .kicad_mod files).")
    elif not all_embed_violations:
        print("✅  [4] All footprints have embedded 3D model with kicad-embed:// path.")
    else:
        print(f"⚠️  [4] {len(all_embed_violations)} footprint(s) with 3D embed issues:")
        for v in all_embed_violations:
            print(f"  {v.footprint_lib}:{v.footprint_name}")
            for issue in v.issues:
                print(f"    ✗ {issue}")
    print()

    print(f"Total issues: {total_issues}")
    print("=" * 70)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Validate KiCad 9 symbol properties, footprint references, and 3D models."
    )
    parser.add_argument(
        "libraries",
        nargs="*",
        type=Path,
        help="Paths to .kicad_sym files. If omitted, the current directory is scanned recursively.",
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
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        metavar="DIR",
        help="Repository root used to locate footprints/ and 3dmodels/ (default: current dir).",
    )
    args = parser.parse_args()

    warn_only = args.warn_only and not args.strict
    repo_root = args.repo_root.resolve()

    # ── Collect symbol library files ──────────────────────────────────────────
    if args.libraries:
        lib_files = [p for p in args.libraries if p.suffix == ".kicad_sym"]
    else:
        lib_files = list(Path(".").rglob("*.kicad_sym"))

    if not lib_files:
        print("No .kicad_sym files found.")
        sys.exit(0)

    # ── Discover footprints and 3-D models ────────────────────────────────────
    available_footprints = find_available_footprints(repo_root)
    available_3d_models  = find_available_3d_models(repo_root)
    fp_check_ran = bool(available_footprints)

    # ── Check 1 + 2: per-library ──────────────────────────────────────────────
    all_prop_violations: dict[Path, list[Violation]] = {}
    all_fp_ref_violations: list[FootprintRefViolation] = []

    for lib_path in sorted(lib_files):
        prop_violations = validate_library(lib_path, args.required_fields)
        all_prop_violations[lib_path] = prop_violations
        if prop_violations:
            emit_github_annotations(prop_violations, lib_path)

        if fp_check_ran:
            text = lib_path.read_text(encoding="utf-8")
            symbols = parse_symbol_properties(text)
            fp_violations = validate_symbol_footprint_refs(lib_path, symbols, available_footprints)
            if fp_violations:
                emit_fp_ref_annotations(fp_violations)
            all_fp_ref_violations.extend(fp_violations)

    # ── Check 3: footprint → 3-D model ───────────────────────────────────────
    all_3d_violations: list[Model3dViolation] = []
    if fp_check_ran:
        all_3d_violations = validate_footprint_3d_models(available_footprints, available_3d_models)
        if all_3d_violations:
            emit_3d_annotations(all_3d_violations)

    # ── Check 4: footprint 3-D model embed ───────────────────────────────────
    all_embed_violations: list[FootprintEmbedViolation] = []
    if fp_check_ran:
        all_embed_violations = validate_footprint_3d_embeds(repo_root, available_footprints)
        if all_embed_violations:
            emit_embed_annotations(all_embed_violations)

    emit_summary(
        all_prop_violations,
        all_fp_ref_violations,
        all_3d_violations,
        all_embed_violations,
        args.required_fields,
        warn_only,
        fp_check_ran,
    )

    total_issues = (
        sum(len(v) for v in all_prop_violations.values())
        + len(all_fp_ref_violations)
        + len(all_3d_violations)
        + len(all_embed_violations)
    )
    if total_issues > 0 and not warn_only:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
