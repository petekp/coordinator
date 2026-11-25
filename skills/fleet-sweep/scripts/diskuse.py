"""Disk use rows for sweep.py report: stale scratch folders and large iOS simulators."""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from prs import run

SCRATCH_STALE_DAYS = 7
SCRATCH_LISTED_MB = 100
SIMULATOR_LISTED_MB = 500


def size_mb(path):
    out = run("du", "-sk", path)
    return int(out.split()[0]) // 1024 if out else 0


def scratch_rows(repo):
    """Leaves out parked/, which holds what removed worktrees left behind."""
    scratch = os.path.join(repo, ".scratch")
    if not os.path.isdir(scratch):
        return
    now = time.time()
    small_count = small_mb = 0
    for name in sorted(os.listdir(scratch)):
        path = os.path.join(scratch, name)
        if name == "parked":
            continue
        age_days = int((now - os.lstat(path).st_mtime) / 86400)
        if age_days < SCRATCH_STALE_DAYS:
            continue
        mb = size_mb(path)
        if mb >= SCRATCH_LISTED_MB:
            yield "ASK", f"{mb:>6}M scratch {path} · untouched {age_days} days"
        else:
            small_count += 1
            small_mb += mb
    if small_count:
        yield "ASK", f"{small_mb:>6}M scratch {scratch}: {small_count} smaller entries untouched {SCRATCH_STALE_DAYS}+ days"


def simulator_rows():
    out = run("xcrun", "simctl", "list", "devices", "-j")
    if not out:
        return
    base = os.path.expanduser("~/Library/Developer/CoreSimulator/Devices")
    devices = [d for group in json.loads(out).get("devices", {}).values() for d in group]
    with ThreadPoolExecutor(max_workers=8) as pool:
        sizes = pool.map(lambda d: size_mb(os.path.join(base, d["udid"])), devices)
    for d, mb in zip(devices, sizes):
        if mb >= SIMULATOR_LISTED_MB:
            yield "ASK", f"{mb:>6}M simulator {d['name']} {d['udid']} {d['state']}"
