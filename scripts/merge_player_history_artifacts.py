#!/usr/bin/env python3
"""Merge the per-world Actions artifacts into the checked-out CSV database."""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "player-history"
TARGET = ROOT / "data" / "player-history" / "worlds"

merged = 0
if ARTIFACTS.exists():
    for artifact in ARTIFACTS.glob("player-history-world-*"):
        slug = artifact.name.removeprefix("player-history-world-")
        if not slug or slug == "none":
            continue
        target = TARGET / slug
        target.mkdir(parents=True, exist_ok=True)
        for source in artifact.iterdir():
            if source.is_file():
                shutil.copy2(source, target / source.name)
                merged += 1
print(f"CSVs de {merged} arquivo(s) mesclados de {ARTIFACTS}.")
