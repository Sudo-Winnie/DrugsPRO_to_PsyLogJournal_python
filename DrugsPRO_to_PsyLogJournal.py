#!/usr/bin/env python3
"""
DrugsPRO_to_PsyLogJournal.py

Converts a DrugsPRO journal export into the journal app's import format
(PsyLog / "Android Journal", https://codeberg.org/PsyLog/PsyLog-android).

将 DrugsPRO 的日记导出转换成 journal app(PsyLog)的导入格式。
Everything printed to the console is bilingual (Chinese + English); code
comments, schema diagnostics and the JSON output stay in English.
打印到控制台的内容都是中英双语;代码注释、结构校验信息和 JSON 输出保持英文。

The mapping follows the same scheme DrugsPRO uses when it imports one of those
files, just in the other direction:

  DrugsPRO experience + its substances  ->  journal experience + ingestions
  DrugsPRO standalone usage-log rows    ->  one experience per day, ingestion per row
  usage rows tagged __fromExperience    ->  skipped, they are projections of an experience

Target schema, from the journal app's own source:
  ui/tabs/settings/JournalExport.kt, InstantSerializer.kt, AdministrationRoute.kt

  { experiences: [ { title, text, creationDate, sortDate, isFavorite,
                     ingestions: [ { substanceName, time, endTime, creationDate,
                                     administrationRoute, dose, isDoseAnEstimate,
                                     estimatedDoseStandardDeviation, units, notes,
                                     stomachFullness, consumerName, customUnitId } ],
                     location, ratings, timedNotes } ],
    substanceCompanions: [], customSubstances: [], customUnits: [] }

Timestamps are epoch milliseconds (InstantSerializer encodes a Long).
administrationRoute is the enum name, upper case: ORAL, SUBLINGUAL, BUCCAL,
INSUFFLATED, RECTAL, TRANSDERMAL, SUBCUTANEOUS, INTRAMUSCULAR, INTRAVENOUS,
SMOKED, INHALED.

Usage / 用法:
  python DrugsPRO_to_PsyLogJournal.py <backup.json> [-o out.json] [--pretty] [--dedupe] [--stdout]
  python DrugsPRO_to_PsyLogJournal.py     # asks for the file first / 先询问文件路径
  python DrugsPRO_to_PsyLogJournal.py --self-test

Needs journal_file.py next to this script / 需要与本脚本放在同一目录的 journal_file.py
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from journal_file import local_instant, local_instant_ms, read_backup_file, to_day_key, to_ms

"""Enum names from the journal app's AdministrationRoute."""
ROUTES = (
    "ORAL",
    "SUBLINGUAL",
    "BUCCAL",
    "INSUFFLATED",
    "RECTAL",
    "TRANSDERMAL",
    "SUBCUTANEOUS",
    "INTRAMUSCULAR",
    "INTRAVENOUS",
    "SMOKED",
    "INHALED",
)

"""DrugsPRO route -> journal app route. The journal app has no vaporized route."""
ROUTE_ALIASES = (
    (re.compile(r"vape|vapor"), "SMOKED"),
    (re.compile(r"intraven|\biv\b"), "INTRAVENOUS"),
    (re.compile(r"intramuscular|\bim\b"), "INTRAMUSCULAR"),
    (re.compile(r"subcutaneous|\bsc\b"), "SUBCUTANEOUS"),
    (re.compile(r"transdermal"), "TRANSDERMAL"),
    (re.compile(r"sublingual|\bsl\b"), "SUBLINGUAL"),
    (re.compile(r"buccal"), "BUCCAL"),
    (re.compile(r"snort|nasal|insuff"), "INSUFFLATED"),
    (re.compile(r"inhal"), "INHALED"),
    (re.compile(r"smok"), "SMOKED"),
    (re.compile(r"rectal|boof|plug"), "RECTAL"),
    (re.compile(r"oral|mouth|\bpo\b"), "ORAL"),
)

