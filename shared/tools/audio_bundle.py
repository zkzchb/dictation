#!/usr/bin/env python3
"""Build, verify, and install V2 audio distribution assets.

The Git repository contains the canonical textbook JSON and pure TTS baseline.
An optional baseline bundle can carry the same TTS for alternate distribution.
A human bundle carries only files named in the recording ledgers, so it can be
layered over a clean baseline without importing learning or review history.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
SHARED_DIR = TOOLS_DIR.parent
ROOT = SHARED_DIR.parent
CONTENT_ROOT = Path(os.getenv("DICTATION_CONTENT_ROOT", ROOT / "chinese" / "3a"))
DEFAULT_AUDIO_DIR = CONTENT_ROOT / "tts"
DATA_FILES = (
    CONTENT_ROOT / "lessons.json",
    CONTENT_ROOT / "knowledge_points.json",
    CONTENT_ROOT / "studio_manifest.json",
)

sys.path.insert(0, str(SHARED_DIR))
from gen_slices import MAX_GROUPS, SYS_PHRASES, collect_targets  # noqa: E402


SCHEMA_VERSION = 1
BASELINE_KIND = "baseline-tts"
HUMAN_KIND = "human-recordings"
HASH_RE = re.compile(r"^[0-9a-f]{12}$")
SYS_KEYS = tuple(SYS_PHRASES)
STUDIO_SYS_KEYS = (
    "intro",
    "poly_prefix",
    "poly_suffix",
    "outro",
    *(f"g{i}" for i in range(1, MAX_GROUPS + 1)),
)
MAX_FILE_BYTES = 8 * 1024 * 1024


class BundleError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_sha256() -> str:
    digest = hashlib.sha256()
    for path in DATA_FILES:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def expected_word_files() -> dict[str, str]:
    items, manifest = collect_targets([str(CONTENT_ROOT / "knowledge_points.json")])
    del items
    return {f"audio/w/{item['hash']}.mp3": text for text, item in manifest.items()}


def expected_system_files() -> dict[str, str]:
    return {f"audio/sys/{key}.mp3": SYS_PHRASES[key] for key in SYS_KEYS}


def expected_baseline_files() -> dict[str, str]:
    return {**expected_word_files(), **expected_system_files()}


def load_object(path: Path, *, missing_ok: bool = True) -> dict[str, Any]:
    if not path.exists() and missing_ok:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"无法读取 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BundleError(f"{path} 必须是 JSON 对象")
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def looks_like_mp3(data: bytes) -> bool:
    if len(data) < 4:
        return False
    if data.startswith(b"ID3"):
        return True
    # MPEG audio frame sync: eleven leading 1 bits.
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def read_mp3(path: Path) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BundleError(f"缺少音频: {path}") from exc
    if size <= 0 or size > MAX_FILE_BYTES:
        raise BundleError(f"音频大小异常: {path} ({size} bytes)")
    data = path.read_bytes()
    if not looks_like_mp3(data):
        raise BundleError(f"文件不是 MP3（可能只是 WebM 改了扩展名）: {path}")
    return data


def validate_recording_ledgers(
    recorded: Any, recorded_sys: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(recorded, dict) or not isinstance(recorded_sys, dict):
        raise BundleError("真人录音台账必须是 JSON 对象")
    expected_words = expected_word_files()
    clean_words: dict[str, Any] = {}
    for word_hash, entry in recorded.items():
        if not HASH_RE.fullmatch(word_hash) or f"audio/w/{word_hash}.mp3" not in expected_words:
            raise BundleError(f"真人录音台账包含未知词条: {word_hash}")
        if not isinstance(entry, dict):
            raise BundleError(f"真人录音台账格式错误: {word_hash}")
        text = str(entry.get("text", ""))
        if expected_words[f"audio/w/{word_hash}.mp3"] != text:
            raise BundleError(f"真人录音台账 text/hash 不匹配: {word_hash} {text}")
        clean_words[word_hash] = entry

    clean_sys: dict[str, Any] = {}
    for key, entry in recorded_sys.items():
        if key not in STUDIO_SYS_KEYS:
            raise BundleError(f"系统录音台账包含未知项目: {key}")
        if not isinstance(entry, dict):
            raise BundleError(f"系统录音台账格式错误: {key}")
        clean_sys[key] = entry
    return clean_words, clean_sys


def canonical_recorded_ledgers(audio_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return validate_recording_ledgers(
        load_object(audio_dir / ".recorded.json"),
        load_object(audio_dir / ".recorded_sys.json"),
    )


def manifest_for_files(kind: str, files: dict[str, bytes], state: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "kind": kind,
        "dataset_sha256": dataset_sha256(),
        "files": [
            {"path": name, "bytes": len(data), "sha256": sha256_bytes(data)}
            for name, data in sorted(files.items())
        ],
    }
    if state is not None:
        manifest["state"] = state
    return manifest


def tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    return info


def write_bundle(output: Path, manifest: dict[str, Any], files: dict[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".part", dir=output.parent)
    os.close(fd)
    try:
        with open(tmp_name, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                    archive.addfile(tar_info("bundle.json", len(manifest_data)), io.BytesIO(manifest_data))
                    for name, data in sorted(files.items()):
                        archive.addfile(tar_info(name, len(data)), io.BytesIO(data))
        os.replace(tmp_name, output)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def pack_baseline(audio_dir: Path, output: Path) -> None:
    recorded, recorded_sys = canonical_recorded_ledgers(audio_dir)
    if recorded or recorded_sys:
        raise BundleError("该目录包含真人录音台账，不能作为纯 TTS 基线包")
    files = {
        name: read_mp3(audio_dir / name.removeprefix("audio/"))
        for name in expected_baseline_files()
    }
    write_bundle(output, manifest_for_files(BASELINE_KIND, files), files)
    print(f"[OK] TTS 基线包: {output} ({len(files)} 个 MP3, sha256={sha256_file(output)})")


def pack_human(audio_dir: Path, output: Path) -> None:
    recorded, recorded_sys = canonical_recorded_ledgers(audio_dir)
    if not recorded and not recorded_sys:
        raise BundleError("没有登记过的真人录音")
    names = [f"audio/w/{key}.mp3" for key in recorded]
    names.extend(f"audio/sys/{key}.mp3" for key in recorded_sys)
    files = {
        name: read_mp3(audio_dir / name.removeprefix("audio/"))
        for name in names
    }
    state = {"recorded": recorded, "recorded_sys": recorded_sys}
    write_bundle(output, manifest_for_files(HUMAN_KIND, files, state), files)
    print(f"[OK] 真人录音包: {output} ({len(files)} 个 MP3, sha256={sha256_file(output)})")


def read_bundle(bundle: Path, expected_kind: str | None = None) -> tuple[dict[str, Any], dict[str, bytes]]:
    try:
        archive = tarfile.open(bundle, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise BundleError(f"无法打开资源包 {bundle}: {exc}") from exc
    with archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or "bundle.json" not in names:
            raise BundleError("资源包目录重复或缺少 bundle.json")
        if any(not member.isfile() for member in members):
            raise BundleError("资源包只能包含普通文件")
        manifest_member = archive.getmember("bundle.json")
        if manifest_member.size > 4 * 1024 * 1024:
            raise BundleError("bundle.json 过大")
        manifest_stream = archive.extractfile(manifest_member)
        if manifest_stream is None:
            raise BundleError("无法读取 bundle.json")
        try:
            manifest = json.loads(manifest_stream.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleError(f"bundle.json 无效: {exc}") from exc

        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA_VERSION:
            raise BundleError("不支持的资源包格式")
        kind = manifest.get("kind")
        if kind not in (BASELINE_KIND, HUMAN_KIND):
            raise BundleError(f"未知资源包类型: {kind}")
        if expected_kind and kind != expected_kind:
            raise BundleError(f"资源包类型应为 {expected_kind}，实际为 {kind}")
        if manifest.get("dataset_sha256") != dataset_sha256():
            raise BundleError("资源包与当前教材 JSON/Studio 清单不匹配")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise BundleError("bundle.json files 格式错误")

        allowed = expected_baseline_files() if kind == BASELINE_KIND else None
        files: dict[str, bytes] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise BundleError("bundle.json 文件项格式错误")
            name = entry.get("path")
            size = entry.get("bytes")
            checksum = entry.get("sha256")
            if not isinstance(name, str) or not isinstance(size, int) or not isinstance(checksum, str):
                raise BundleError("bundle.json 文件字段格式错误")
            valid_word = bool(re.fullmatch(r"audio/w/[0-9a-f]{12}\.mp3", name))
            valid_sys = bool(re.fullmatch(r"audio/sys/[A-Za-z0-9_]+\.mp3", name))
            if not (valid_word or valid_sys) or size <= 0 or size > MAX_FILE_BYTES:
                raise BundleError(f"非法文件项: {name}")
            if allowed is not None and name not in allowed:
                raise BundleError(f"TTS 基线包含未知文件: {name}")
            if name not in names:
                raise BundleError(f"资源包缺少文件: {name}")
            stream = archive.extractfile(archive.getmember(name))
            if stream is None:
                raise BundleError(f"无法读取文件: {name}")
            data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) != size or sha256_bytes(data) != checksum or not looks_like_mp3(data):
                raise BundleError(f"文件校验失败: {name}")
            files[name] = data

        if set(names) != {"bundle.json", *files}:
            raise BundleError("资源包含未登记文件")
        if kind == BASELINE_KIND and set(files) != set(expected_baseline_files()):
            raise BundleError("TTS 基线文件不完整")
        if kind == HUMAN_KIND:
            state = manifest.get("state")
            if not isinstance(state, dict):
                raise BundleError("真人录音包缺少 state")
            recorded, recorded_sys = validate_recording_ledgers(
                state.get("recorded"), state.get("recorded_sys")
            )
            manifest["state"] = {"recorded": recorded, "recorded_sys": recorded_sys}
            expected_human = {
                *(f"audio/w/{key}.mp3" for key in recorded),
                *(f"audio/sys/{key}.mp3" for key in recorded_sys),
            }
            if set(files) != expected_human:
                raise BundleError("真人录音文件与台账不一致")
        return manifest, files


def install_bundle(bundle: Path, audio_dir: Path, kind: str, reset_review_state: bool) -> None:
    manifest, files = read_bundle(bundle, kind)
    audio_dir.mkdir(parents=True, exist_ok=True)
    if kind == BASELINE_KIND:
        recorded, recorded_sys = canonical_recorded_ledgers(audio_dir)
        if recorded or recorded_sys:
            raise BundleError("运行目录已有真人录音，拒绝用 TTS 资源包覆盖")
    for name, data in files.items():
        relative = Path(name).relative_to("audio")
        dest = audio_dir / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, dest)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    if kind == HUMAN_KIND:
        state = manifest["state"]
        recorded = load_object(audio_dir / ".recorded.json")
        recorded.update(state.get("recorded", {}))
        recorded_sys = load_object(audio_dir / ".recorded_sys.json")
        recorded_sys.update(state.get("recorded_sys", {}))
        atomic_write_json(audio_dir / ".recorded.json", recorded)
        atomic_write_json(audio_dir / ".recorded_sys.json", recorded_sys)
        if reset_review_state:
            atomic_write_json(audio_dir / ".checked.json", {})
            atomic_write_json(audio_dir / ".rerecord.json", {"created_at": "", "words": []})
    print(f"[OK] 已安装 {kind}: {len(files)} 个 MP3 → {audio_dir}")


def inventory(audio_dir: Path) -> None:
    expected = expected_baseline_files()
    missing = []
    invalid = []
    for name in expected:
        path = audio_dir / name.removeprefix("audio/")
        if not path.exists():
            missing.append(name)
            continue
        try:
            read_mp3(path)
        except BundleError:
            invalid.append(name)
    actual = {
        f"audio/{path.relative_to(audio_dir).as_posix()}"
        for path in audio_dir.rglob("*.mp3")
        if path.is_file()
    } if audio_dir.exists() else set()
    extra = sorted(actual - set(expected))
    ledger_error = ""
    try:
        canonical_recorded_ledgers(audio_dir)
    except BundleError as exc:
        ledger_error = str(exc)
    result = {
        "dataset_sha256": dataset_sha256(),
        "expected": len(expected),
        "words": len(expected_word_files()),
        "system": len(expected_system_files()),
        "missing": missing,
        "invalid": invalid,
        "extra": extra,
        "ledger_error": ledger_error,
        "complete": not missing and not invalid and not extra and not ledger_error,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not result["complete"]:
        raise BundleError(
            f"音频不完整：缺少 {len(missing)}，无效 {len(invalid)}，"
            f"多余 {len(extra)}，台账错误 {bool(ledger_error)}"
        )


def build_dataset_manifest(content_root: Path) -> None:
    if content_root.resolve() != CONTENT_ROOT.resolve():
        raise BundleError(
            f"当前进程绑定教材目录 {CONTENT_ROOT}；如需其他目录请设置 DICTATION_CONTENT_ROOT"
        )
    lessons_path = content_root / "lessons.json"
    kp_path = content_root / "knowledge_points.json"
    studio_path = content_root / "studio_manifest.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    knowledge_points = json.loads(kp_path.read_text(encoding="utf-8"))
    studio = json.loads(studio_path.read_text(encoding="utf-8"))
    if not all(isinstance(value, list) for value in (lessons, knowledge_points, studio)):
        raise BundleError("教材 JSON 顶层必须都是数组")

    audio_files: list[tuple[str, Path]] = []
    for relative in sorted(expected_baseline_files()):
        path = content_root / "tts" / relative.removeprefix("audio/")
        read_mp3(path)
        audio_files.append((path.relative_to(content_root).as_posix(), path))

    checksum_text = "".join(
        f"{sha256_file(path)}  {relative}\n" for relative, path in audio_files
    ).encode("utf-8")
    checksum_path = content_root / "tts.sha256"
    atomic_write_bytes(checksum_path, checksum_text)

    categories: dict[str, int] = {}
    for item in knowledge_points:
        if isinstance(item, dict):
            category = str(item.get("category", ""))
            categories[category] = categories.get(category, 0) + 1
    metadata = {
        "schema_version": 1,
        "id": "chinese-3a",
        "display_name": "人教版小学语文三年级上册",
        "language": "zh-CN",
        "subject": "chinese",
        "grade": 3,
        "semester": "first",
        "paths": {
            "lessons": "lessons.json",
            "knowledge_points": "knowledge_points.json",
            "studio_manifest": "studio_manifest.json",
            "tts": "tts",
            "tts_checksums": "tts.sha256",
        },
        "counts": {
            "lessons": len(lessons),
            "knowledge_points": len(knowledge_points),
            "studio_words": len(studio),
            "tts_words": len(expected_word_files()),
            "tts_system": len(expected_system_files()),
            "categories": dict(sorted(categories.items())),
        },
        "tts": {
            "provider": "youdao",
            "voice": "youxiaoxun",
            "speed": 0.6,
            "format": "mp3",
        },
        "sha256": {
            "lessons": sha256_file(lessons_path),
            "knowledge_points": sha256_file(kp_path),
            "studio_manifest": sha256_file(studio_path),
            "tts_checksums": sha256_file(checksum_path),
            "dataset": dataset_sha256(),
        },
    }
    atomic_write_json(content_root / "dataset.json", metadata)
    print(
        f"[OK] 教材清单: {content_root / 'dataset.json'} "
        f"({len(lessons)} 课程, {len(knowledge_points)} 知识点, {len(audio_files)} 音频)"
    )


def verify_dataset_manifest(content_root: Path) -> None:
    if content_root.resolve() != CONTENT_ROOT.resolve():
        raise BundleError(
            f"当前进程绑定教材目录 {CONTENT_ROOT}；如需其他目录请设置 DICTATION_CONTENT_ROOT"
        )
    metadata = load_object(content_root / "dataset.json", missing_ok=False)
    if (
        metadata.get("schema_version") != 1
        or metadata.get("id") != "chinese-3a"
        or metadata.get("language") != "zh-CN"
        or metadata.get("subject") != "chinese"
        or metadata.get("grade") != 3
        or metadata.get("semester") != "first"
    ):
        raise BundleError("dataset.json 格式或教材 ID 错误")

    lessons_path = content_root / "lessons.json"
    kp_path = content_root / "knowledge_points.json"
    studio_path = content_root / "studio_manifest.json"
    checksum_path = content_root / "tts.sha256"
    actual_hashes = {
        "lessons": sha256_file(lessons_path),
        "knowledge_points": sha256_file(kp_path),
        "studio_manifest": sha256_file(studio_path),
        "tts_checksums": sha256_file(checksum_path),
        "dataset": dataset_sha256(),
    }
    if metadata.get("sha256") != actual_hashes:
        raise BundleError("dataset.json 中的教材 SHA-256 与文件不一致")

    expected_paths = {
        f"tts/{name.removeprefix('audio/')}" for name in expected_baseline_files()
    }
    actual_paths = {
        path.relative_to(content_root).as_posix()
        for path in (content_root / "tts").rglob("*.mp3")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise BundleError("教材 TTS 目录包含缺失或未登记的 MP3")
    listed: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        parts = line.split("  ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise BundleError("tts.sha256 格式错误")
        checksum, relative = parts
        if relative in listed:
            raise BundleError(f"tts.sha256 路径重复: {relative}")
        listed[relative] = checksum
    if set(listed) != expected_paths:
        raise BundleError("tts.sha256 与教材要求的音频文件集合不一致")
    for relative, checksum in listed.items():
        path = content_root / relative
        read_mp3(path)
        if sha256_file(path) != checksum:
            raise BundleError(f"TTS 校验失败: {relative}")

    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    knowledge_points = json.loads(kp_path.read_text(encoding="utf-8"))
    studio = json.loads(studio_path.read_text(encoding="utf-8"))
    if not all(isinstance(value, list) for value in (lessons, knowledge_points, studio)):
        raise BundleError("教材 JSON 顶层必须都是数组")
    categories: dict[str, int] = {}
    for item in knowledge_points:
        if not isinstance(item, dict):
            raise BundleError("knowledge_points.json 的项目必须是对象")
        category = str(item.get("category", ""))
        categories[category] = categories.get(category, 0) + 1

    counts = metadata.get("counts", {})
    expected_counts = {
        "lessons": 43,
        "knowledge_points": 814,
        "studio_words": 869,
        "tts_words": 869,
        "tts_system": 25,
        "categories": {"多音字": 38, "易错字": 108, "生字": 250, "词语": 418},
    }
    actual_counts = {
        "lessons": len(lessons),
        "knowledge_points": len(knowledge_points),
        "studio_words": len(studio),
        "tts_words": len(expected_word_files()),
        "tts_system": len(expected_system_files()),
        "categories": dict(sorted(categories.items())),
    }
    if (
        not isinstance(counts, dict)
        or counts != expected_counts
        or actual_counts != expected_counts
    ):
        raise BundleError("dataset.json 的冻结数量不正确")
    print(
        f"[OK] 教材包完整: {metadata.get('display_name')} "
        f"({len(listed)} 音频, dataset={actual_hashes['dataset'][:12]})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("pack-baseline", "pack-human"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)
        cmd.add_argument("--output", type=Path, required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--kind", choices=(BASELINE_KIND, HUMAN_KIND))

    install = sub.add_parser("install")
    install.add_argument("--bundle", type=Path, required=True)
    install.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)
    install.add_argument("--kind", choices=(BASELINE_KIND, HUMAN_KIND), required=True)
    install.add_argument("--reset-review-state", action="store_true")

    inv = sub.add_parser("inventory")
    inv.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)

    dataset = sub.add_parser("build-dataset")
    dataset.add_argument("--content-root", type=Path, default=CONTENT_ROOT)

    verify_dataset = sub.add_parser("verify-dataset")
    verify_dataset.add_argument("--content-root", type=Path, default=CONTENT_ROOT)

    args = parser.parse_args()
    try:
        if args.command == "pack-baseline":
            pack_baseline(args.audio_dir, args.output)
        elif args.command == "pack-human":
            pack_human(args.audio_dir, args.output)
        elif args.command == "verify":
            manifest, files = read_bundle(args.bundle, args.kind)
            print(f"[OK] {manifest['kind']}: {len(files)} 个 MP3, sha256={sha256_file(args.bundle)}")
        elif args.command == "install":
            install_bundle(args.bundle, args.audio_dir, args.kind, args.reset_review_state)
        elif args.command == "inventory":
            inventory(args.audio_dir)
        elif args.command == "build-dataset":
            build_dataset_manifest(args.content_root)
        elif args.command == "verify-dataset":
            verify_dataset_manifest(args.content_root)
        return 0
    except BundleError as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
