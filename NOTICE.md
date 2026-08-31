# Notices and content licensing

Dictation software is licensed under the GNU Affero General Public License
version 3.0 (AGPL-3.0). See LICENSE.

The repository separates software from learning content:

| Material | Public distribution rule |
|---|---|
| Program code, deployment tools, and tests | AGPL-3.0 |
| tests/fixtures/demo-content-pack/ synthetic test data | CC0-1.0; see the license inside that directory |
| Third-party textbook or exercise data | Not included unless the pack provides independently verified source and redistribution terms |
| Text-to-speech output | Not included by default; redistribution depends on the selected TTS provider's terms |
| Pack-author recordings | Distributed only under the license declared by that content pack |
| Runtime recording drafts and ledgers | User data; excluded from program releases |
| Learning history, accounts, and backups | User data; never part of a source release |

A content pack is a separate work and must state its own origin, author, license,
and audio rights. The software license does not grant rights to a separately
installed content pack. The current `primary-3a` pack is published separately
by `zkzchb/dictation-content` under CC BY-NC 4.0; its noncommercial restriction
does not apply to the AGPL-3.0 application code.

The synthetic test pack was written specifically for Dictation v2.1 and does
not copy lesson text, vocabulary lists, ordering, or exercises from a published
textbook or commercial question bank. Production content is maintained in the
separate dictation-content repository.
