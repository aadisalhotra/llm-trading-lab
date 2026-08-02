# LLM Trading Lab — project conventions

## Working-tree discipline (ratified 2026-08-02)

**One session per checkout.** Two agent/editor sessions must never work the
same working tree concurrently — that is how the 2026-08-02 cost-rates edits
appeared unattributed inside the SDK-migration diff. Parallel workstreams go
on branches (or a second clone); each lands through its own staged-and-halt
lane with its own report. If a session finds working-tree changes it did not
make, it stops and diffs them before staging anything (quarantine via
`git stash push -u -m "<attribution>"` when they belong to another lane).
