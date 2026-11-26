"""Run the calibration tool from the adapter-branch worktree with the main
checkout's .env loaded into the process (values never printed).
Usage: calib_run.py <worktree> <env file> -- <tool args...>"""
import os
import runpy
import sys
from pathlib import Path

WT, ENV = Path(sys.argv[1]), Path(sys.argv[2])
assert sys.argv[3] == "--"
from dotenv import dotenv_values  # noqa: E402
for k, v in dotenv_values(ENV).items():
    if v:
        os.environ.setdefault(k, v)
os.chdir(WT)
sys.path.insert(0, str(WT))
tool = WT / "scripts" / "calibration" / "calibrate_refreshed_arms.py"
sys.argv = [str(tool)] + sys.argv[4:]
runpy.run_path(str(tool), run_name="__main__")
