#!/usr/bin/env python3
"""Collect ranked XP snapshots and recent death events for active players."""
from __future__ import annotations

import csv
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import quote

from collect_active_characters import API_BASE, fetch_json, normalize_name, slugify, stamp, utc_now

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_DIR = ROOT / "data" / "players-ativos"
DATA_DIR = ROOT / "data" / "player-history"
XP_DIR = DATA_DIR / "experience"
XP_CHECKPOINT_DIR = DATA_DIR / "highscore-checkpoints"
DEATH_CHECK_DIR = DATA_DIR / "death-checks"
XP_COVERAGE_PATH = DATA_DIR / "xp-coverage.csv"

RETENTION_DAYS = 90
DEATH_PROFILE_BATCH_SIZE = 100
DEATH_PROFILE_DELAY_SECONDS = 1.0
HIGHSCORE_PAGE_DELAY_SECONDS = 0.15
MAX_HIGHSCORE_PAGES = 500
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")

XP_FIELDS = [
    "date", "name", "normalized_name", "world", "rank", "experience_total",
    "experience_gain", "gain_status", "highscore_age_minutes", "collected_at",
]
DEATH_EVENT_FIELDS = [
    "name", "normalized_name", "world", "death_time", "death_date", "level",
    "reason", "recorded_at", "source_truncated",
]
DEATH_DAILY_FIELDS = [
    "date", "name", "normalized_name", "world", "deaths_count", "status", "verified_at",
]
DEATH_CHECK_FIELDS = [
    "name", "normalized_name", "world", "last_seen_at", "last_checked_at",
    "status", "deaths_truncated",
]
XP_COVERAGE_FIELDS = [
    "name", "normalized_name", "world", "last_seen_at", "checked_date", "xp_status",
    "experience_total", "rank",
]


def local_date() -> str:
    return datetime.now(LOCAL_TZ).date().isoformat()


def key_for(name: str, world: str) -> str:
    return f"{world.casefold()}::{normalize_name(name)}"


def read_csv(path: Path, fields: list[str], keys: tuple[str, ...]) -> dict[tuple[str, ...], dict[str, str]]:
    rows: dict[tuple[str, ...], dict[str, str]] = {}
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            identity = tuple(row.get(field, "") or "" for field in keys)
            if all(identity):
                rows[identity] = {field: row.get(field, "") or "" for field in fields}
    return rows


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def load_active_players(cutoff: datetime) -> dict[str, dict[str, str]]:
    if not ACTIVE_DIR.exists():
        raise RuntimeError(f"A pasta do banco Players Ativos não existe: {ACTIVE_DIR}")
    players: dict[str, dict[str, str]] = {}
    for path in ACTIVE_DIR.glob("*.csv"):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                name = (row.get("name") or "").strip()
                world = (row.get("world") or "").strip()
                if not name or not world:
                    continue
                seen_raw = (row.get("last_seen_at") or "").replace("Z", "+00:00")
                try:
                    last_seen = datetime.fromisoformat(seen_raw)
                except ValueError:
                    continue
                if last_seen < cutoff:
                    continue
                player_key = key_for(name, world)
                current = players.get(player_key)
                if current is None or row.get("last_seen_at", "") > current.get("last_seen_at", ""):
                    row.update({"name": name, "world": world, "normalized_name": normalize_name(name)})
                    players[player_key] = row
    if not players:
        raise RuntimeError("Não há personagens com last_seen_at dentro dos últimos 90 dias.")
    return players


def xp_file(world: str) -> Path:
    return XP_DIR / f"{slugify(world)}.csv"


def checkpoint_file(world: str) -> Path:
    return XP_CHECKPOINT_DIR / f"{slugify(world)}.csv"


def death_check_file(world: str) -> Path:
    return DEATH_CHECK_DIR / f"{slugify(world)}.csv"


