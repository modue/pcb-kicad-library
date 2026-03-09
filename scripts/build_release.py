#!/usr/bin/env python3
"""
build_release.py
----------------
Builds a KiCad PCM-compatible release package from this repository.

What it does:
  1. Creates a ZIP archive with the correct KiCad PCM directory structure:
       symbols/      ← all .kicad_sym files found in the repo
       footprints/   ← all .pretty directories (if any)
       3dmodels/     ← all .3dshapes directories (if any)
       resources/    ← icon.png (if present)
       metadata.json ← from repo root (WITHOUT download_* fields, per spec)

  2. Computes SHA-256 and byte sizes (compressed + uncompressed).

  3. Reads the template metadata.json from the repo root and writes a
     release-ready packages.json into docs/ (which is served by GitHub Pages).
     This version of metadata DOES include the download_* fields.

  4. Writes docs/repository.json if it doesn't already exist.

Usage (called by GitHub Actions, but also runnable locally):
  python scripts/build_release.py \\
      --version 1.0.0 \\
      --download-url https://github.com/OWNER/REPO/releases/download/v1.0.0/library.zip \\
      --output-zip dist/library.zip

Requirements: Python 3.10+, zero external dependencies.
"""

import argparse
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

# Matches any path ending with   SomeName.3dshapes/ModelFile.ext
# regardless of what prefix came before (env var, absolute, relative).
_MODEL_PATH_RE = re.compile(
    r'([\w][\w\-\.]*\.3dshapes/[\w\-\.]+\.(?:step|stp|wrl|wrz))',
    re.IGNORECASE,
)


def rewrite_3d_model_paths(content: str, identifier: str) -> str:
    """
    Rewrite 3D model paths inside a .kicad_mod file so they point to the
    PCM-installed location instead of the local development path.

    Development path example:
      ${KICAD_USER_MODUE_DIR}/pcb-kicad-library/3dmodels/
          modue_DFN_QFN.3dshapes/QFN-56.step

    Installed path (output):
      ${KICAD_USER_TEMPLATE_DIR}/../3rdparty/3dmodels/
          com.github.modue.pcb-kicad-library/modue_DFN_QFN.3dshapes/QFN-56.step

    Only the portion from `*.3dshapes/model.ext` onward is preserved;
    the prefix is always replaced with the PCM base path.
    """
    pcm_base = f"${{KICAD_USER_TEMPLATE_DIR}}/../3rdparty/3dmodels/{identifier}"

    def _replace_model_value(m: re.Match) -> str:
        full_path = m.group(2)
        # Extract just the "SomeName.3dshapes/ModelFile.ext" tail
        shapes_match = _MODEL_PATH_RE.search(full_path)
        if shapes_match:
            new_path = f"{pcm_base}/{shapes_match.group(1)}"
            return f"{m.group(1)}{new_path}{m.group(3)}"
        return m.group(0)  # no .3dshapes pattern found — leave unchanged

    model_expr_re = re.compile(r'(\(model\s+")([^"]+)(")', re.IGNORECASE)
    return model_expr_re.sub(_replace_model_value, content)


# ── Helpers ───────────────────────────────────────────────────────────────────

def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def uncompressed_size(zip_path: Path) -> int:
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            total += info.file_size
    return total


def find_symbol_libs(root: Path) -> list[Path]:
    return sorted(root.rglob("*.kicad_sym"))


def find_pretty_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.pretty") if p.is_dir())


def find_3dshapes_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.3dshapes") if p.is_dir())


# ── ZIP builder ───────────────────────────────────────────────────────────────

def build_zip(repo_root: Path, output_zip: Path):
    """
    Builds the PCM-compatible ZIP archive.

    KiCad requires a flat archive (no top-level directory wrapper) with
    specific subdirectory names.  We strip metadata.json of its download_*
    keys before including it — the spec says those fields must NOT be in the
    archive copy.
    """
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    # Read the package identifier from metadata.json (needed for 3D path rewrite)
    meta_src = repo_root / "metadata.json"
    identifier = json.loads(meta_src.read_text(encoding="utf-8")).get(
        "identifier", "com.example.library"
    )

    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:

        # ── symbols/ ─────────────────────────────────────────────────────────
        for sym in find_symbol_libs(repo_root):
            # Skip anything inside docs/, dist/, scripts/, .github/
            if any(part in sym.parts for part in ("docs", "dist", "scripts", ".github")):
                continue
            # Rename modue_X.kicad_sym → modue_PROD_X.kicad_sym so the
            # production-installed library has a distinct name from the
            # development copy open in the same KiCad instance.
            prod_name = sym.name.replace("modue_", "modue_PROD_", 1)
            arcname = f"symbols/{prod_name}"
            print(f"  + {arcname}  (renamed from {sym.name})")
            zf.write(sym, arcname)

        # ── footprints/ ──────────────────────────────────────────────────────
        for pretty in find_pretty_dirs(repo_root):
            if any(part in pretty.parts for part in ("docs", "dist", "scripts", ".github")):
                continue
            for fp_file in sorted(pretty.rglob("*")):
                if fp_file.is_file():
                    rel = fp_file.relative_to(repo_root)
                    arcname = f"footprints/{rel}"
                    if fp_file.suffix == ".kicad_mod":
                        # Rewrite local 3D model paths → PCM-installed paths
                        content = fp_file.read_text(encoding="utf-8")
                        content = rewrite_3d_model_paths(content, identifier)
                        print(f"  + {arcname}  (3D model paths rewritten)")
                        zf.writestr(arcname, content)
                    else:
                        print(f"  + {arcname}")
                        zf.write(fp_file, arcname)

        # ── 3dmodels/ ────────────────────────────────────────────────────────
        for shapes in find_3dshapes_dirs(repo_root):
            if any(part in shapes.parts for part in ("docs", "dist", "scripts", ".github")):
                continue
            for model_file in sorted(shapes.rglob("*")):
                if model_file.is_file():
                    rel = model_file.relative_to(repo_root)
                    arcname = f"3dmodels/{rel}"
                    print(f"  + {arcname}")
                    zf.write(model_file, arcname)

        # ── resources/icon.png ───────────────────────────────────────────────
        icon = repo_root / "resources" / "icon.png"
        if icon.exists():
            print(f"  + resources/icon.png")
            zf.write(icon, "resources/icon.png")

        # ── metadata.json (archive copy — without download_* fields) ─────────
        if not meta_src.exists():
            raise FileNotFoundError(
                "metadata.json not found in repo root. "
                "Create it based on the template in the README."
            )
        meta = json.loads(meta_src.read_text(encoding="utf-8"))
        # Strip download_* keys from each version entry (spec requirement)
        for ver in meta.get("versions", []):
            for key in ("download_url", "download_sha256", "download_size", "install_size"):
                ver.pop(key, None)
        zf.writestr("metadata.json", json.dumps(meta, indent=2, ensure_ascii=False))
        print(f"  + metadata.json (download_* fields stripped for archive copy)")

    print(f"\nArchive written: {output_zip} ({output_zip.stat().st_size:,} bytes)")


