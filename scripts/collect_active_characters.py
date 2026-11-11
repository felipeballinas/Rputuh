#!/usr/bin/env python3
"""Hourly discovery and 90-day CSV registry for Tibia active players."""
from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_BASE = "https://api.tibiadata.com/v5"
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "players-ativos"
USER_AGENT = "RPUTUH-Portal/1.0 (hourly online-character registry)"
PROFILE_BATCH_SIZE = 50
PROFILE_REFRESH_DAYS = 7
RETENTION_DAYS = 90
RETRIES = 4

FIELDS = [
    "name", "normalized_name", "world", "level", "vocation", "guild_name", "guild_rank",
    "residence", "last_login", "achievement_points", "sex", "account_status", "house",
    "married_to", "first_seen_at", "last_seen_at", "profile_updated_at", "profile_status",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def normalize_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not slug:
        raise ValueError(f"Nome de mundo sem slug válido: {value!r}")
    return slug


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(RETRIES):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError(f"Resposta JSON inesperada em {url}")
            return payload
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == RETRIES - 1:
                raise RuntimeError(f"TibiaData respondeu HTTP {exc.code} em {url}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == RETRIES - 1:
                raise RuntimeError(f"Falha consultando {url}: {exc}") from exc
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Falha consultando {url}")


def csv_path(world: str) -> Path:
    return DATA_DIR / f"{slugify(world)}.csv"


def load_world_csv(world: str) -> tuple[Path, dict[str, dict[str, str]], bool]:
    path = csv_path(world)
    records: dict[str, dict[str, str]] = {}
    had_duplicates = False
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                name = row.get("name", "")
                key = normalize_name(name)
                if key:
                    row["normalized_name"] = key
                    incoming = {field: row.get(field, "") or "" for field in FIELDS}
                    existing = records.get(key)
                    if existing:
                        had_duplicates = True
                        # Preserve the most recently seen record and fill any
                        # blank fields from the duplicate row.
                        if incoming.get("last_seen_at", "") > existing.get("last_seen_at", ""):
                            records[key] = {field: incoming.get(field) or existing.get(field, "") for field in FIELDS}
                        else:
                            records[key] = {field: existing.get(field) or incoming.get(field, "") for field in FIELDS}
                    else:
                        records[key] = incoming
    return path, records, had_duplicates


def save_world_csv(path: Path, records: dict[str, dict[str, str]]) -> None:
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for key in sorted(records):
            writer.writerow({field: records[key].get(field, "") for field in FIELDS})
    temporary.replace(path)


def optional_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or value.get("value") or "").strip()
    return ""


def profile_values(payload: dict) -> dict[str, str] | None:
    outer = payload.get("character") or {}
    profile = outer.get("character") or {}
    if not profile.get("name"):
        return None
    guild = profile.get("guild") or {}
    return {
        "name": optional_text(profile.get("name")),
        "level": optional_text(profile.get("level")),
        "vocation": optional_text(profile.get("vocation")),
        "world": optional_text(profile.get("world")),
        "guild_name": optional_text(guild.get("name") or guild.get("guild_name")) if isinstance(guild, dict) else optional_text(guild),
        "guild_rank": optional_text(guild.get("rank")) if isinstance(guild, dict) else "",
        "residence": optional_text(profile.get("residence")),
        "last_login": optional_text(profile.get("last_login")),
        "achievement_points": optional_text(profile.get("achievement_points")),
        "sex": optional_text(profile.get("sex")),
        "account_status": optional_text(profile.get("account_status")),
        "house": optional_text(profile.get("house") or profile.get("house_name")),
        "married_to": optional_text(profile.get("married_to") or profile.get("marriage")),
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    timestamp = stamp(now)
    today = now.date().isoformat()

    world_directory = fetch_json(f"{API_BASE}/worlds").get("worlds", {})
    worlds = world_directory.get("regular_worlds")
    if not isinstance(worlds, list) or not worlds:
        raise RuntimeError("A lista de mundos regulares não veio na resposta da TibiaData.")

    # Fetch every world's online list before writing, so a failed scan never
    # leaves a partially refreshed collection of world files.
    scans: list[tuple[str, list[dict]]] = []
    for index, world_info in enumerate(worlds):
        world = world_info.get("name")
        if not isinstance(world, str) or not world.strip():
            raise RuntimeError("A TibiaData retornou um mundo sem nome.")
        payload = fetch_json(f"{API_BASE}/world/{quote(world, safe='')}")
        online = payload.get("world", {}).get("online_players")
        if not isinstance(online, list):
            raise RuntimeError(f"A lista de personagens online está ausente para {world}.")
        scans.append((world, online))
        if index + 1 < len(worlds):
            time.sleep(0.15)

    state: dict[str, tuple[Path, dict[str, dict[str, str]], bool]] = {}
    new_names = 0
    online_count = 0
    for world, online in scans:
        path, records, had_duplicates = load_world_csv(world)
        changed = had_duplicates
        online_count += len(online)
        for player in online:
            name = optional_text(player.get("name"))
            if not name:
                continue
            key = normalize_name(name)
            record = records.get(key)
            if record is None:
                records[key] = {field: "" for field in FIELDS}
                record = records[key]
                record.update({
                    "name": name, "normalized_name": key, "world": world,
                    "first_seen_at": timestamp, "last_seen_at": timestamp,
                    "profile_status": "pending",
                })
                new_names += 1
                changed = True
            else:
                if record.get("last_seen_at", "")[:10] != today:
                    record["last_seen_at"] = timestamp
                    changed = True
            level = optional_text(player.get("level"))
            vocation = optional_text(player.get("vocation"))
            if record.get("level") != level:
                record["level"] = level
                changed = True
            if record.get("vocation") != vocation:
                record["vocation"] = vocation
                changed = True
        state[world] = (path, records, changed)

    cutoff = now - timedelta(days=RETENTION_DAYS)
    candidates: list[tuple[str, str, dict[str, str]]] = []
    removed_count = 0
    for world, (_, records, _) in state.items():
        expired = []
        for key, record in records.items():
            try:
                last_seen = datetime.fromisoformat(record.get("last_seen_at", "").replace("Z", "+00:00"))
            except ValueError:
                try:
                    last_seen = datetime.fromisoformat(record.get("first_seen_at", "").replace("Z", "+00:00"))
                except ValueError:
                    last_seen = datetime.min.replace(tzinfo=timezone.utc)
            try:
                profile_time = datetime.fromisoformat(record.get("profile_updated_at", "").replace("Z", "+00:00"))
            except ValueError:
                profile_time = datetime.min.replace(tzinfo=timezone.utc)
            if last_seen < cutoff:
                expired.append(key)
                continue
            profile_due = record.get("profile_status") == "pending" or profile_time < now - timedelta(days=PROFILE_REFRESH_DAYS)
            if profile_due:
                candidates.append((world, key, record))
        for key in expired:
            del records[key]
        removed_count += len(expired)
        if expired:
            path, records, _ = state[world]
            state[world] = (path, records, True)
    candidates.sort(key=lambda item: (item[2].get("profile_status") != "pending", item[2].get("first_seen_at", ""), item[0], item[1]))

    enriched = 0
    profile_errors = 0
    for index, (world, key, record) in enumerate(candidates[:PROFILE_BATCH_SIZE]):
        url = f"{API_BASE}/character/{quote(record['name'], safe='')}"
        try:
            values = profile_values(fetch_json(url))
        except RuntimeError as exc:
            print(f"Perfil adiado para {record['name']} ({world}): {exc}")
            profile_errors += 1
            continue
        if values is None:
            record["profile_status"] = "not_found"
            record["profile_updated_at"] = timestamp
        else:
            for field, value in values.items():
                record[field] = value
            record["normalized_name"] = key
            record["world"] = world
            record["first_seen_at"] = record.get("first_seen_at", "")
            record["last_seen_at"] = record.get("last_seen_at", "")
            record["profile_updated_at"] = timestamp
            record["profile_status"] = "loaded"
        path, records, _ = state[world]
        state[world] = (path, records, True)
        enriched += 1
        if index + 1 < min(len(candidates), PROFILE_BATCH_SIZE):
            time.sleep(1.0)

    changed_files = 0
    for path, records, changed in state.values():
        if changed:
            save_world_csv(path, records)
            changed_files += 1

    print(
        f"Mundos consultados: {len(scans)}; personagens online: {online_count}; "
        f"novos nomes: {new_names}; removidos após {RETENTION_DAYS} dias: "
        f"{removed_count}; perfis consultados: {enriched}; "
        f"perfis adiados por erro: {profile_errors}; CSVs atualizados: {changed_files}."
    )


if __name__ == "__main__":
    main()
