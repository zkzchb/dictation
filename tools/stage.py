#!/usr/bin/env python3
"""Stage program assets and one external content pack for V2 or V3.

The program checkout remains read-only. V2 is staged into a writable runtime
directory so recordings and state survive code updates. V3 is staged into the
ignored ``v3/public`` build directory before a Workers deployment.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SHARED_WEB = ROOT / "shared" / "web"
DEFAULT_CONTENT_ROOT = (
    ROOT.parent / "dictation-content" / "packs" / "zh-cn" / "primary-3a"
)
sys.path.insert(0, str(ROOT / "shared"))

from content_pack import ContentPackError, load_content_pack  # noqa: E402


V2_STATIC_FILES = (
    "index.html",
    "playback_config.json",
    "studio.html",
    "studio2.html",
    "check.html",
)
V3_STATIC_FILES = ("index.html", "playback_config.json")
WORD_HASH_RE = re.compile(r"^[0-9a-f]{12}$")
SYSTEM_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")


def stage_audio(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> int:
    """Rebuild a public audio tree using only MP3 files in w/ and sys/."""
    source_root = Path(src).resolve()
    target_root = Path(dst)
    source_files: list[tuple[Path, Path]] = []
    for subdir in ("w", "sys"):
        source_dir = source_root / subdir
        if not source_dir.is_dir():
            continue
        for source in sorted(source_dir.iterdir()):
            if source.suffix == ".mp3" and source.is_file():
                source_files.append((Path(subdir) / source.name, source))

    target_root.parent.mkdir(parents=True, exist_ok=True)
    staged_root = Path(
        tempfile.mkdtemp(prefix=f".{target_root.name}.", dir=target_root.parent)
    )
    try:
        for relative, source in source_files:
            destination = staged_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        if target_root.is_symlink() or target_root.is_file():
            target_root.unlink()
        elif target_root.is_dir():
            shutil.rmtree(target_root)
        os.replace(staged_root, target_root)
        staged_root = None
    finally:
        if staged_root is not None and staged_root.exists():
            shutil.rmtree(staged_root)
    return len(source_files)


def _read_ledger(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"录音台账损坏，拒绝覆盖音频: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"录音台账必须是 JSON 对象: {path}")
    return value


def install_runtime_audio(source_root: Path, target_root: Path) -> int:
    """Install a baseline while preserving files registered as recordings."""
    recorded_words = _read_ledger(target_root / ".recorded.json")
    recorded_system = _read_ledger(target_root / ".recorded_sys.json")
    if any(
        not isinstance(key, str) or not WORD_HASH_RE.fullmatch(key)
        for key in recorded_words
    ):
        raise RuntimeError(".recorded.json 含有非法词条标识，拒绝覆盖音频")
    if any(
        not isinstance(key, str) or not SYSTEM_KEY_RE.fullmatch(key)
        for key in recorded_system
    ):
        raise RuntimeError(".recorded_sys.json 含有非法系统提示音标识，拒绝覆盖音频")
    protected = {
        *(Path("w") / f"{key}.mp3" for key in recorded_words),
        *(Path("sys") / f"{key}.mp3" for key in recorded_system),
    }

    source_files: dict[Path, Path] = {}
    for subdir in ("w", "sys"):
        source_dir = source_root / subdir
        if not source_dir.is_dir():
            raise RuntimeError(f"内容包缺少音频目录: {source_dir}")
        for source in sorted(source_dir.iterdir()):
            if source.suffix != ".mp3" or not source.is_file():
                continue
            relative = Path(subdir) / source.name
            source_files[relative] = source

    # Validate the full source/target relationship before touching the
    # writable runtime. This keeps a failed content update from leaving a
    # partially copied audio tree behind.
    expected = set(source_files)

    actual = {
        path.relative_to(target_root)
        for subdir in ("w", "sys")
        for path in (target_root / subdir).glob("*.mp3")
        if path.is_file()
    }
    unknown_protected = protected - expected
    if unknown_protected:
        names = ", ".join(sorted(path.as_posix() for path in unknown_protected))
        raise RuntimeError(f"录音台账指向当前内容包之外的文件: {names}")
    stale = actual - expected
    protected_stale = stale & protected
    if protected_stale:
        names = ", ".join(sorted(path.as_posix() for path in protected_stale))
        raise RuntimeError(f"内容更新将孤立已有真人录音，需先迁移: {names}")
    missing_protected = protected & (expected - actual)
    if missing_protected:
        names = ", ".join(sorted(path.as_posix() for path in missing_protected))
        raise RuntimeError(f"录音台账登记的文件不存在，拒绝用基线补回: {names}")

    for relative, source in sorted(source_files.items()):
        if relative not in protected:
            destination = target_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    for relative in stale:
        (target_root / relative).unlink()
    return len(expected)


def _copy_static(files: tuple[str, ...], target_root: Path) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    for name in files:
        source = SHARED_WEB / name
        if not source.is_file():
            raise RuntimeError(f"缺少程序静态文件: {source}")
        shutil.copy2(source, target_root / name)


def _git_ref(path: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def deployment_record(pack) -> dict[str, object]:
    content_repo = pack.root
    while content_repo != content_repo.parent and not (content_repo / ".git").exists():
        content_repo = content_repo.parent
    return {
        "schema": 1,
        "program_ref": _git_ref(ROOT),
        "content_ref": _git_ref(content_repo),
        "content_version": pack.metadata.get("version"),
        "pack_id": pack.id,
        "dataset_sha256": pack.dataset_sha256,
        "staged_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def _write_record(target_root: Path, pack) -> None:
    (target_root / "deployment.json").write_text(
        json.dumps(deployment_record(pack), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def stage_v2(content_root: Path, web_root: Path) -> int:
    pack = load_content_pack(content_root)
    audio_source = pack.paths.get("tts")
    if audio_source is None:
        raise RuntimeError("V2 部署要求内容包提供 paths.tts")
    _copy_static(V2_STATIC_FILES, web_root)
    copied = install_runtime_audio(audio_source, web_root / "audio")
    _write_record(web_root, pack)
    print(f"[OK] V2 runtime: {web_root} ({copied} 个音频)")
    return copied


def stage_v3(content_root: Path, public_root: Path) -> int:
    pack = load_content_pack(content_root)
    audio_source = pack.paths.get("tts")
    if audio_source is None:
        raise RuntimeError("V3 部署要求内容包提供 paths.tts")
    public_root.mkdir(parents=True, exist_ok=True)
    _copy_static(V3_STATIC_FILES, public_root)
    copied = stage_audio(audio_source, public_root / "audio")
    _write_record(public_root, pack)
    print(f"[OK] V3 public: {public_root} ({copied} 个音频)")
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("v2", "v3", "all"), nargs="?", default="all")
    parser.add_argument("--content-root", type=Path, default=DEFAULT_CONTENT_ROOT)
    parser.add_argument("--web-root", type=Path, default=ROOT / ".runtime" / "local" / "web")
    parser.add_argument("--public-root", type=Path, default=ROOT / "v3" / "public")
    args = parser.parse_args()
    try:
        if args.target in ("v2", "all"):
            stage_v2(args.content_root, args.web_root)
        if args.target in ("v3", "all"):
            stage_v3(args.content_root, args.public_root)
    except (ContentPackError, OSError, RuntimeError) as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
