#!/usr/bin/env python3
"""Verify the frozen V2 wheelhouse without third-party Python packages."""

from __future__ import annotations

import argparse
import hashlib
import platform
import re
import sys
from pathlib import Path


HASH_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9_.+-]+\.whl)$")
SUPPORTED_MACHINES = {"aarch64", "amd64", "arm64", "x86_64"}


class WheelhouseError(RuntimeError):
    pass


def load_manifest(wheelhouse: Path) -> dict[str, str]:
    manifest = wheelhouse / "sha256"
    if not manifest.is_file():
        raise WheelhouseError(f"missing manifest: {manifest}")

    expected: dict[str, str] = {}
    for number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        match = HASH_LINE.fullmatch(raw)
        if not match:
            raise WheelhouseError(f"invalid manifest line {number}: {raw!r}")
        digest, name = match.groups()
        if name in expected:
            raise WheelhouseError(f"duplicate manifest entry: {name}")
        expected[name] = digest
    if not expected:
        raise WheelhouseError("wheelhouse manifest is empty")
    return expected


def verify_wheelhouse(wheelhouse: Path, *, check_platform: bool = True) -> int:
    wheelhouse = wheelhouse.resolve()
    expected = load_manifest(wheelhouse)
    actual = {path.name for path in wheelhouse.glob("*.whl") if path.is_file()}
    expected_names = set(expected)

    missing = sorted(expected_names - actual)
    extra = sorted(actual - expected_names)
    if missing or extra:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("unlisted: " + ", ".join(extra))
        raise WheelhouseError("wheel file set mismatch; " + "; ".join(details))

    for name in sorted(expected):
        digest = hashlib.sha256((wheelhouse / name).read_bytes()).hexdigest()
        if digest != expected[name]:
            raise WheelhouseError(
                f"SHA-256 mismatch for {name}: expected {expected[name]}, got {digest}"
            )

    if check_platform:
        if sys.version_info[:2] != (3, 12):
            raise WheelhouseError(
                "V2 offline wheelhouse requires CPython 3.12; "
                f"found {platform.python_version()}"
            )
        if platform.system() != "Linux":
            raise WheelhouseError(
                f"V2 offline wheelhouse requires Linux; found {platform.system()}"
            )
        machine = platform.machine().lower()
        if machine not in SUPPORTED_MACHINES:
            raise WheelhouseError(
                "V2 offline wheelhouse supports x86_64 and aarch64; "
                f"found {machine or 'unknown architecture'}"
            )

    return len(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument(
        "--skip-platform-check",
        action="store_true",
        help="verify only file set and hashes (for cross-platform maintenance)",
    )
    args = parser.parse_args()
    try:
        count = verify_wheelhouse(
            args.wheelhouse, check_platform=not args.skip_platform_check
        )
    except WheelhouseError as exc:
        print(f"wheelhouse verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"wheelhouse verified: {count} wheels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
