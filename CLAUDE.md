# LLM Trading Lab — project conventions

## Working-tree discipline (ratified 2026-08-02)

**One session per checkout.** Two agent/editor sessions must never work the
same working tree concurrently — that is how the 2026-08-02 cost-rates edits
appeared unattributed inside the SDK-migration diff. Parallel workstreams go
in a separate git worktree or clone per lane (not branches — see enforcement
rule 3 below); each lands through its own staged-and-halt lane with its own
report. If a session finds working-tree changes it did not make, it stops
and diffs them before staging anything (quarantine via
`git stash push -u -m "<attribution>"` when they belong to another lane).

## Session serialization — enforcement doctrine (hub ruling 2026-08-02)

Three same-checkout races in one evening — the third violating the
convention above in the hour it landed — established that a file-based
convention cannot bind a session already mid-flight and cannot prevent two
sessions being started against one checkout. The convention stands;
enforcement lives in the dispatch process.

1. **One Operations lane at a time, PI-enforced at dispatch.** The hub
   relays one Operations prompt and waits for its HALT or close before
   relaying the next. An outstanding prompt blocks the queue. Parallelism
   caused all three 2026-08-02 races; none of that evening's work needed it.

2. **Push authority is per-package, per-lane.** A lane pushes only the
   package it staged, and only on that package's sign-off. No lane exercises
   another lane's push authority, ever. (The 2026-08-02 mid-run push of the
   reporting lane's commits by the parallel session was benign only because
   that package happened to have sign-off; with divergent content it is an
   unauthorized publish.)

3. **Genuinely parallel work, if ever needed, gets a separate git worktree
   (or clone) per lane**, each landing through its own staged-and-halt.
   Not branches — the monitor-fix episode proved branch-parked work silently
   doesn't run in production. This supersedes the "branches" option
   originally written into the convention above.

4. **The backstop stays primary.** Any working-tree change a session didn't
   make gets stop-and-identify before anything stages, regardless of how
   benign it looks. That instinct kept every 2026-08-02 race harmless, and
   it outranks any convention.

5. **Landing beside another lane's staged index: pathspec commit, and new
   paths need `git add` first** (ratified 2026-08-12). When a lane must land
   while another lane's work sits staged in the same index, commit by
   pathspec — `git commit -- <path>` — which commits only the named path and
   leaves every other index entry untouched. That property is the whole
   point of the ritual; it is what kept the broker lane out of the v4 prompt
   lane's staged set.

   **A bare pathspec commit fails on a new file.** Partial-commit pathspecs
   resolve only against files git already tracks, so an untracked path exits
   with `error: pathspec '<path>' did not match any file(s) known to git`.
   New paths need two steps:

   ```
   git add <path>
   git commit -F <msgfile> -- <path>
   ```

   The `git add` does put the new path into the shared index alongside the
   other lane's entries, but the pathspec on the commit still scopes the
   commit to that path alone, and the other lane's entries survive staged.
   Verify with `git status --short` after committing — the other lane's
   staged set must be byte-identical to before.

   bce1119a landed as a bare pathspec commit because it modified an
   already-tracked path; the 2026-08-12 broker lane could not, because it
   added a new file. Every future new-file lane hits this. Note also that
   `-m` must precede the `--`; anything after `--` is parsed as a pathspec,
   so `git commit -- <path> -m "msg"` fails confusingly. Prefer `-F` with a
   message file for multi-paragraph lane messages.

6. **Integrating past cron ticks while another lane sits staged: merge, never
   rebase, and restore the index by blob hash** (ratified 2026-08-13). The
   intraday cron pushes a tick roughly every 30 minutes, touching `data/`
   only, so a lane that stages and halts is almost always behind
   `origin/main` by the time it lands. When another lane's work is staged in
   the same index at that moment, both obvious integrations are unsafe:

   - `git rebase` requires a clean tree, and `--autostash` restores with
     `git stash apply` (no `--index`), which **flattens the other lane's
     staged set into unstaged**. That is a rule-4 violation executed
     automatically, with no diff to inspect.
   - `git merge` **refuses outright on a dirty index even when no path
     overlaps**: the ort strategy exits `Your local changes to the following
     files would be overwritten by merge`, naming the staged files, though
     the incoming commits never touch them.

   Merge is the correct integration here — it never rewrites the other lane's
   working-tree content — and it is the existing precedent (`7e81bdf4 Merge
   origin/main (automated cron ticks) into Phase-A equity-curve re-anchor`).
   The sequence:

   ```
   git commit -F <msgfile> -- <your paths>      # land your package first (rule 5)
   git write-tree                               # recoverable snapshot of the index
   git ls-files -s -- <other lane's paths>      # RECORD BLOB HASHES
   git diff --name-only -- <other lane's paths> # MUST be empty
   git reset                                    # unstage; worktree untouched
   git merge origin/main -m "<attribution>"
   git add <other lane's paths>                 # restore
   git ls-files -s -- <other lane's paths>      # diff against the recorded hashes
   ```

   Step 4 is the safety precondition, not a formality: the transient unstage
   is only reversible because the worktree already equals the index for those
   paths. Status `M ` / `A ` with a clean second column proves it; anything
   else means unstaging would lose content, and the lane stops.

   Verify restoration by **blob hash, not `git diff --cached --stat`**.
   Matching insertion counts are not identity — two different contents can
   produce the same `--stat` line. Identical blob SHAs are proof.

   Before pushing, confirm `git diff --stat origin/main..HEAD` names only your
   own package's paths. A merge makes it easy to carry another lane's commits
   without noticing, and that is the rule-2 boundary.

   The transient unstage is acceptable only because a staged-and-halted lane
   has no running process to observe it. Never do this while another
   Operations lane is mid-flight — that is rule 1, and it still governs.

7. **Two lanes must never need the same file.** Rule 3 sends parallel work to
   separate worktrees, but a worktree does not resolve *same-file* contention
   — it relocates it. When a lane's package requires editing a path another
   lane already has staged (`scripts/phase_a_integrity_ledger.json` is the
   recurring one, since every lane ledgers), the index cannot represent both
   independently: `git add` produces a combined blob, and a pathspec commit on
   that path then publishes the other lane's staged content under this lane's
   sign-off — rule 2, violated silently. Staging into a contended file also
   makes "the other lane's staged set is byte-identical" unverifiable by
   construction, because that file *is* the other lane's staged set.

   There is no in-index workaround. The resolutions are serialization (let the
   holding lane land first, then ledger on top of it) or explicit quarantine
   of the holding lane's change with attribution (`git stash push -u -m`),
   which splits that lane's package and needs its owner's sign-off. Pick one
   before editing, never after.
