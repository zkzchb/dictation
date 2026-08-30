# Dictation repository guidance

This repository contains the AGPL-3.0 Dictation application only. Current
development covers V2 (FastAPI/SQLite), V3 (Cloudflare Workers/D1), shared
content-pack tooling, deployment scripts, tests, and program documentation.
Formal course materials, published recordings, runtime databases, learning history, and
deployment credentials belong outside this repository.

Before submitting a change, run the smallest relevant checks and then the full
regression suite when the change affects selectors, content synchronization,
audio staging, deployment, or persistence:

```bash
DICTATION_CONTENT_ROOT=tests/fixtures/demo-content-pack \
  python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q v2 v3 shared tools tests
python tests/check_inline_js.py
for script in deploy/*.sh; do bash -n "$script"; done
```

## Code Review Rules

### Preserve published content identity

- Flag changes that delete or reuse a published knowledge-point ID, silently
  switch pack IDs, or reinterpret existing learning history. The safe path is
  an additive stable-ID migration, or a new pack/database with an explicit
  compatibility and rollback plan.

### Keep deployment writes failure-safe

- Flag deployment or synchronization code that mutates databases, audio trees,
  ledgers, or generated web assets before the complete content pack and audio
  inventory have been validated. Build replacement state separately and make
  it visible only after validation succeeds.

### Enforce the program/content/data boundary

- Flag credentials, personal infrastructure, production databases, learning
  records, recording ledgers, formal course materials, or product audio entering the program
  tree, logs, fixtures, or release artifacts. Tests must use synthetic or
  clearly redistributable data, and public verification reports must be
  sanitized before commit.
