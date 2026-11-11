#!/usr/bin/env python3
"""Emit the active worlds with characters seen in the last 30 days as JSON."""
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collect_active_characters import normalize_name, slugify

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_DIR = ROOT / "data" / "players-ativos"
CUTOFF = datetime.now(timezone.utc) - timedelta(days=30)

worlds: dict[str, str] = {}
for path in ACTIVE_DIR.glob("*.csv"):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("name") or "").strip()
            world = (row.get("world") or "").strip()
            seen = (row.get("last_seen_at") or "").replace("Z", "+00:00")
            if not name or not world:
                continue
            try:
                if datetime.fromisoformat(seen) >= CUTOFF:
                    worlds[world.casefold()] = world
            except ValueError:
                continue

matrix = [{"name": world, "slug": slugify(world)} for world in sorted(worlds.values(), key=str.casefold)]
if not matrix:
    matrix = [{"name": "__none__", "slug": "none"}]
print(json.dumps(matrix, ensure_ascii=False, separators=(",", ":")))
