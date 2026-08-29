#!/usr/bin/env python3
"""Audit national-academy anchor contents against a pre-change Git revision."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

try:
    from add_national_anchor_tocs import (
        CATEGORIES,
        NATIONAL_ROOT,
        ROOT,
        STYLE_BLOCK_RE,
        TOC_BLOCK_RE,
        detail_pages,
        strip_owned_heading_attributes,
        validate_hubs,
        validate_page,
    )
except ImportError:  # Supports package-style execution from the site root.
    from .add_national_anchor_tocs import (
        CATEGORIES,
        NATIONAL_ROOT,
        ROOT,
        STYLE_BLOCK_RE,
        TOC_BLOCK_RE,
        detail_pages,
        strip_owned_heading_attributes,
        validate_hubs,
        validate_page,
    )


class GitBatch:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("Could not open git cat-file pipes")

    def blob(self, revision: str, path: Path) -> bytes:
        relative = path.relative_to(ROOT).as_posix()
        request = f"{revision}:{relative}\n".encode("utf-8")
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(request)
        self.process.stdin.flush()
        header = self.process.stdout.readline().decode("ascii", errors="replace").strip()
        parts = header.split()
        if len(parts) == 2 and parts[1] == "missing":
            raise ValueError(f"Missing baseline blob: {relative}")
        if len(parts) != 3 or parts[1] != "blob":
            raise ValueError(f"Unexpected git cat-file response: {header}")
        size = int(parts[2])
        data = self.process.stdout.read(size)
        if self.process.stdout.read(1) != b"\n":
            raise ValueError("Malformed git cat-file record terminator")
        return data

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait(timeout=10)


def strip_enhancement(source: str) -> str:
    source, style_count = STYLE_BLOCK_RE.subn("", source, count=1)
    source, toc_count = TOC_BLOCK_RE.subn("", source, count=1)
    if style_count != 1 or toc_count != 1:
        raise ValueError(
            f"Could not strip owned enhancement style={style_count} toc={toc_count}"
        )
    return strip_owned_heading_attributes(source)


def normalized_newlines(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline-ref",
        default="HEAD",
        help="Git revision before the anchor change (default: HEAD)",
    )
    args = parser.parse_args()

    pages = detail_pages()
    failures: list[str] = []
    exact_preserved = 0
    batch = GitBatch()
    try:
        for path in pages:
            relative = path.relative_to(ROOT).as_posix()
            try:
                current = path.read_bytes().decode("utf-8")
                errors = validate_page(current)
                if errors:
                    raise ValueError("; ".join(errors))
                stripped = strip_enhancement(current).encode("utf-8")
                baseline = batch.blob(args.baseline_ref, path)
                if normalized_newlines(stripped) != normalized_newlines(baseline):
                    raise ValueError("Non-anchor page content differs from baseline")
                exact_preserved += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{relative}: {exc}")

        hubs = [NATIONAL_ROOT / "index.html"] + [
            NATIONAL_ROOT / category / "index.html" for category in CATEGORIES
        ]
        for hub in hubs:
            relative = hub.relative_to(ROOT).as_posix()
            try:
                if normalized_newlines(hub.read_bytes()) != normalized_newlines(
                    batch.blob(args.baseline_ref, hub)
                ):
                    raise ValueError("Hub differs from baseline")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{relative}: {exc}")
    finally:
        batch.close()

    failures.extend(validate_hubs())
    if not (ROOT / "assets" / "national-anchor-toc.css").is_file():
        failures.append("Anchor TOC stylesheet is missing")

    print(f"pages={len(pages)} exact_non_anchor_preservation={exact_preserved}")
    print(f"hubs_checked={1 + len(CATEGORIES)} baseline_ref={args.baseline_ref}")
    print(f"failures={len(failures)}")
    for failure in failures[:50]:
        print("ERROR", failure)
    if len(failures) > 50:
        print(f"ERROR ... and {len(failures) - 50} more")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
