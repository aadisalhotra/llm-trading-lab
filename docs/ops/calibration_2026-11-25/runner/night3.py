"""Night 3 of the calibration, unattended (hub H-OPS-36).

1. Wait for Wednesday's end-of-day commit ("end-of-day tick 2026-11-25...")
   on origin/main. Give up at WAIT_UNTIL (no run; status BLOCKED_NO_EOD).
2. Export data/prompts at that commit and check that every file of the
   be838ae export (Monday's and Tuesday's ticks and images) is byte-identical
   in it. On any difference: stop before any call (FAILED_IDENTITY).
3. Run the third increment under the watcher (calib_night.py, beside this
   file): per-arm processes, billing/quota stop per arm, hard stop.
4. Re-run passes for provider failures (the tool re-runs those only), never
   on an arm the watcher stopped for billing/quota, until none remain, the
   pass limit, or the hard stop.
5. If every arm is complete (429 calls, none failed): the final computation
   (tool `summarize` over all arms) -> summary.json. Otherwise no final
   computation; status says what is missing (counts only).

Status: status_night3.json (overwritten at each step) and night3.log
(appended), both in the calibration output folder. Nothing prints a key.

usage: night3.py <worktree> <env_file>
"""
import hashlib, io, json, os, re, subprocess, sys, tarfile, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WT = Path(sys.argv[1])
ENV = sys.argv[2]
OUT_REL = "docs/ops/calibration_2026-11-25"
OUT = WT / OUT_REL
INPUTS_ROOT = Path("C:/Users/Aadi/projects/llm-trading-lab-calib-inputs")
PREV_EXPORT = INPUTS_ROOT / "be838ae" / "data" / "prompts"
SESSIONS = ["2026-11-23", "2026-11-24", "2026-11-25"]
EOD_RE = r"^end-of-day tick 2026-11-25"
NOT_BEFORE = datetime(2026, 11, 25, 21, 0, tzinfo=timezone.utc)    # 16:00 ET: no EOD can exist earlier
WAIT_UNTIL = datetime(2026, 11, 26, 11, 0, tzinfo=timezone.utc)    # 06:00 ET Thu: too late to run
HARD_STOP = datetime(2026, 11, 26, 14, 20, tzinfo=timezone.utc)    # 09:20 ET Thu
ARMS = ["cb_sonnet", "cb_opus", "cb_astra", "cb_grok", "cb_argon"]
CALLS_PER_ARM = 429           # 195 screen + 195 trade + 39 send
MAX_RERUN_PASSES = 4
WATCH_LOG = OUT / "watch_2026-11-25.log"

state = {"night": 3, "stage": "starting", "updated_utc": None, "eod_commit": None, "export": None,
         "identity_check": None, "increment": None, "reruns": [], "stopped_arms": {},
         "arms": {}, "final_computation": None, "problems": []}


def now():
    return datetime.now(timezone.utc)


def log(msg):
    line = f"{now().isoformat(timespec='seconds')} {msg}"
    with open(OUT / "night3.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def status(stage=None, **kw):
    if stage:
        state["stage"] = stage
    state.update(kw)
    state["updated_utc"] = now().isoformat(timespec="seconds")
    tmp = OUT / "status_night3.json.tmp"
    tmp.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, OUT / "status_night3.json")


