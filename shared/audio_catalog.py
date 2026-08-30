"""Runtime audio catalog helpers shared by V2 and packaging tools.

This module deliberately has no network, TTS-provider, or content-specific
dependencies.  The selected content pack supplies knowledge points; this
module only defines the stable system prompts and derives the audio filenames
from a pack's options.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable


MAX_GROUPS = int(os.getenv("MAX_GROUPS", "20"))
SYS_PHRASES = {
    "intro": "准备听写。每三个词为一组，报三遍。",
    "outro": "听写完毕，请检查后交卷。",
    "poly_prefix": "多音字：",
    "poly_suffix": "，请组词并默写，标注拼音。",
    # Kept for compatibility with old packs; current frontends do not play it.
    "poly_intro": "下面是多音字。请写出不同读音的组词，并标上拼音。",
    **{f"g{n}": f"第{n}组。" for n in range(1, MAX_GROUPS + 1)},
}


def word_hash(text: str) -> str:
    """Return the 12-character content-addressed audio name for ``text``."""

    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def collect_targets(kp_files: Iterable[str | Path]):
    """Derive word audio targets and a studio manifest from knowledge points.

    Returns ``(items, manifest)`` where ``items`` is a list of
    ``(text-with-pause, relative-file-path)`` pairs and ``manifest`` maps the
    original text to ``url``, ``pinyin`` and ``hash`` fields.

    Multi-pronunciation knowledge points use the target character itself;
    other categories include every candidate option so randomly selected
    examples always have a corresponding audio file.
    """

    texts: dict[str, str] = {}
    for raw_path in kp_files:
        path = Path(raw_path)
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for kp in data:
            if not isinstance(kp, dict):
                continue
            category = kp.get("category", "")
            options = kp.get("options_json") or []
            if isinstance(options, str):
                try:
                    options = json.loads(options)
                except json.JSONDecodeError:
                    options = []
            if category == "多音字":
                target = str(kp.get("target") or "").strip()
                if target:
                    texts.setdefault(target, "")
                continue
            for option in options:
                if not isinstance(option, dict):
                    continue
                text = str(option.get("text") or "").strip()
                if text:
                    texts.setdefault(text, str(option.get("pinyin") or ""))
            if not options:
                target = str(kp.get("target") or "").strip()
                if target:
                    texts.setdefault(target, "")

    items = []
    manifest = {}
    for text, pinyin in sorted(texts.items()):
        digest = word_hash(text)
        items.append((f"{text}。", os.path.join("audio", "w", f"{digest}.mp3")))
        manifest[text] = {
            "url": f"/audio/w/{digest}.mp3",
            "pinyin": pinyin,
            "hash": digest,
        }
    return items, manifest

