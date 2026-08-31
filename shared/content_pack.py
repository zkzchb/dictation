"""Content-pack v1 loader and validator.

The application code consumes a content pack through ``dataset.json`` instead
of relying on a specific grade, semester, or repository path.  This module is
stdlib-only so deployment and release tooling can validate a pack before any
runtime dependencies are installed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTENT_ROOT = Path(
    os.getenv(
        "DICTATION_CONTENT_ROOT",
        ROOT.parent / "dictation-content" / "packs" / "zh-cn" / "primary-3a",
    )
)
PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
STUDIO_HASH_RE = re.compile(r"^[0-9a-f]{12}$")
SUPPORTED_CATEGORIES = frozenset({"生字", "词语", "易错字", "多音字"})


class ContentPackError(RuntimeError):
    """Raised when a content pack is unsafe, inconsistent, or unsupported."""


@dataclass(frozen=True)
class RuntimeConfig:
    cold_start_lesson: int | None
    initial_lesson: int
    review_lessons: frozenset[int]
    daily_target: int
    review_target: int
    polyphonic_per_lesson: int

    def is_review_lesson(self, lesson_seq: int) -> bool:
        return lesson_seq in self.review_lessons


@dataclass(frozen=True)
class ContentPack:
    root: Path
    metadata: dict[str, Any]
    lessons: tuple[dict[str, Any], ...]
    knowledge_points: tuple[dict[str, Any], ...]
    studio_manifest: tuple[dict[str, Any], ...]
    runtime: RuntimeConfig
    paths: dict[str, Path]
    dataset_sha256: str

    @property
    def id(self) -> str:
        return str(self.metadata["id"])

    @property
    def display_name(self) -> str:
        return str(self.metadata["display_name"])


def _read_json(path: Path, expected: type) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentPackError(f"无法读取 {path}: {exc}") from exc
    if not isinstance(value, expected):
        raise ContentPackError(f"{path.name} 顶层必须是 {expected.__name__}")
    return value


def _safe_path(root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ContentPackError(f"dataset.json paths.{field} 必须是非空相对路径")
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts:
        raise ContentPackError(f"dataset.json paths.{field} 不能越出内容包: {value}")
    resolved_root = root.resolve()
    resolved = (resolved_root / Path(*posix.parts)).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ContentPackError(f"dataset.json paths.{field} 不能越出内容包: {value}")
    return resolved


def _positive_int(value: Any, field: str, *, minimum: int = 1, maximum: int = 500) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ContentPackError(f"dataset.json runtime.{field} 必须在 {minimum}..{maximum} 之间")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ContentPackError(f"无法读取 {path}: {exc}") from exc
    return digest.hexdigest()


def dataset_digest(paths: dict[str, Path]) -> str:
    digest = hashlib.sha256()
    for key in ("lessons", "knowledge_points", "studio_manifest"):
        path = paths[key]
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError as exc:
            raise ContentPackError(f"无法读取 {path}: {exc}") from exc
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_lessons(items: list[Any]) -> tuple[dict[str, Any], ...]:
    lessons: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ContentPackError(f"lessons.json[{index}] 必须是对象")
        seq = item.get("lesson_seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
            raise ContentPackError(f"lessons.json[{index}].lesson_seq 必须是正整数")
        if seq in seen:
            raise ContentPackError(f"lessons.json lesson_seq 重复: {seq}")
        seen.add(seq)
        for field in ("unit_id", "unit_name", "lesson_name"):
            value = item.get(field)
            if field == "unit_id":
                valid = isinstance(value, int) and not isinstance(value, bool) and value > 0
            else:
                valid = isinstance(value, str) and bool(value.strip())
            if not valid:
                raise ContentPackError(f"lessons.json[{index}].{field} 无效")
        lessons.append(item)
    if not lessons:
        raise ContentPackError("lessons.json 不能为空")
    return tuple(lessons)


def _validate_knowledge_points(
    items: list[Any], lesson_ids: set[int]
) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ContentPackError(f"knowledge_points.json[{index}] 必须是对象")
        # v2.0 files had no explicit IDs, so their stable legacy identity is the
        # one-based array position. New packs should write this value explicitly
        # before they start publishing independent content updates.
        point_id = item.get("id", index + 1)
        if (
            isinstance(point_id, bool)
            or not isinstance(point_id, int)
            or point_id <= 0
            or point_id > 2_147_483_647
        ):
            raise ContentPackError(f"knowledge_points.json[{index}].id 必须是正整数")
        if point_id in seen_ids:
            raise ContentPackError(f"knowledge_points.json id 重复: {point_id}")
        seen_ids.add(point_id)
        seq = item.get("lesson_seq")
        if seq not in lesson_ids:
            raise ContentPackError(
                f"knowledge_points.json[{index}] 引用了不存在的 lesson_seq: {seq}"
            )
        target = item.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ContentPackError(f"knowledge_points.json[{index}].target 无效")
        category = item.get("category")
        if category not in SUPPORTED_CATEGORIES:
            raise ContentPackError(
                f"knowledge_points.json[{index}].category 不受支持: {category}"
            )
        if not isinstance(item.get("options_json"), list):
            raise ContentPackError(f"knowledge_points.json[{index}].options_json 必须是数组")
        normalized = dict(item)
        normalized["id"] = point_id
        result.append(normalized)
    if not result:
        raise ContentPackError("knowledge_points.json 不能为空")
    return tuple(result)


def _validate_studio(items: list[Any]) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    texts: set[str] = set()
    hashes: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ContentPackError(f"studio_manifest.json[{index}] 必须是对象")
        text = item.get("text")
        word_hash = item.get("hash")
        if not isinstance(text, str) or not text.strip():
            raise ContentPackError(f"studio_manifest.json[{index}].text 无效")
        if not isinstance(word_hash, str) or not STUDIO_HASH_RE.fullmatch(word_hash):
            raise ContentPackError(f"studio_manifest.json[{index}].hash 无效")
        expected = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
        if word_hash != expected:
            raise ContentPackError(f"studio_manifest.json text/hash 不匹配: {text}")
        if text in texts or word_hash in hashes:
            raise ContentPackError(f"studio_manifest.json 词条重复: {text}")
        texts.add(text)
        hashes.add(word_hash)
        result.append(item)
    if not result:
        raise ContentPackError("studio_manifest.json 不能为空")
    return tuple(result)


def _runtime_config(metadata: dict[str, Any], lesson_ids: set[int]) -> RuntimeConfig:
    raw = metadata.get("runtime", {})
    if not isinstance(raw, dict):
        raise ContentPackError("dataset.json runtime 必须是对象")

    cold = raw.get("cold_start_lesson", 3000 if 3000 in lesson_ids else None)
    if cold is not None and (isinstance(cold, bool) or not isinstance(cold, int) or cold not in lesson_ids):
        raise ContentPackError("dataset.json runtime.cold_start_lesson 不存在")

    review_raw = raw.get("review_lessons")
    if review_raw is None:
        review = {seq for seq in lesson_ids if seq % 10 == 0 and seq != cold}
    else:
        if not isinstance(review_raw, list) or any(
            isinstance(seq, bool) or not isinstance(seq, int) for seq in review_raw
        ):
            raise ContentPackError("dataset.json runtime.review_lessons 必须是整数数组")
        review = set(review_raw)
        if len(review) != len(review_raw) or not review <= lesson_ids or cold in review:
            raise ContentPackError("dataset.json runtime.review_lessons 包含重复或未知课程")

    regular = sorted(lesson_ids - review - ({cold} if cold is not None else set()))
    if not regular:
        raise ContentPackError("内容包至少需要一门正式课")
    initial = raw.get("initial_lesson", regular[0])
    if isinstance(initial, bool) or not isinstance(initial, int) or initial not in regular:
        raise ContentPackError("dataset.json runtime.initial_lesson 必须是一门正式课")

    return RuntimeConfig(
        cold_start_lesson=cold,
        initial_lesson=initial,
        review_lessons=frozenset(review),
        daily_target=_positive_int(raw.get("daily_target", 30), "daily_target"),
        review_target=_positive_int(raw.get("review_target", 50), "review_target"),
        polyphonic_per_lesson=_positive_int(
            raw.get("polyphonic_per_lesson", 2),
            "polyphonic_per_lesson",
            minimum=0,
            maximum=20,
        ),
    )


def _validate_counts(
    metadata: dict[str, Any],
    lessons: tuple[dict[str, Any], ...],
    knowledge_points: tuple[dict[str, Any], ...],
    studio: tuple[dict[str, Any], ...],
) -> None:
    counts = metadata.get("counts")
    if not isinstance(counts, dict):
        raise ContentPackError("dataset.json counts 必须是对象")
    categories: dict[str, int] = {}
    for item in knowledge_points:
        category = str(item["category"])
        categories[category] = categories.get(category, 0) + 1
    expected = {
        "lessons": len(lessons),
        "knowledge_points": len(knowledge_points),
        "studio_words": len(studio),
        "tts_words": len(studio),
        "categories": dict(sorted(categories.items())),
    }
    for field, value in expected.items():
        if counts.get(field) != value:
            raise ContentPackError(
                f"dataset.json counts.{field} 不一致: {counts.get(field)!r} != {value!r}"
            )
    if isinstance(counts.get("tts_system"), bool) or not isinstance(counts.get("tts_system"), int):
        raise ContentPackError("dataset.json counts.tts_system 必须是非负整数")
    if counts["tts_system"] < 0:
        raise ContentPackError("dataset.json counts.tts_system 必须是非负整数")


def _validate_hashes(metadata: dict[str, Any], paths: dict[str, Path], digest: str) -> None:
    expected = metadata.get("sha256")
    if not isinstance(expected, dict):
        raise ContentPackError("dataset.json sha256 必须是对象")
    actual = {
        "lessons": _sha256_file(paths["lessons"]),
        "knowledge_points": _sha256_file(paths["knowledge_points"]),
        "studio_manifest": _sha256_file(paths["studio_manifest"]),
        "dataset": digest,
    }
    if "tts_checksums" in paths:
        actual["tts_checksums"] = _sha256_file(paths["tts_checksums"])
    for field, value in actual.items():
        recorded = expected.get(field)
        if not isinstance(recorded, str) or not HASH_RE.fullmatch(recorded) or recorded != value:
            raise ContentPackError(f"dataset.json sha256.{field} 与文件不一致")


def load_content_pack(
    root: str | os.PathLike[str] | Path | None = None,
    *,
    verify_hashes: bool = True,
) -> ContentPack:
    if root is None:
        root = os.getenv("DICTATION_CONTENT_ROOT", str(DEFAULT_CONTENT_ROOT))
    root_path = Path(root).resolve()
    metadata = _read_json(root_path / "dataset.json", dict)
    if metadata.get("schema_version") != 1:
        raise ContentPackError("只支持 content-pack schema_version=1")
    pack_id = metadata.get("id")
    if not isinstance(pack_id, str) or not PACK_ID_RE.fullmatch(pack_id):
        raise ContentPackError("dataset.json id 必须是安全、稳定的小写标识符")
    for field in ("display_name", "language", "subject"):
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ContentPackError(f"dataset.json {field} 必须是非空字符串")
    version = metadata.get("version")
    if version is not None and (
        not isinstance(version, str) or not VERSION_RE.fullmatch(version)
    ):
        raise ContentPackError("dataset.json version 必须是语义化版本")

    raw_paths = metadata.get("paths")
    if not isinstance(raw_paths, dict):
        raise ContentPackError("dataset.json paths 必须是对象")
    paths = {
        field: _safe_path(root_path, raw_paths.get(field), field)
        for field in ("lessons", "knowledge_points", "studio_manifest")
    }
    for field in ("tts", "tts_checksums"):
        if field in raw_paths:
            paths[field] = _safe_path(root_path, raw_paths[field], field)

    lessons = _validate_lessons(_read_json(paths["lessons"], list))
    lesson_ids = {item["lesson_seq"] for item in lessons}
    knowledge_points = _validate_knowledge_points(
        _read_json(paths["knowledge_points"], list), lesson_ids
    )
    studio = _validate_studio(_read_json(paths["studio_manifest"], list))
    runtime = _runtime_config(metadata, lesson_ids)
    _validate_counts(metadata, lessons, knowledge_points, studio)
    digest = dataset_digest(paths)
    if verify_hashes:
        _validate_hashes(metadata, paths, digest)

    return ContentPack(
        root=root_path,
        metadata=metadata,
        lessons=lessons,
        knowledge_points=knowledge_points,
        studio_manifest=studio,
        runtime=runtime,
        paths=paths,
        dataset_sha256=digest,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("content_root", nargs="?", type=Path, default=DEFAULT_CONTENT_ROOT)
    parser.add_argument(
        "--skip-hashes", action="store_true",
        help="只校验结构，不核对文件 SHA-256（仅用于诊断）",
    )
    args = parser.parse_args()
    try:
        pack = load_content_pack(args.content_root, verify_hashes=not args.skip_hashes)
    except ContentPackError as exc:
        print(f"[X] 内容包校验失败: {exc}", file=sys.stderr)
        return 1
    print(
        f"[OK] {pack.display_name} ({pack.id}): "
        f"{len(pack.lessons)} 门课程, {len(pack.knowledge_points)} 条知识点, "
        f"dataset={pack.dataset_sha256[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