def git(*a, check=True):
    p = subprocess.run(["git", "-C", str(WT), *a], capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {a[0]} failed rc={p.returncode}: {p.stderr.decode(errors='replace')[:300]}")
    return p.stdout


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def arm_counts():
    out = {}
    for arm in ARMS:
        p = OUT / f"{arm}_calls.jsonl"
        latest = {}
        n = 0
        for ln in p.read_text(encoding="utf-8").splitlines() if p.exists() else []:
            if ln.strip():
                r = json.loads(ln)
                n += 1
                latest[(r["input_id"], r["leg"], r["setting"])] = r
        cls = {}
        for r in latest.values():
            cls[r["cls"]] = cls.get(r["cls"], 0) + 1
        wed = sum(1 for r in latest.values() if r["input_id"].startswith("20261125T"))
        out[arm] = {"records": n, "calls": len(latest), "of": CALLS_PER_ARM, "wednesday_calls": wed,
                    "by_class": cls,
                    "last_call_utc": max((r["started_utc"] for r in latest.values()), default=None)}
    return out


def watcher(arms, tag):
    env = dict(os.environ, CALIB_ARMS=" ".join(arms), CALIB_LOG_TAG=tag)
    cmd = [sys.executable, "-u", str(HERE / "calib_night.py"), str(WT), ENV,
           HARD_STOP.isoformat(), str(WATCH_LOG), "--", "run", "--sessions", *SESSIONS,
           "--inputs-dir", str(state["export"]["dir"]), "--out-dir", OUT_REL, "--label", "calibration"]
    t0 = now()
    rc = subprocess.run(cmd, cwd=str(WT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    # Arms the watcher stopped for billing/quota during this pass.
    for ln in WATCH_LOG.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\S+) STOP (cb_\w+): (.*)$", ln)
        if m and datetime.fromisoformat(m.group(1)) >= t0.replace(microsecond=0):
            state["stopped_arms"][m.group(2)] = m.group(3)
    deadline_hit = any("DEADLINE stop" in ln and datetime.fromisoformat(ln.split()[0]) >= t0.replace(microsecond=0)
                       for ln in WATCH_LOG.read_text(encoding="utf-8").splitlines())
    return rc, deadline_hit


def main():
    status("waiting_for_eod")
    log(f"night3 started pid={os.getpid()} runner sha256 night3={sha(__file__)} "
        f"watcher={sha(HERE / 'calib_night.py')} wrapper={sha(HERE / 'calib_run.py')}")

    # 1. Wait for the end-of-day commit.
    eod = None
    while eod is None:
        if now() >= WAIT_UNTIL:
            status("BLOCKED_NO_EOD", problems=state["problems"] + [
                f"no end-of-day commit for 2026-11-25 on origin/main by {WAIT_UNTIL.isoformat()}; nothing run"])
            log("BLOCKED_NO_EOD")
            return
        if now() >= NOT_BEFORE:
            try:
                git("fetch", "-q", "origin", "main")
                h = git("log", "origin/main", "--format=%H %cI %s", "-E", f"--grep={EOD_RE}", "-n", "1",
                        "--since=2026-11-25T12:00:00Z").decode().strip()
                if h:
                    eod = h.split(" ", 2)
            except Exception as e:  # network blip: keep waiting
                state["last_fetch_error"] = str(e)[:200]
            status(last_check_utc=now().isoformat(timespec="seconds"))
        if eod is None:
            time.sleep(120)
    state["eod_commit"] = {"commit": eod[0], "committed": eod[1], "subject": eod[2]}
    log(f"EOD commit {eod[0]} {eod[1]}")

    # 2. Export and byte-identity check.
    status("exporting")
    short = eod[0][:9]
    dest = INPUTS_ROOT / short
    tar = git("archive", "--format=tar", eod[0], "data/prompts")
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(dest)
    new = dest / "data" / "prompts"
    prev_files = [p for p in PREV_EXPORT.rglob("*") if p.is_file()]
    diffs = [str(p.relative_to(PREV_EXPORT)) for p in prev_files
             if not (new / p.relative_to(PREV_EXPORT)).is_file()
             or (new / p.relative_to(PREV_EXPORT)).read_bytes() != p.read_bytes()]
    wed_ticks = sorted(p.name for p in (new / "2026-11").glob("20261125T*.jsonl"))
    state["export"] = {"dir": str(new), "commit": eod[0], "wednesday_tick_files": len(wed_ticks),
                       "files_total": sum(1 for p in new.rglob("*") if p.is_file())}
    state["identity_check"] = {"against": "be838ae export (= 2b5e7791c for Monday)",
                               "files_checked": len(prev_files), "differences": diffs[:20],
                               "ok": not diffs}
    log(f"export {new} wed_ticks={len(wed_ticks)} identity_checked={len(prev_files)} diffs={len(diffs)}")
    if diffs:
        status("FAILED_IDENTITY", problems=state["problems"] + [
            f"{len(diffs)} Monday/Tuesday input files differ in the {short} export; nothing run"])
        return
    if len(wed_ticks) != 13:
        state["problems"].append(f"Wednesday has {len(wed_ticks)} tick files, not 13: the increment runs on those "
                                 "present, but the final computation (defined over 195) is left to the hub")

    # Runner record (H-OPS-34): what governs which calls run tonight.
    with open(OUT / "run_log.jsonl", "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"kind": "runner", "recorded_utc": now().isoformat(timespec="seconds"),
                            "note": "night 3 (2026-11-25), unattended, hub H-OPS-36",
                            "orchestrator": {"path": "runner/night3.py", "sha256": sha(__file__)},
                            "watcher": {"path": "runner/calib_night.py", "sha256": sha(HERE / "calib_night.py")},
                            "wrapper": {"path": "runner/calib_run.py", "sha256": sha(HERE / "calib_run.py")},
                            "inputs_commit": eod[0], "sessions": SESSIONS,
                            "hard_stop_utc": HARD_STOP.isoformat()}) + "\n")

    # 3. The third increment.
    status("increment_running", increment={"started_utc": now().isoformat(timespec="seconds")})
    rc, dl = watcher(ARMS, "2026-11-25")
    state["increment"].update(ended_utc=now().isoformat(timespec="seconds"), rc=rc, hard_stop_hit=dl)
    status(arms=arm_counts())
    log(f"increment done rc={rc} deadline={dl} stopped={state['stopped_arms']}")

    # 4. Re-run passes (connection and other provider failures; never a billing-stopped arm).
    p = 0
    while not dl and p < MAX_RERUN_PASSES and now() < HARD_STOP:
        counts = arm_counts()
        todo = [a for a in ARMS if a not in state["stopped_arms"]
                and counts[a]["by_class"].get("provider_failure", 0) > 0]
        if not todo:
            break
        p += 1
        status(f"rerun_pass_{p}")
        t = now().isoformat(timespec="seconds")
        rc, dl = watcher(todo, f"2026-11-25_rerun{p}")
        state["reruns"].append({"pass": p, "arms": todo, "started_utc": t,
                                "ended_utc": now().isoformat(timespec="seconds"), "rc": rc, "hard_stop_hit": dl})
        status(arms=arm_counts())
        log(f"rerun pass {p} arms={todo} rc={rc} deadline={dl}")

    # 5. Final computation only over complete arms' full set.
    counts = arm_counts()
    state["arms"] = counts
    # Complete = no call left as a provider failure, and the full set (429 per arm, 13 Wednesday ticks).
    incomplete = sorted(a for a, c in counts.items()
                        if c["by_class"].get("provider_failure", 0) or c["calls"] != CALLS_PER_ARM)
    if incomplete or state["stopped_arms"] or dl or state["export"]["wednesday_tick_files"] != 13:
        status("INCOMPLETE_NO_FINAL", problems=state["problems"] + [
            f"incomplete arms {incomplete}; stopped for billing/quota {state['stopped_arms']}; hard stop hit {dl}; "
            f"Wednesday tick files {state['export']['wednesday_tick_files']}; "
            "no final computation run (partial = counts only)"])
        log("INCOMPLETE_NO_FINAL")
        return
    status("final_computation")
    r = subprocess.run([sys.executable, "-u", str(HERE / "calib_run.py"), str(WT), ENV, "--", "summarize",
                        "--out-dir", OUT_REL, "--label", "calibration"], cwd=str(WT), capture_output=True)
    (OUT / "summarize_2026-11-25.log").write_bytes(r.stdout + r.stderr)
    sp = OUT / "summary.json"
    state["final_computation"] = {"rc": r.returncode, "ended_utc": now().isoformat(timespec="seconds"),
                                  "summary": "summary.json", "summary_sha256": sha(sp) if sp.exists() else None,
                                  "tables": {a: {"file": f"{a}_calls.jsonl", "sha256": sha(OUT / f"{a}_calls.jsonl")}
                                             for a in ARMS}}
    status("DONE" if r.returncode == 0 else "FAILED_FINAL")
    log(f"final computation rc={r.returncode}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 -- the status file must say what happened
        status("CRASHED", problems=state["problems"] + [f"{type(e).__name__}: {str(e)[:300]}"])
        log(f"CRASHED {type(e).__name__}: {e}")
        raise