def fetch_world_highscores(world: str, player_names: set[str]) -> tuple[dict[str, dict], int, int, int]:
    page_url = f"{API_BASE}/highscores/{quote(world, safe='')}/experience/all/1"
    first_response = fetch_json(page_url)
    first = first_response.get("highscores", {})
    page_info = first.get("highscore_page", {})
    age_raw = first.get("highscore_age")
    age = int(age_raw) if age_raw is not None else -1
    total_pages = int(page_info.get("total_pages") or 1)
    if total_pages < 1 or total_pages > MAX_HIGHSCORE_PAGES:
        raise RuntimeError(f"Número de páginas inesperado ({total_pages}) no ranking de {world}.")

    matches: dict[str, dict] = {}
    records_seen = 0

    def collect(payload: dict) -> None:
        nonlocal records_seen
        highscore_list = payload.get("highscores", {}).get("highscore_list")
        if not isinstance(highscore_list, list):
            raise RuntimeError(f"Lista do ranking de experiência ausente para {world}.")
        records_seen += len(highscore_list)
        for entry in highscore_list:
            name = str(entry.get("name") or "").strip()
            normalized = normalize_name(name)
            xp = entry.get("value")
            if name and normalized in player_names and xp is not None:
                matches[normalized] = entry

    collect(first_response)
    for page in range(2, total_pages + 1):
        payload = fetch_json(f"{API_BASE}/highscores/{quote(world, safe='')}/experience/all/{page}")
        collect(payload)
        if page < total_pages:
            time.sleep(HIGHSCORE_PAGE_DELAY_SECONDS)
    return matches, records_seen, total_pages, age


def load_death_events() -> dict[tuple[str, ...], dict[str, str]]:
    return read_csv(DATA_DIR / "death-events.csv", DEATH_EVENT_FIELDS,
                    ("normalized_name", "world", "death_time", "level", "reason"))


def date_from_death(death: dict) -> tuple[str, str]:
    raw = death.get("time") or death.get("date") or ""
    if isinstance(raw, dict):
        raw = raw.get("date") or raw.get("time") or ""
    value = str(raw).strip()
    return (value[:10], value) if len(value) >= 10 else ("", value)


def prune_by_date(rows: dict[tuple[str, ...], dict[str, str]], field: str, cutoff: str) -> None:
    for identity in list(rows):
        row_date = rows[identity].get(field, "")
        if not row_date or row_date < cutoff:
            del rows[identity]


