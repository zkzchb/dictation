# Dictation

[简体中文](README.md) | English

Dictation is a self-hosted Chinese literacy practice application for families and schools. It provides daily dictation, unit review, missed-word recycling, polyphonic-character rotation, learning records, and a recording studio for local or VPS deployments. The program is not tied to a publisher, grade, or fixed lesson numbering: courses, knowledge points, and audio are loaded from external content packs.

The current stable program release is [`v2.1.0`](https://github.com/zkzchb/dictation/releases/tag/v2.1.0). Its compatible public content release is [`content-v1.0.0`](https://github.com/zkzchb/dictation-content/releases/tag/content-v1.0.0).

## Project vision

Dictation is not intended as a centralized SaaS tied to fixed curricula and voices. It is a self-hosted runtime for runnable, versioned learning materials. Its guiding principle is: **stateless runtime, versioned materials, owned records, explainable feedback.**

See [Project vision](docs/PROJECT-VISION.en.md) / [项目愿景](docs/PROJECT-VISION.md).

## Two repositories

| Repository | Responsibility | Release cadence |
|---|---|---|
| [`zkzchb/dictation`](https://github.com/zkzchb/dictation) | V2/V3 application, content-pack specification, deployment, and tests | Stable, low frequency |
| [`zkzchb/dictation-content`](https://github.com/zkzchb/dictation-content) | Curriculum JSON, recordings, manifests, and content documentation | Independently versioned |

The application repository contains only a small CC0 synthetic test fixture. It does not publish the production curriculum or product recordings. Deployment records capture the program commit, content commit, content release, pack ID, and dataset SHA-256 so an installation can be reproduced.

## Runtimes and product editions

V2/V3 and the three Editions describe different dimensions. **V2/V3 are technical runtimes and deployment targets; an Edition is a user-facing product profile, not another code version or branch.** One Edition may run on different runtimes, and one V2 deployment may provide both personal practice and the content Studio.

### Technical runtimes

| Runtime | Target | Database | Static audio | Recording Studio |
|---|---|---|---|---|
| V2 | Local Ubuntu or Ubuntu VPS | SQLite | Local filesystem | Supported |
| V3 | Cloudflare Workers | D1 | Worker static assets | Not supported |

V2 and V3 share the content-pack v1 contract and selection rules. Seeded parity tests verify their SQLite and D1 selectors, so the same learning pack can be staged for either runtime.

### Product editions

| Product edition | Current status | Runtime and deployment relationship | Current scope and boundary |
|---|---|---|---|
| Personal Edition | Available | V3 on Workers/D1 is the low-operations reference; V2 local/VPS is also usable and additionally provides Studio | Personal or family dictation, review, and learning records. V3 has no Studio. This is not yet a fully authenticated multi-user service; access protection remains the operator’s responsibility |
| Teacher Studio Edition | Core workflow available | Currently uses V2 on local Ubuntu or VPS; it combines the V2 learning runtime with Studio workflows | Recording, re-recording, and human quality review are available, and maintained learning packs can be consumed by V2 or V3. A fuller import, editing, and publishing experience remains roadmap work |
| Classroom Edition | Planned | Intended for a self-hosted VPS or school server; the final runtime has not been selected | Classes, students, assignments, multiple materials, progress, and teaching feedback are future work. v2.1.0 does not include complete multi-user authentication or classroom collaboration |

In short: **V2/V3 answer “how the program runs”; the Editions answer “who uses it and for what.” They are not a one-to-one mapping.**

## Five-minute local start

Pin both repositories to release tags so two moving `main` branches cannot drift during installation:

```bash
mkdir dictation-workspace && cd dictation-workspace
git clone --branch content-v1.0.0 https://github.com/zkzchb/dictation-content.git
git clone --branch v2.1.0 https://github.com/zkzchb/dictation.git
cd dictation
cp deploy/local.env.example deploy/local.env
bash deploy/local-install.sh --serve
```

The default content root is `../dictation-content/packs/zh-cn/primary-3a`, and runtime state is written to `.runtime/local`. Open `http://localhost:8889`.

To use another pack, change `CONTENT_ROOT` in `deploy/local.env`. The installer validates its schema, digests, and audio manifest before building or synchronizing the database. Destructive deletion, stable-ID reassignment, and incompatible pack switching are rejected.

## Deployment guides

- [Local Ubuntu](DEPLOY-local.md)
- [Ubuntu VPS](DEPLOY-vps.md)
- [Cloudflare Workers + D1](DEPLOY-cloudflare.md)

The recommended VPS layout separates immutable program and content checkouts from mutable user state:

```text
/opt/
├── dictation/                  read-only program checkout
└── dictation-content/          read-only content checkout

/var/lib/dictation/             SQLite, runtime audio, recordings, and deployment ledger
```

During an upgrade, the installer migrates an older in-repository database and runtime audio into the independent state directory while retaining rollback copies.

## Content-pack compatibility

`dataset.json` is the only content-pack entry point. Every knowledge point has a stable integer ID that cannot be reused after publication. Compatible updates may append lessons and knowledge points or revise an existing item while preserving its semantic identity. Deletion, renumbering, or changing the pack ID requires a separate database.

```bash
python3 shared/content_pack.py ../dictation-content/packs/zh-cn/primary-3a
python3 shared/sync_content.py \
  --db .runtime/local/v2/dictation.db \
  --content-root ../dictation-content/packs/zh-cn/primary-3a
```

See the [content-pack v1 specification](docs/CONTENT-PACK-SPEC.md), [repository architecture](docs/REPOSITORY-ARCHITECTURE.md), and [content licensing boundary](docs/CONTENT-LICENSING.md).

## Development and verification

```bash
python3 -m venv .testenv
.testenv/bin/pip install -r v2/requirements.txt
DICTATION_CONTENT_ROOT=tests/fixtures/demo-content-pack \
  .testenv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
python3 -m compileall -q v2 v3 shared tools tests
for script in deploy/*.sh; do bash -n "$script"; done
python3 tests/check_inline_js.py
```

The test fixture contains no audio. Audio packaging, verification, and installation tests generate minimal synthetic MP3 files in temporary directories and do not depend on the public content repository.

## Project layout

```text
dictation/
├── v2/                 FastAPI / SQLite API
├── v3/                 Cloudflare Python Worker / D1
├── shared/             selection, database, synchronization, and content-pack tools
├── shared/web/         shared V2/V3 web frontend source
├── deploy/             local, VPS, and Cloudflare deployment entry points
├── tools/              V2/V3 static-asset preparation
├── tests/              regression tests and synthetic content fixture
└── docs/               specifications, architecture, licensing, and release evidence
```

The historical `v2.0.0` release and `v2.0-stable` branch remain available as a rollback baseline. The maintained product line contains V2 and V3 only.

Sanitized v2.1 acceptance evidence is preserved under [`docs/verification`](docs/verification/) and in [PR #30](https://github.com/zkzchb/dictation/pull/30). Raw machine logs, credentials, addresses, and user data remain outside the public repository.

## License

The application is licensed under [GNU AGPL-3.0](LICENSE). Content packs are independent works and must declare their own sources, authorship, licensing, and audio rights. The program license does not automatically relicense a content pack. See [NOTICE](NOTICE.md) for details.