# ── packages.json / repository.json ──────────────────────────────────────────

def update_docs(
    repo_root: Path,
    version: str,
    download_url: str,
    output_zip: Path,
):
    """
    Writes docs/packages.json with the full metadata including download_* fields.
    Also bootstraps docs/repository.json on first run.
    """
    docs = repo_root / "docs"
    docs.mkdir(exist_ok=True)

    # Read the base metadata
    meta_src = repo_root / "metadata.json"
    meta = json.loads(meta_src.read_text(encoding="utf-8"))

    digest     = sha256_of_file(output_zip)
    dl_size    = output_zip.stat().st_size
    inst_size  = uncompressed_size(output_zip)

    print(f"\nRelease stats:")
    print(f"  SHA-256      : {digest}")
    print(f"  Download size: {dl_size:,} bytes")
    print(f"  Install size : {inst_size:,} bytes")

    # Build the version entry for this release
    new_version = {
        "version": version,
        "status": "stable",
        "kicad_version": "9.0",
        "download_url": download_url,
        "download_sha256": digest,
        "download_size": dl_size,
        "install_size": inst_size,
    }

    # Merge: prepend new version, keep older ones (deduplicated by version string)
    existing_versions = [
        v for v in meta.get("versions", [])
        if v.get("version") != version
    ]
    meta["versions"] = [new_version] + existing_versions

    # ── packages.json ────────────────────────────────────────────────────────
    packages_json = docs / "packages.json"
    packages_data = {"$schema": "https://go.kicad.org/pcm/schemas/v1", "packages": [meta]}
    packages_json.write_text(
        json.dumps(packages_data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\nWritten: {packages_json}")

    # ── repository.json (always rewritten to keep update_timestamp fresh) ───────
    import time
    repo_json_path = docs / "repository.json"

    # Derive GitHub Pages URL — when Pages source is set to /docs folder,
    # files are served at the repo root URL (NOT under /docs/).
    gh_repo    = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")
    owner      = gh_repo.split("/")[0] if "/" in gh_repo else "OWNER"
    repo_name  = gh_repo.split("/")[-1] if "/" in gh_repo else "REPO"
    pages_base = f"https://{owner}.github.io/{repo_name}"

    update_timestamp = int(time.time())

    if repo_json_path.exists():
        # Keep existing URL in case it was customised, just bump the timestamp
        repo_json = json.loads(repo_json_path.read_text(encoding="utf-8"))
        repo_json["packages"]["update_timestamp"] = update_timestamp
        print(f"Updated: {repo_json_path}  (update_timestamp refreshed)")
    else:
        repo_json = {
            "$schema": "https://go.kicad.org/pcm/schemas/v1",
            "name": meta.get("name", "KiCad Symbol Library"),
            "packages": {
                "url": f"{pages_base}/packages.json",
                "update_timestamp": update_timestamp
            },
        }
        print(f"Written: {repo_json_path}  (first-time bootstrap)")

    repo_json_path.write_text(
        json.dumps(repo_json, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Print the URL users need to paste into KiCad PCM
    repo_data  = json.loads(repo_json_path.read_text())
    pkg_url    = repo_data.get("packages", {}).get("url", "")
    repo_url   = pkg_url.replace("packages.json", "repository.json")
    print(f"\n{'='*60}")
    print(f"  KiCad PCM repository URL (share this with your team):")
    print(f"  {repo_url}")
    print(f"{'='*60}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build KiCad PCM release package.")
    parser.add_argument("--version",      required=True, help="Version string, e.g. 1.0.0")
    parser.add_argument("--download-url", required=True, help="Public URL of the ZIP asset")
    parser.add_argument("--output-zip",   required=True, type=Path, help="Path for output ZIP")
    parser.add_argument("--repo-root",    default=".",   type=Path, help="Repo root directory")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    print(f"Building release {args.version} from {repo_root}\n")

    print("Building ZIP archive:")
    build_zip(repo_root, args.output_zip)

    print("\nUpdating docs/packages.json:")
    update_docs(repo_root, args.version, args.download_url, args.output_zip)


if __name__ == "__main__":
    main()