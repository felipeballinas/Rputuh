#!/usr/bin/env python3
"""Reconcile per-world CSV snapshots produced by parallel character shards."""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "player-history"
TARGET = ROOT / "data" / "player-history" / "worlds"

SCHEMAS = {
    "experience.csv": (("date", "normalized_name", "world"), "collected_at"),
    "highscore-checkpoints.csv": (("date", "world"), "updated_at"),
    "xp-coverage.csv": (("normalized_name", "world"), "checked_date"),
    "deaths-daily.csv": (("date", "normalized_name", "world"), "verified_at"),
    "death-events.csv": (("normalized_name", "world", "death_time", "level", "reason"), "last_verified_at"),
    "death-checks.csv": (("normalized_name", "world"), "last_checked_at"),
}

world_files: dict[str, dict[str, dict[tuple[str, ...], dict[str, str]]]] = {}
headers: dict[tuple[str, str], list[str]] = {}
artifact_count = 0

if ARTIFACTS.exists():
    for artifact in sorted(ARTIFACTS.glob("player-history-world-*-shard-*")):
        artifact_name = artifact.name.removeprefix("player-history-world-")
        slug, separator, _shard = artifact_name.rpartition("-shard-")
        if not separator or not slug or slug == "none":
            continue
        artifact_count += 1
        for filename, (key_fields, timestamp_field) in SCHEMAS.items():
            source = artifact / filename
            if not source.exists():
                continue
            identity = (slug, filename)
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if identity not in headers:
                    headers[identity] = list(reader.fieldnames or [])
                rows = world_files.setdefault(slug, {}).setdefault(filename, {})
                for row in reader:
                    key = tuple(row.get(field, "") or "" for field in key_fields)
                    if not all(key):
                        continue
                    current = rows.get(key)
                    incoming_stamp = row.get(timestamp_field, "") or ""
                    current_stamp = (current or {}).get(timestamp_field, "") or ""
                    if current is None or incoming_stamp >= current_stamp:
                        rows[key] = {field: row.get(field, "") or "" for field in headers[identity]}

written = 0
for slug, files in world_files.items():
    destination = TARGET / slug
    destination.mkdir(parents=True, exist_ok=True)
    for filename, rows in files.items():
        fields = headers[(slug, filename)]
        with (destination / filename).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows[key] for key in sorted(rows))
        written += 1

print(f"{artifact_count} shards reconciliados; {written} CSVs reunidos por mundo.")
