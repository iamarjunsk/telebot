#!/usr/bin/env python3
import sys
import subprocess
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
VENV_PYTHON = BASE_DIR / "venv" / "Scripts" / "python.exe"
BOT_SCRIPT = BASE_DIR / "bot.py"

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"

os.chdir(BASE_DIR)
subprocess.run([str(VENV_PYTHON), "-X", "utf8=1", str(BOT_SCRIPT)], env=env, check=False)