def main() -> None:
    today = local_date()
    now = utc_now()
    timestamp = stamp(now)
    cutoff_dt = now - timedelta(days=RETENTION_DAYS)
    cutoff_date = (datetime.now(LOCAL_TZ).date() - timedelta(days=RETENTION_DAYS)).isoformat()
    players = load_active_players(cutoff_dt)
    by_world: dict[str, dict[str, dict[str, str]]] = {}
    for player in players.values():
        by_world.setdefault(player["world"], {})[player["normalized_name"]] = player

    # Pull one complete experience ranking per world per Tibia date, and keep
    # only ranking rows that match the active-player registry.
    xp_written = 0
    xp_skipped = 0
    xp_rows_by_world: dict[str, dict[tuple[str, ...], dict[str, str]]] = {}
    complete_worlds: set[str] = set()
    for world, world_players in by_world.items():
        xp_path = xp_file(world)
        xp_rows = read_csv(xp_path, XP_FIELDS, ("date", "normalized_name", "world"))
        checkpoint_path = checkpoint_file(world)
        checkpoints = read_csv(checkpoint_path, ["date", "world", "status", "records_seen", "pages", "updated_at"], ("date", "world"))
        checkpoint_key = (today, world)
        if checkpoints.get(checkpoint_key, {}).get("status") == "complete":
            xp_skipped += 1
            xp_rows_by_world[world] = xp_rows
            complete_worlds.add(world)
            continue
        try:
            found, records_seen, pages, age = fetch_world_highscores(world, set(world_players))
        except (RuntimeError, ValueError) as exc:
            print(f"Ranking de experiência adiado para {world}: {exc}")
            continue

        # Replace today's rows for this world so a retry cannot duplicate data.
        for identity in list(xp_rows):
            if identity[0] == today and identity[2] == world:
                del xp_rows[identity]
        yesterday = (datetime.now(LOCAL_TZ).date() - timedelta(days=1)).isoformat()
        previous_xp = {
            row["normalized_name"]: int(row["experience_total"])
            for row in xp_rows.values()
            if row.get("world") == world and row.get("date") == yesterday and row.get("experience_total", "").isdigit()
        }
        for normalized, entry in found.items():
            name = str(entry.get("name") or world_players[normalized]["name"])
            experience = int(entry["value"])
            prior = previous_xp.get(normalized)
            xp_rows[(today, normalized, world)] = {
                "date": today,
                "name": name,
                "normalized_name": normalized,
                "world": world,
                "rank": str(entry.get("rank") or ""),
                "experience_total": str(experience),
                "experience_gain": str(experience - prior) if prior is not None else "",
                "gain_status": "daily" if prior is not None else "sem_snapshot_do_dia_anterior",
                "highscore_age_minutes": str(age),
                "collected_at": timestamp,
            }
            xp_written += 1
        prune_by_date(xp_rows, "date", cutoff_date)
        write_csv(xp_path, XP_FIELDS, [xp_rows[key] for key in sorted(xp_rows)])
        xp_rows_by_world[world] = xp_rows
        complete_worlds.add(world)
        checkpoints[checkpoint_key] = {
            "date": today, "world": world, "status": "complete", "records_seen": str(records_seen),
            "pages": str(pages), "updated_at": timestamp,
        }
        write_csv(checkpoint_path, ["date", "world", "status", "records_seen", "pages", "updated_at"],
                  [checkpoints[key] for key in sorted(checkpoints)])

    # A compact current status lets us distinguish an unranked character from
    # one whose world's ranking has not yet been collected today.
    coverage = read_csv(XP_COVERAGE_PATH, XP_COVERAGE_FIELDS, ("normalized_name", "world"))
    for world, world_players in by_world.items():
        if world not in complete_worlds:
            continue
        xp_rows = xp_rows_by_world[world]
        for normalized, player in world_players.items():
            xp_row = xp_rows.get((today, normalized, world))
            coverage[(normalized, world)] = {
                "name": player["name"], "normalized_name": normalized, "world": world,
                "last_seen_at": player.get("last_seen_at", ""), "checked_date": today,
                "xp_status": "found_in_experience_highscores" if xp_row else "not_listed_in_experience_highscores",
                "experience_total": xp_row.get("experience_total", "") if xp_row else "",
                "rank": xp_row.get("rank", "") if xp_row else "",
            }
    for identity in list(coverage):
        if coverage[identity].get("last_seen_at", "")[:10] < cutoff_date:
            del coverage[identity]
    write_csv(XP_COVERAGE_PATH, XP_COVERAGE_FIELDS,
              [coverage[key] for key in sorted(coverage)])

    # Query profiles in small hourly batches. Their recent death lists are
    # deduplicated as events; deaths_truncated is preserved for honest counts.
    death_events = load_death_events()
    prune_by_date(death_events, "death_date", cutoff_date)
    checks_by_world = {
        world: read_csv(death_check_file(world), DEATH_CHECK_FIELDS,
                        ("normalized_name", "world"))
        for world in by_world
    }
    due: list[tuple[str, str, dict[str, str], dict[str, dict[str, str]]]] = []
    for world, world_players in by_world.items():
        checks = checks_by_world[world]
        for normalized, player in world_players.items():
            check = checks.get((normalized, world), {})
            checked_raw = (check.get("last_checked_at") or "").replace("Z", "+00:00")
            try:
                checked = datetime.fromisoformat(checked_raw)
            except ValueError:
                checked = datetime.min.replace(tzinfo=timezone.utc)
            retry_hours = 6 if check.get("status", "").startswith("error") else 24
            if checked < now - timedelta(hours=retry_hours):
                due.append((world, normalized, player, checks))
    due.sort(key=lambda item: (item[3].get((item[1], item[0]), {}).get("last_checked_at", ""), item[0], item[1]))

    checked_count = 0
    profile_errors = 0
    for index, (world, normalized, player, checks) in enumerate(due[:DEATH_PROFILE_BATCH_SIZE]):
        url = f"{API_BASE}/character/{quote(player['name'], safe='')}"
        try:
            payload = fetch_json(url)
        except RuntimeError as exc:
            checks[(normalized, world)] = {
                "name": player["name"], "normalized_name": normalized, "world": world,
                "last_seen_at": player.get("last_seen_at", ""), "last_checked_at": timestamp,
                "status": f"error: {exc}", "deaths_truncated": "",
            }
            profile_errors += 1
            if index + 1 < min(len(due), DEATH_PROFILE_BATCH_SIZE):
                time.sleep(DEATH_PROFILE_DELAY_SECONDS)
            continue

        outer = payload.get("character") or {}
        character = outer.get("character") or {}
        if not character.get("name"):
            status = "not_found"
            truncated = ""
            deaths = []
        else:
            status = "loaded"
            truncated = str(bool(outer.get("deaths_truncated"))).lower()
            deaths = outer.get("deaths") or []
            if not isinstance(deaths, list):
                deaths = []
        for death in deaths:
            death_date, death_time = date_from_death(death)
            if not death_date or death_date < cutoff_date:
                continue
            level = str(death.get("level") or "")
            reason = str(death.get("reason") or "")
            identity = (normalized, world, death_time, level, reason)
            existing_event = death_events.get(identity)
            if existing_event is None:
                death_events[identity] = {
                    "name": player["name"], "normalized_name": normalized, "world": world,
                    "death_time": death_time, "death_date": death_date, "level": level,
                    "reason": reason, "recorded_at": timestamp, "source_truncated": truncated,
                }
            elif existing_event.get("source_truncated") != truncated:
                existing_event["source_truncated"] = truncated
            # Keep the first observation timestamp so repeated profile refreshes
            # do not rewrite old event rows on every hourly run.
        checks[(normalized, world)] = {
            "name": player["name"], "normalized_name": normalized, "world": world,
            "last_seen_at": player.get("last_seen_at", ""), "last_checked_at": timestamp,
            "status": status, "deaths_truncated": truncated,
        }
        checked_count += 1
        if index + 1 < min(len(due), DEATH_PROFILE_BATCH_SIZE):
            time.sleep(DEATH_PROFILE_DELAY_SECONDS)

    for world in by_world:
        checks = checks_by_world[world]
        # Merge updates made above and discard characters outside the 90-day cohort.
        for identity in list(checks):
            if f"{identity[1].casefold()}::{identity[0]}" not in players:
                del checks[identity]
        write_csv(death_check_file(world), DEATH_CHECK_FIELDS, [checks[key] for key in sorted(checks)])
    write_csv(DATA_DIR / "death-events.csv", DEATH_EVENT_FIELDS,
              [death_events[key] for key in sorted(death_events)])

    # Publish daily counts derived from distinct death events. A zero is
    # recorded only when that character's profile was successfully checked
    # today and the API did not report a truncated death list.
    daily_deaths_path = DATA_DIR / "deaths-daily.csv"
    daily_deaths = read_csv(daily_deaths_path, DEATH_DAILY_FIELDS,
                            ("date", "normalized_name", "world"))
    prune_by_date(daily_deaths, "date", cutoff_date)
    counts: dict[tuple[str, str, str], int] = {}
    for event in death_events.values():
        count_key = (event["death_date"], event["normalized_name"], event["world"])
        counts[count_key] = counts.get(count_key, 0) + 1
    for (death_date, normalized, world), count in counts.items():
        player = by_world.get(world, {}).get(normalized)
        if not player:
            continue
        check = checks_by_world[world].get((normalized, world), {})
        status = "parcial_lista_truncada" if check.get("deaths_truncated") == "true" else "morte_registrada"
        daily_deaths[(death_date, normalized, world)] = {
            "date": death_date, "name": player["name"], "normalized_name": normalized,
            "world": world, "deaths_count": str(count), "status": status,
            "verified_at": check.get("last_checked_at", ""),
        }
    for world, checks in checks_by_world.items():
        for (normalized, checked_world), check in checks.items():
            if checked_world != world or check.get("status") != "loaded" or check.get("deaths_truncated") != "false":
                continue
            checked_at = (check.get("last_checked_at") or "").replace("Z", "+00:00")
            try:
                checked_date = datetime.fromisoformat(checked_at).astimezone(LOCAL_TZ).date().isoformat()
            except ValueError:
                continue
            if checked_date != today or normalized not in by_world.get(world, {}):
                continue
            identity = (today, normalized, world)
            player = by_world[world][normalized]
            existing = daily_deaths.get(identity)
            daily_deaths[identity] = {
                "date": today, "name": player["name"], "normalized_name": normalized,
                "world": world, "deaths_count": existing["deaths_count"] if existing else "0",
                "status": "confirmado_sem_truncamento", "verified_at": check.get("last_checked_at", ""),
            }
    for identity in list(daily_deaths):
        if daily_deaths[identity]["date"] < cutoff_date:
            del daily_deaths[identity]
    write_csv(daily_deaths_path, DEATH_DAILY_FIELDS,
              [daily_deaths[key] for key in sorted(daily_deaths)])

    print(
        f"Personagens ativos na janela de 90 dias: {len(players)}; "
        f"linhas de XP incluídas hoje: {xp_written}; rankings já coletados hoje: {xp_skipped}; "
        f"perfis consultados para mortes: {checked_count}; perfis pendentes: {max(0, len(due) - checked_count - profile_errors)}; "
        f"erros: {profile_errors}."
    )


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    main()
