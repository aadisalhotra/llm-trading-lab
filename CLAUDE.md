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
   - **A path-scoped stash is not a path-scoped index restore** (learned
     2026-08-19). `git stash push -- <path>` scopes the *worktree* save to that
     path, but the stash commit records the **whole index**. `git stash pop
     --index` therefore tries to restore every other lane's staged files and
     exits `already exists in index` / `patch does not apply`, naming paths you
     never stashed. It fails *safely* — the stash entry is kept and nothing is
     lost — but the restore must be done with a path-scoped guard patch
     (`git diff --cached -- <path>` saved before, `git apply` after), not with
     `pop --index`. Same family as the `--autostash` trap above: both promise an
     index restore and deliver something wider.

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

   **Blob identity holds only while your lane does not commit to that path.**
   (Learned 2026-08-19.) When a lane quarantines another lane's hunk, commits
   its *own* change to the same file, and then restores, the restored blob is
   necessarily different — it is the legitimate composition of both changes.
   `scripts/build_monthly_data_layer.py` moved `2ab259f1` → `c6708922` exactly
   this way, with the v4 lane's staged content untouched. On such a path the
   invariant is **delta-identity**: the restored `git diff --cached -- <path>`
   must have added/removed lines byte-identical to the guard patch taken before
   the quarantine. A checkpoint that re-verifies that path against the *old*
   blob hash will read a false failure. Blob identity remains the right proof
   for any contended path this lane did **not** commit to.

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

   **CRLF branch: on a file that is LF in the object store and CRLF in the
   worktree, quarantine by byte splice, not by `git apply`** (ratified
   2026-09-14). `scripts/phase_a_integrity_ledger.json` is exactly this file,
   so this is the branch the recurring contended path actually takes. The
   obvious quarantine — save `git diff --cached -- <path>` as a guard patch,
   restore, edit, commit, `git apply` the guard patch back — **fails on the
   replay**: `git diff` emits the patch in the object store's LF form, and
   `git apply` then cannot match a single context line against a CRLF worktree
   file. It fails safely (nothing is written), but it fails, and it fails after
   the commit has already landed, which is the worst moment to be improvising.

   The byte splice has no such failure mode, and it is exactly reversible:

   ```
   A = worktree bytes                       # == the other lane's staged content
   git diff --cached -- <path> > guard.patch   # keep it; it is the PROOF, not the restore
   git diff --name-only -- <path>           # MUST be empty (rule 6 step 4)
   git restore --source=HEAD --staged --worktree -- <path>
   B = worktree bytes                       # HEAD's checkout form
   # prove the other lane's delta is one contiguous insertion, and capture it:
   #   pre = len(common prefix of A,B); suf = len(common suffix)
   #   ins = A[pre : len(A)-suf]; assert A == B[:pre] + ins + B[pre:]
   <make this lane's edit>                  # rule 8 if it is the ledger
   git add <path> ; git commit -F <msgfile> -- <path>
   C = worktree bytes ; assert C[:pre] == B[:pre]   # the splice point survived
   write C[:pre] + ins + C[pre:] ; git add <path>
   ```

   Capture the hunk as **raw bytes plus an offset**, not as lines: the splice
   point is mid-line in the general case, so a line-split of the raw hunk
   yields fragments that will not match anything and will fail a naive
   verifier for the wrong reason.

   **Prove the restore by diffing added LINES against the guard patch**, which
   is what the guard patch is for once it is no longer the restore mechanism:

   ```
   git diff --cached -- <path> | grep '^+' | grep -v '^+++'   # must equal
   grep '^+' guard.patch       | grep -v '^+++'               # byte for byte
   ```

   and assert zero deletions. This is the delta-identity check rule 6 requires
   on a path this lane committed to; the blob hash necessarily moves and is not
   the proof. Worked instance: ledger `99a409a4` → `85b1bd3c`, 17 lines
   byte-identical, 0 deletions, the other five v4-lane blobs untouched.

