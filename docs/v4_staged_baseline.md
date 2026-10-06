# v4 staged-set baseline

**Recorded:** 2026-10-05, from the index at HEAD `56f56418`, after the v4 build (hub dispatch 2026-10-05 items 1-2).
**Status:** pending hub re-certification. Supersedes the 2026-08 baseline (`93d89633` / `8cf46c17` / `638956f7` / `b6107d6c`), which no longer matches because the build moved all four blobs.
**Why this is a repo file:** a baseline held only in memory is one cold start from gone. The landing checklist (`docs/v4_landing_checklist.md`, Part E) verifies against this file.

## Blob-checked paths (identity = full blob SHA, from `git ls-files -s`)

| path | blob |
|---|---|
| `prompts/CHANGELOG.md` | `f322d2167b51c150f5a16927b2482941c332a5e8` |
| `prompts/v4.txt` | `d8e38661937aecca196007a830e182d35b7b887b` |
| `src/prompt_builder.py` | `c1ed48236820cf2ae9ac8b1e5b46d11d90da18ce` |
| `tests/test_v4_ablation.py` | `13c40070fe1121e5e0ac853f24e91d5beddaccde` |

`prompts/v4.txt` sha256 (LF): `5a275e2b0eb306cccb48c136f39784817de9f727e32b3154673403643b4c6d07`.

## Delta-checked paths (contended; identity = the added lines, per CLAUDE.md rules 6-7)

| path | index blob | delta | sha256 of added lines |
|---|---|---|---|
| `scripts/build_monthly_data_layer.py` | `82d031cd` | +15 / -0 | `8793ee3ea046fa4cb58471ff29645d53fe9989e7a3a1c12ad74c0b3afa4d678c` |
| `scripts/phase_a_integrity_ledger.json` | `9fb45160` | +17 / -0 | `f50782acea9428ca16737685d0f018cf25b19dfac6aab84b97ebc9c28f64369c` |

The added-lines digest is computed as:

```
git diff --cached -- <path> | grep '^+' | grep -v '^+++' | sha256sum
```

Added lines only, LF form as `git diff` emits it (the ledger is CRLF in the worktree). Zero deletions on both paths.

## Guard patches go stale

The ledger hunk's `v4_equivalence` line was rewritten by the build, so any guard patch recorded **before** 2026-10-05 no longer matches the staged hunk. A stale guard fails the delta-identity check for the wrong reason. The checklist now requires a fresh guard patch before the landing quarantine; see its "Fresh guard patch" precondition.
