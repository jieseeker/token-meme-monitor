#!/usr/bin/env python3
"""Log database size, disk headroom, and observed growth without touching SQLite."""
import argparse
import json
import os
import time
from pathlib import Path


def sample(database, previous=None, now=None):
    now = time.time() if now is None else now
    database = Path(database)
    sizes = {"database": database.stat().st_size}
    sizes.update({suffix: (database.parent / (database.name + suffix)).stat().st_size
                  if (database.parent / (database.name + suffix)).exists() else 0
                  for suffix in ("-wal", "-shm")})
    disk = os.statvfs(database.parent)
    free_bytes = disk.f_bavail * disk.f_frsize
    total_bytes = sum(sizes.values())
    growth_bytes_per_day = None
    if previous and isinstance(previous.get("at"), (int, float)) and isinstance(previous.get("total_bytes"), int) and now > previous["at"] + 60:
        growth_bytes_per_day = round((total_bytes - previous["total_bytes"]) * 86400 / (now - previous["at"]))
    projected_days = (free_bytes / growth_bytes_per_day
                      if growth_bytes_per_day and growth_bytes_per_day > 0 else None)
    return {
        "at": now, "database_bytes": sizes["database"], "wal_bytes": sizes["-wal"],
        "shm_bytes": sizes["-shm"], "total_bytes": total_bytes, "free_bytes": free_bytes,
        "growth_bytes_per_day": growth_bytes_per_day, "projected_days_to_full": projected_days,
        "alert": free_bytes < 5 * 1024**3 or sizes["-wal"] > 2 * 1024**3
                 or (projected_days is not None and projected_days < 3),
    }


def run(database, state):
    try:
        previous = json.loads(state.read_text())
    except (FileNotFoundError, ValueError):
        previous = None
    result = sample(database, previous)
    state.parent.mkdir(parents=True, exist_ok=True)
    temporary = state.with_suffix(state.suffix + ".tmp")
    temporary.write_text(json.dumps(result))
    temporary.replace(state)
    print(json.dumps(result, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    while True:
        run(args.db, args.state)
        if not args.loop:
            break
        time.sleep(3600)


if __name__ == "__main__":
    main()