_DOSE_STRING = re.compile(r"^([0-9]+(?:[.,][0-9]+)?)\s*([A-Za-z\u00b5%]+)?\s*$")
_LEADING_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_DAY_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_route(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return "ORAL"
    upper = text.upper()
    if upper in ROUTES:
        return upper
    lower = text.lower()
    for pattern, route in ROUTE_ALIASES:
        if pattern.search(lower):
            return route
    return "ORAL"


def parse_float(value: Any) -> float | None:
    """Number.parseFloat equivalent: reads a leading number, ignores the rest."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _LEADING_NUMBER.match(str(value).strip())
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_dose_string(dose: Any) -> tuple[float | None, str | None]:
    """'600 mg' -> (600.0, 'mg'); '40' -> (40.0, None)."""
    text = str(dose if dose is not None else "").strip()
    if not text:
        return None, None
    match = _DOSE_STRING.match(text)
    if not match:
        return None, None
    value = float(match.group(1).replace(",", "."))
    unit = match.group(2).lower() if match.group(2) else None
    return value, unit


def tidy_number(value: float | None) -> float | int | None:
    """Keeps whole doses as integers so the file reads like the app's own."""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def build_unit_lookup(usage_rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """
    Unit lookup for an experience: the app's usage-log rows are a projection of
    the experience, so a row with the same substance name carries the unit that
    the substance entry itself does not store.
    """
    by_experience: dict[str, dict[str, str]] = {}
    for row in usage_rows:
        if not isinstance(row, dict):
            continue
        experience_id = row.get("__expId")
        if not experience_id:
            continue
        name = str(row.get("name") or "").strip().lower()
        if not name:
            continue
        _, unit = parse_dose_string(row.get("dose"))
        bucket = by_experience.setdefault(experience_id, {})
        if unit and name not in bucket:
            bucket[name] = unit
    return by_experience


def substance_title(substances: list[dict[str, Any]]) -> str:
    seen: list[str] = []
    for substance in substances:
        name = str(substance.get("name") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return " + ".join(seen)


def convert_to_journal_app(
    payload: dict[str, Any], drop_duplicate_rows: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Converts a DrugsPRO backup payload into the journal app's import format."""
    usage_rows = payload.get("u") if isinstance(payload.get("u"), list) else []
    source_experiences = payload.get("e") if isinstance(payload.get("e"), list) else []
    unit_lookup = build_unit_lookup(usage_rows)

    experiences: list[dict[str, Any]] = []
    skipped_projected_rows = 0

    for source in source_experiences:
        if not isinstance(source, dict):
            continue
        substances = [
            s
            for s in (source.get("substances") or [])
            if isinstance(s, dict) and str(s.get("name") or "").strip()
        ]
        created_ms = to_ms(source.get("__updatedAt"))
        day_key = (
            source.get("date")
            or (to_day_key(created_ms) if created_ms is not None else None)
            or to_day_key(to_ms_now())
        )
        units_for_experience = unit_lookup.get(str(source.get("id") or ""), {})

        ingestions = []
        for substance in substances:
            name = str(substance["name"]).strip()
            day_offset = 1 if substance.get("day2") else 0
            time_ms = local_instant_ms(day_key, substance.get("timeHHMM"), day_offset)
            fallback_ms = local_instant_ms(day_key)
            unit = units_for_experience.get(name.lower())
            ingestions.append(
                {
                    "substanceName": name,
                    "time": time_ms if time_ms is not None else fallback_ms,
                    "endTime": local_instant_ms(day_key, substance.get("endTimeHHMM"), day_offset)
                    if substance.get("endTimeHHMM")
                    else None,
                    "creationDate": created_ms if created_ms is not None else time_ms,
                    "administrationRoute": normalize_route(
                        substance.get("roaName") or substance.get("roa")
                    ),
                    "dose": tidy_number(parse_float(substance.get("doseMult"))),
                    "isDoseAnEstimate": False,
                    "estimatedDoseStandardDeviation": None,
                    "units": unit or "mg",
                    "notes": None,
                    "stomachFullness": None,
                    "consumerName": None,
                    "customUnitId": None,
                }
            )

        sort_ms = ingestions[0]["time"] if ingestions else fallback_day_ms(day_key)
        experiences.append(
            {
                "title": (
                    str(source.get("title") or "").strip()
                    or substance_title(substances)
                    or "Experience"
                ),
                "text": str(source.get("description") or ""),
                "creationDate": created_ms if created_ms is not None else sort_ms,
                "sortDate": sort_ms,
                "isFavorite": bool(source.get("favorite")),
                "ingestions": ingestions,
                "location": None,
                "ratings": [],
                "timedNotes": [],
            }
        )

    # Usage rows the app generated from an experience are already represented by
    # that experience's substances; only the free-standing rows carry new data.
    standalone: list[dict[str, Any]] = []
    for row in usage_rows:
        if not isinstance(row, dict):
            continue
        if row.get("__expId") or row.get("__fromExperience"):
            skipped_projected_rows += 1
            continue
        if not str(row.get("name") or "").strip():
            continue
        standalone.append(row)

    rows_by_day: dict[str, list[dict[str, Any]]] = {}
    for row in standalone:
        date_iso = str(row.get("dateISO") or "")
        day_key = (
            date_iso
            if _DAY_KEY.match(date_iso)
            else (to_day_key(row.get("__updatedAt")) or to_day_key(to_ms_now()))
        )
        rows_by_day.setdefault(day_key, []).append(row)

    # Rows that repeat the same substance, dose and route on the same day are
    # counted, and kept by default: without a clock time they could equally be a
    # genuine re-dose. --dedupe collapses them when the caller knows better.
    seen_overall: set[tuple[str, str, str, str]] = set()
    duplicate_rows = 0
    for row in standalone:
        key = (
            str(row.get("dateISO") or "").strip(),
            str(row.get("name") or "").strip().lower(),
            str(row.get("dose") or "").strip(),
            str(row.get("roa") or "").strip().lower(),
        )
        if key in seen_overall:
            duplicate_rows += 1
        else:
            seen_overall.add(key)

    standalone_days = 0
    for day_key in sorted(rows_by_day):
        seen_in_day: set[tuple[str, str, str]] = set()
        rows = []
        for row in rows_by_day[day_key]:
            key = (
                str(row.get("name") or "").strip().lower(),
                str(row.get("dose") or "").strip(),
                str(row.get("roa") or "").strip().lower(),
            )
            if key in seen_in_day and drop_duplicate_rows:
                continue
            seen_in_day.add(key)
            rows.append(row)
        if not rows:
            continue

        # The free-standing rows carry no clock time, so they are laid out from
        # noon in one minute steps to keep their original order stable.
        base_ms = local_instant_ms(day_key)
        ingestions = []
        for index, row in enumerate(rows):
            value, unit = parse_dose_string(row.get("dose"))
            time_ms = base_ms + index * 60_000
            parsed = parse_float(row.get("dose"))
            ingestions.append(
                {
                    "substanceName": str(row["name"]).strip(),
                    "time": time_ms,
                    "endTime": None,
                    "creationDate": to_ms(row.get("__updatedAt")) or time_ms,
                    "administrationRoute": normalize_route(row.get("roa")),
                    "dose": tidy_number(parsed if parsed is not None else value),
                    "isDoseAnEstimate": False,
                    "estimatedDoseStandardDeviation": None,
                    "units": unit or "mg",
                    "notes": None,
                    "stomachFullness": None,
                    "consumerName": None,
                    "customUnitId": None,
                }
            )

        standalone_days += 1
        names: list[str] = []
        for ingestion in ingestions:
            if ingestion["substanceName"] not in names:
                names.append(ingestion["substanceName"])
        experiences.append(
            {
                "title": " + ".join(names) or "Intake",
                "text": "",
                "creationDate": base_ms,
                "sortDate": base_ms,
                "isFavorite": False,
                "ingestions": ingestions,
                "location": None,
                "ratings": [],
                "timedNotes": [],
            }
        )

    experiences.sort(key=lambda experience: experience["sortDate"])

    journal = {
        "experiences": experiences,
        "substanceCompanions": [],
        "customSubstances": [],
        "customUnits": [],
    }

    carried = {"u", "e", "v", "t"}
    dropped = [
        key
        for key in payload
        if key not in carried and payload.get(key) is not None
    ]

    stats = {
        "experiences": len(source_experiences),
        "standaloneRows": len(standalone),
        "standaloneDays": standalone_days,
        "duplicateRows": duplicate_rows,
        "skippedProjectedRows": skipped_projected_rows,
        "ingestions": sum(len(experience["ingestions"]) for experience in experiences),
        "droppedSections": dropped,
    }
    return journal, stats


def fallback_day_ms(day_key: Any) -> int:
    value = local_instant_ms(day_key)
    return value if value is not None else (to_ms(to_ms_now()) or 0)


def to_ms_now() -> int:
    from time import time

    return int(time() * 1000)


def validate_journal_app_file(journal: dict[str, Any]) -> list[str]:
    """Mirrors what the journal app requires in order to decode the file."""
    problems: list[str] = []

    keys = sorted(journal)
    if keys != ["customSubstances", "customUnits", "experiences", "substanceCompanions"]:
        problems.append(f"unexpected top level keys: {', '.join(keys)}")
    if "exportSource" in journal:
        problems.append("exportSource must not be set (the v12 path would rewrite it)")
    if not isinstance(journal["substanceCompanions"], list):
        problems.append("substanceCompanions is not a list")

    for index, experience in enumerate(journal["experiences"]):
        where = f"experiences[{index}]"
        if not isinstance(experience.get("title"), str):
            problems.append(f"{where}.title is not a string")
        if not isinstance(experience.get("text"), str):
            problems.append(f"{where}.text is not a string")
        if not isinstance(experience.get("sortDate"), int):
            problems.append(f"{where}.sortDate is not an integer")
        if not isinstance(experience.get("creationDate"), int):
            problems.append(f"{where}.creationDate is not an integer")
        if not isinstance(experience.get("isFavorite"), bool):
            problems.append(f"{where}.isFavorite is not a boolean")

        for position, ingestion in enumerate(experience.get("ingestions") or []):
            at = f"{where}.ingestions[{position}]"
            if not str(ingestion.get("substanceName") or ""):
                problems.append(f"{at}.substanceName missing")
            if not isinstance(ingestion.get("time"), int):
                problems.append(f"{at}.time is not an integer")
            if not isinstance(ingestion.get("isDoseAnEstimate"), bool):
                problems.append(f"{at}.isDoseAnEstimate is not a boolean")
            if ingestion.get("administrationRoute") not in ROUTES:
                problems.append(
                    f"{at}.administrationRoute is not a known route: "
                    f"{ingestion.get('administrationRoute')}"
                )
            dose = ingestion.get("dose")
            if dose is not None and not isinstance(dose, (int, float)):
                problems.append(f"{at}.dose is neither number nor null")
    return problems


SELF_TEST_PAYLOAD = {
    "v": 1,
    "t": 1791617033136,
    "u": [
        {"dateISO": "2026-10-08", "name": "Caffeine", "dose": "90 mg", "roa": "oral"},
        {"dateISO": "2026-10-09", "name": "THC", "dose": "5 mg", "roa": "smoked"},
        {
            "dateISO": "2026-10-09",
            "name": "CBD",
            "dose": "20 mg",
            "roa": "oral",
            "__expId": "exp-1",
            "__fromExperience": True,
        },
    ],
    "e": [
        {
            "id": "exp-1",
            "date": "2026-10-09",
            "title": "THC evening",
            "description": "test",
            "favorite": True,
            "__updatedAt": "2026-10-09T21:00:00.000Z",
            "substances": [
                {"name": "CBD", "timeHHMM": "20:30", "doseMult": 20, "roaName": "Oral"},
                {"name": "THC", "timeHHMM": "21:15", "doseMult": 5, "roaName": "Smoked", "day2": False},
            ],
        }
    ],
    "n": {"2026-10-08": "<div>note</div>"},
    "x": {"jrnl_stats_range": "90d"},
}


def self_test() -> bool:
    journal, stats = convert_to_journal_app(SELF_TEST_PAYLOAD)
    problems = validate_journal_app_file(journal)
    by_title = {experience["title"]: experience for experience in journal["experiences"]}
    evening = by_title.get("THC evening", {})

    checks = [
        ("no schema problems", not problems),
        ("projected row skipped", stats["skippedProjectedRows"] == 1),
        ("three experiences", len(journal["experiences"]) == 3),
        ("standalone rows counted", stats["standaloneRows"] == 2),
        ("standalone days grouped", stats["standaloneDays"] == 2),
        ("sorted by sortDate", journal["experiences"][0]["title"] == "Caffeine"),
        ("experience title kept", "THC evening" in by_title),
        ("favorite carried", evening.get("isFavorite") is True),
        ("two ingestions", len(evening.get("ingestions", [])) == 2),
        ("dose from doseMult", evening["ingestions"][1]["dose"] == 5),
        ("route mapped", evening["ingestions"][1]["administrationRoute"] == "SMOKED"),
        (
            "ingestion time from date+HH:MM",
            evening["ingestions"][1]["time"] == local_instant_ms("2026-10-09", "21:15"),
        ),
        ("units default mg", evening["ingestions"][0]["units"] == "mg"),
        ("dose parsed from string", by_title["Caffeine"]["ingestions"][0]["dose"] == 90),
        ("dropped sections reported", "n" in stats["droppedSections"]),
        ("empty companion arrays", journal["substanceCompanions"] == []),
    ]

    print("Self test / 自检")
    failed = 0
    for label, ok in checks:
        if not ok:
            failed += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    for problem in problems[:5]:
        print(f"         {problem}")
    print(
        f"\n{failed} check(s) failed / {failed} 项检查未通过"
        if failed
        else "\nAll checks passed / 全部检查通过"
    )
    return failed == 0


def clean_user_path(raw: str) -> str:
    """
    Turns whatever the user pasted into a path: drag and drop wraps the path in
    quotes, copying from a browser can give a file:// URL, and both often come
    with stray whitespace.
    """
    text = str(raw or "").strip().strip('"').strip("'").strip()
    if text.lower().startswith("file://"):
        parsed = urlparse(text)
        text = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:", text):
            text = text[1:]
    return text.strip()


STAT_LABEL_CELLS = 27


def stat_line(label: str, value: str) -> str:
    """`label` and `value` in two columns; Chinese labels count as two cells."""
    cells = sum(
        2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in label
    )
    return f"  {label}{' ' * max(1, STAT_LABEL_CELLS - cells)}{value}"


def prompt_for_input_file() -> Path | None:
    """Asks for the backup file until one is given, or returns None to cancel."""
    print("DrugsPRO 导出文件 -> journal app(PsyLog)导入格式")
    print("DrugsPRO export -> journal app (PsyLog) import format")
    print("可以把文件直接拖进这个窗口,也可以粘贴或输入完整路径。")
    print("Drag the file into this window, or paste / type the full path.")
    while True:
        try:
            raw = input("备份文件路径 / Backup file path (回车即退出 / Enter to quit): ")
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        candidate = clean_user_path(raw)
        if not candidate:
            return None

        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()
        if path.is_dir():
            print(f"这是一个目录,需要具体文件 / That is a directory, a file is needed: {path}")
        else:
            print(f"找不到文件 / File not found: {path}")
        print("请再试一次,或直接回车退出。 / Try again, or press Enter to quit.")


def run_conversion(input_path: Path, options: argparse.Namespace) -> int:
    journal, stats = convert_to_journal_app(
        read_backup_file(input_path), drop_duplicate_rows=options.dedupe
    )
    problems = validate_journal_app_file(journal)
    text = json.dumps(
        journal,
        ensure_ascii=False,
        indent=2 if options.pretty else None,
        separators=None if options.pretty else (",", ":"),
    )

    if options.stdout:
        print(text)
        return 0

    if options.out:
        out_path = Path(options.out).resolve()
    else:
        stem = re.sub(r"\.json$", "", input_path.name, flags=re.IGNORECASE)
        out_path = input_path.with_name(f"{stem}.journal-app.json")
    # newline="" keeps the file byte for byte the same as the app's own export
    # and as the Node version, instead of switching to CRLF on Windows.
    with open(out_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)

    print(f"Converted / 已转换  {input_path.name} -> {out_path}")
    print(
        stat_line(
            "experiences / 体验",
            f"{stats['experiences']} (from the journal / 来自导出中的体验)",
        )
    )
    print(
        stat_line(
            "standalone rows / 独立记录",
            f"{stats['standaloneRows']} -> {stats['standaloneDays']} day(s) / 天",
        )
    )
    print(
        stat_line(
            "skipped rows / 跳过记录",
            f"{stats['skippedProjectedRows']} "
            "(an experience's projection / 属于某次体验的投影行)",
        )
    )
    print(stat_line("ingestions / 摄入记录", f"{stats['ingestions']} in total / 共"))
    if stats["duplicateRows"]:
        suffix = (
            " (collapsed / 已合并)"
            if options.dedupe
            else " (same day, substance, dose and route / 同日、同物质、同剂量、同途径; --dedupe 合并)"
        )
        print(
            stat_line(
                "duplicate rows / 重复记录", f"{stats['duplicateRows']}{suffix}"
            )
        )
    print(stat_line("size / 大小", f"{len(text.encode('utf-8'))} bytes / 字节"))
    if stats["droppedSections"]:
        print(
            stat_line(
                "not carried over / 未迁移:",
                ", ".join(stats["droppedSections"]),
            )
        )
    print(
        stat_line(
            "schema",
            f"{len(problems)} problem(s) / 结构问题: {problems[0]}"
            if problems
            else "ok / 结构检查通过",
        )
    )
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    # Keep a legacy Windows console from blowing up on the Chinese prompts.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        description=(
            "Convert a DrugsPRO journal export into the journal app's import format.\n"
            "将 DrugsPRO 的日记导出转换成 journal app(PsyLog)的导入格式。\n"
            "Run it without a file to be asked for one / 不带文件运行时会先询问文件路径。"
        ),
        formatter_class=functools.partial(
            argparse.RawDescriptionHelpFormatter, max_help_position=34, width=100
        ),
        epilog=(
            "examples / 示例:\n"
            "  python DrugsPRO_to_PsyLogJournal.py                  # interactive / 交互式\n"
            "  python DrugsPRO_to_PsyLogJournal.py backup.json      # -> backup.journal-app.json\n"
            "  python DrugsPRO_to_PsyLogJournal.py backup.json -o out.json --pretty\n"
            "  python DrugsPRO_to_PsyLogJournal.py backup.json --stdout\n"
            "  python DrugsPRO_to_PsyLogJournal.py --self-test\n"
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="DrugsPRO export (json, json.gz, ...) / DrugsPRO 导出文件(json、json.gz 等)",
    )
    parser.add_argument(
        "-o",
        "--out",
        help="output path / 输出路径(默认 <备份文件>.journal-app.json)",
    )
    parser.add_argument(
        "--pretty", action="store_true", help="indent the JSON / 输出缩进后的 JSON"
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help=(
            "collapse same-day rows that repeat substance, dose and route / "
            "合并同日物质、剂量、途径都相同的记录"
        ),
    )
    parser.add_argument(
        "--stdout", action="store_true", help="print instead of writing / 只打印不写文件"
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help=(
            "do not wait for Enter after an interactive run / "
            "交互式运行结束后不等待回车"
        ),
    )
    parser.add_argument(
        "--self-test", action="store_true", help="run the built-in checks / 运行内置自检"
    )
    options = parser.parse_args(argv)

    if options.self_test:
        return 0 if self_test() else 1

    input_path: Path | None = None
    if options.input:
        candidate = Path(clean_user_path(options.input)).expanduser()
        if candidate.is_file():
            input_path = candidate.resolve()
        elif candidate.is_dir():
            print(f"这是一个目录,需要具体文件 / That is a directory, a file is needed: {candidate}")
        else:
            print(f"找不到文件 / File not found: {candidate}")

    # No usable path on the command line (double click, or a typo): ask for it.
    interactive = input_path is None
    if interactive:
        input_path = prompt_for_input_file()
        if input_path is None:
            print("已取消,没有转换任何东西。 / Cancelled, nothing was converted.")
            return 1

    try:
        code = run_conversion(input_path, options)
    except (ValueError, OSError) as exc:
        print(f"转换失败 / Conversion failed: {exc}")
        code = 1

    if interactive and not options.no_pause and sys.stdin.isatty():
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            input("\n按回车键退出... / Press Enter to exit...")
    return code


if __name__ == "__main__":
    sys.exit(main())