8. **The integrity ledger is never edited by parse-and-reserialize** (ratified
   2026-08-19). `scripts/phase_a_integrity_ledger.json` must be modified by
   **surgical text insertion**, never by `json.load` → mutate → `json.dumps`.

   A round-trip is not faithful to that file. Measured on the 2026-08-19 C
   window, `json.dumps(d, indent=2)` produced **255 spurious diff lines** before
   a single real change was added: it strips the blank lines that separate
   sections, renormalizes `0.80` to `0.8`, expands inline arrays like
   `["completeness", "uncorrupted_book", …]` onto one line each, and rewrites
   the file's CRLF endings as LF.

   That is not cosmetic. The ledger is the file rule 7 names as the recurring
   contended path, so a reserialize buries the real entries in unrelated
   churn, destroys the holding lane's ability to verify its own hunk, and makes
   "exactly the expected added key paths" unprovable. The C window's diff was
   **139 insertions, 0 deletions** because it was a text insert.

   **Three proofs run before the write, every time:**

   1. **Reparse** — the new text is valid JSON.
   2. **Deep-equal outside the insertion** — parse the new text, remove the
      appended elements, and assert it equals the original parse. This is what
      catches an accidental edit elsewhere in the structure.
   3. **Literal byte-prefix survival** — the original bytes up to the insertion
      point must still be a literal prefix of the new content.

   Locate the insertion point by asserting the file's exact tail (for the
   2026-08 window: `"\r\n    ]\r\n  }\r\n}\r\n"`) and refuse to guess if it does
   not match. Write bytes, not text, so the line endings survive.

   This sits beside rule 6's `--autostash` warning and the two findings landed
   with it: a path-scoped stash is not a path-scoped index restore, and blob
   identity holds only while your lane does not commit to the contended path.

9. **Verify an instrument against the system, never against its own success
   report** (ratified 2026-09-14). An instrument that reports success while
   measuring nothing is the most expensive failure this lab has, because it
   costs a whole cycle and leaves no error behind. Two instances, one week
   apart, in different tools:

   * **Task Scheduler.** `New-ScheduledTaskSettingsSet` takes
     `-AllowStartIfOnBatteries` and `-DontStopIfGoingOnBatteries` — **switches,
     in the affirmative**. The negated forms (`-DisallowStartIfOnBatteries`,
     `-StopIfGoingOnBatteries`) are not parameters at all; passing them makes
     the cmdlet return `$null`, `Register-ScheduledTask` then fails on a null
     `-Settings` — and a trailing `Write-Output "REGISTERED"` still prints, so
     the script reports success and **no task exists**. Set
     `StartWhenAvailable` as a property on the returned settings object. Then
     verify with `Get-ScheduledTask` / `Get-ScheduledTaskInfo` and read back
     every setting that matters: `StartBoundary`, `NextRunTime`, `LogonType`,
     `ExecutionTimeLimit`, `StartWhenAvailable`, both battery flags.

   * **The tripwire estimator.** A baseline stated as "mean ± sd" without
     naming its estimator is not a specification. The same window gave
     56.16 ± 3.29 (daily-mean) and 56.26 ± 7.67 (per-call); scoring a day mean
     against the per-call sd understates z by ≈2.3× and raises no error
     anywhere. Registered in the ledger as
     `behavioral_tripwire_must_name_its_estimator`.

   Same class in both: the instrument's own output said "fine". The rule is to
   ask the system, not the instrument — `Get-ScheduledTask`, not the script's
   echo; the named estimator, not the number that happened to be in scope.

   The scheduling corollary that came with the first instance:
   **`StartWhenAvailable` is a hazard, not a safety net, for a
   market-hours job.** A missed 09:35 start firing at an arbitrary later hour
   produces a market-closed abort that reads like a result. Better that it does
   not fire at all.
