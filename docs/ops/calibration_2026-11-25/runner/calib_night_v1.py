"""Night increment: one process per arm, so a billing/quota failure stops
that arm alone (hub H-OPS-33). Hard stop at a UTC deadline for every arm.

usage: calib_night.py <worktree> <env> <deadline_utc_iso> <watch_log> -- <tool run args>
"""
import json, re, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
wt, env, deadline, wlog = sys.argv[1:5]
tool_args = sys.argv[sys.argv.index("--") + 1:]
dl = datetime.fromisoformat(deadline).timestamp()
out = Path(wt) / tool_args[tool_args.index("--out-dir") + 1]
ARMS = ["cb_sonnet", "cb_opus", "cb_astra", "cb_grok", "cb_argon"]
# Billing or quota: ACCOUNT_STATE, or a 402/429 / quota / credit text in the (redacted) error.
BILLING = re.compile(r"\b(402|429)\b|quota|billing|insufficient|credit|exhausted|rate.?limit|resource_exhausted",
                     re.I)


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    with open(wlog, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


procs, seen = {}, {}
for arm in ARMS:
    p = out / f"{arm}_calls.jsonl"
    seen[arm] = sum(1 for _ in open(p, encoding="utf-8")) if p.exists() else 0
    lf = open(out / f"run_2026-11-24_{arm}.log", "a", encoding="utf-8")
    procs[arm] = subprocess.Popen([sys.executable, "-u", str(HERE / "calib_run.py"), wt, env, "--",
                                   *tool_args, "--arms", arm], cwd=wt, stdout=lf, stderr=subprocess.STDOUT)
    log(f"started {arm} pid={procs[arm].pid} from_line={seen[arm]}")
    time.sleep(2)   # stagger the run_meta / run_log writes

stopped = {}
while any(p.poll() is None for p in procs.values()):
    for arm, pr in procs.items():
        p = out / f"{arm}_calls.jsonl"
        if not p.exists():
            continue
        lines = open(p, encoding="utf-8").read().splitlines()
        for ln in lines[seen[arm]:]:
            if not ln.strip():
                continue
            r = json.loads(ln)
            if r.get("cls") == "provider_failure" and (
                    r.get("error_class") == "ACCOUNT_STATE" or BILLING.search(r.get("error") or "")):
                if pr.poll() is None:
                    pr.terminate()
                stopped[arm] = r.get("error_class")
                log(f"STOP {arm}: billing/quota failure {r.get('error_class')} at {r.get('started_utc')}"
                    f" input={r.get('input_id')} {r.get('leg')}/{r.get('setting')}")
        seen[arm] = len(lines)
    if time.time() >= dl:
        for arm, pr in procs.items():
            if pr.poll() is None:
                pr.terminate()
                log(f"DEADLINE stop {arm}")
        break
    time.sleep(10)

for arm, pr in procs.items():
    try:
        pr.wait(timeout=60)
    except subprocess.TimeoutExpired:
        pr.kill()
    log(f"exit {arm} rc={pr.returncode}" + (f" STOPPED({stopped[arm]})" if arm in stopped else ""))
