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
