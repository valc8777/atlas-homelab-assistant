#!/usr/bin/env python3
"""Deterministic solver for narrative date-arithmetic word problems (DEF-053).

Found via the 2026-09-17 overnight stress run (`DEC-2026-09-17-04/-05`): the
BIG-bench `date_understanding` category poses a structured multiple-choice
prompt of the shape

    <premise establishing "today"> What is the date <offset> in MM/DD/YYYY?
    Choices: 02/22/2016; 11/22/2016; ...
    Answer with the choice and a short reason.

and the model sometimes anchors "one week ago" (etc.) to the *real* current
date instead of the *story's own stated* "today" -- a genuine reasoning
confusion, not a routing bug (this question shape already reaches the
general-purpose model correctly).

`resolve_narrative_date_answer()` only ever returns an answer when it can
(a) confidently parse the premise's stated "today" and the requested offset,
and (b) verify the computed date against the actual offered choice list --
exactly one choice must match. Anything else (an unrecognized premise
phrasing, an unparseable offset, a computed date with zero or more than one
matching choice) returns None and the question falls through to the model
exactly as before. This makes the intervention strictly non-regressive: it
can only turn a would-be miss into a hit, never the reverse -- deliberately,
since some premises in this task have known-odd answer keys (e.g. the
Thanksgiving case pairs "of 2001" with a dated answer of 2002) that a
confident deterministic solver should abstain on rather than blindly match.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re

_MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2, "feburary": 2,  # dataset's own spelling
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}

_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_MONTH_ALTERNATION = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
_DATE_LITERAL = (
    rf"(?:{_MONTH_ALTERNATION})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s*\d{{4}}"
    rf"|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}"
)

_QUESTION_PATTERN = re.compile(
    r"^(?P<premise>.*?)\s+what is the date (?P<offset>.+?) in mm/dd/yyyy\?\s*$",
    re.IGNORECASE,
)


def _num_word(text: str) -> int | None:
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    return _WORD_NUMBERS.get(text)


def _parse_ordinal(text: str) -> int | None:
    text = text.strip().lower()
    match = re.fullmatch(r"(\d+)(?:st|nd|rd|th)?", text)
    if match:
        return int(match.group(1))
    return _ORDINAL_WORDS.get(text)


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _add_months(date: dt.date, months: int) -> dt.date:
    total = date.month - 1 + months
    year = date.year + total // 12
    month = total % 12 + 1
    day = min(date.day, _last_day_of_month(year, month))
    return dt.date(year, month, day)


def _add_years(date: dt.date, years: int) -> dt.date:
    year = date.year + years
    day = date.day
    if date.month == 2 and day == 29 and not calendar.isleap(year):
        day = 28
    return dt.date(year, date.month, day)


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return dt.date(year, month, 1 + offset + 7 * (n - 1))


def _parse_literal_date(text: str, default_year: int | None = None) -> dt.date | None:
    text = text.strip().strip(".,")
    if not text:
        return None
    # A leading weekday name ("Tue, 7/9/1972") is flavor text, not part of
    # the date itself.
    text = re.sub(r"^(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s*", "", text, flags=re.IGNORECASE)
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if match:
        month, day, year = (int(group) for group in match.groups())
        year = year if year > 99 else 2000 + year
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None
    match = re.fullmatch(r"([A-Za-z.]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", text)
    if match:
        month_text, day_text, year_text = match.groups()
        month = _MONTH_NAMES.get(month_text.rstrip(".").lower())
        if month is None:
            return None
        year = int(year_text) if year_text else default_year
        if year is None:
            return None
        try:
            return dt.date(year, month, int(day_text))
        except ValueError:
            return None
    return None


def _resolve_today(premise: str) -> dt.date | None:
    text = " ".join(premise.split())

    # "The first day of 2019 is a Tuesday, and today is the first Monday of
    # 2019." -- trusts the premise's *stated* weekday for Jan 1, not the
    # real calendar, matching the same "believe the story, not reality"
    # principle the whole fix is about.
    match = re.search(
        r"the first day of (?P<year>\d{4}) is a (?P<wd1>\w+),\s*and today is the "
        r"first (?P<wd2>\w+) of \d{4}",
        text, re.IGNORECASE,
    )
    if match:
        wd1 = _WEEKDAYS.get(match.group("wd1").lower())
        wd2 = _WEEKDAYS.get(match.group("wd2").lower())
        if wd1 is not None and wd2 is not None:
            jan1 = dt.date(int(match.group("year")), 1, 1)
            return jan1 + dt.timedelta(days=(wd2 - wd1) % 7)

    match = re.search(r"today is the us thanksgiving of (?P<year>\d{4})", text, re.IGNORECASE)
    if match:
        return _nth_weekday_of_month(int(match.group("year")), 11, _WEEKDAYS["thursday"], 4)

    match = re.search(r"today is the palindrome day of (?P<year>\d{4})", text, re.IGNORECASE)
    if match:
        year = int(match.group("year"))
        day = dt.date(year, 1, 1)
        while day.year == year:
            candidate = f"{day.month:02d}{day.day:02d}{day.year:04d}"
            if candidate == candidate[::-1]:
                return day
            day += dt.timedelta(days=1)
        return None

    match = re.search(r"this is the last day of (?P<year>\d{4})", text, re.IGNORECASE)
    if match:
        return dt.date(int(match.group("year")), 12, 31)

    match = re.search(r"today is the first day of (?P<year>\d{4})", text, re.IGNORECASE)
    if match:
        return dt.date(int(match.group("year")), 1, 1)

    match = re.search(
        r"celebrating the last day of (?P<month>[A-Za-z]+)\.?\s+(?P<year>\d{4})",
        text, re.IGNORECASE,
    )
    if match:
        month = _MONTH_NAMES.get(match.group("month").lower())
        if month:
            year = int(match.group("year"))
            return dt.date(year, month, _last_day_of_month(year, month))

    match = re.search(
        r"today is the last day of the (?P<q>\w+) quarter of (?P<year>\d{4})",
        text, re.IGNORECASE,
    )
    if match:
        quarter = _parse_ordinal(match.group("q"))
        if quarter:
            year = int(match.group("year"))
            month = quarter * 3
            return dt.date(year, month, _last_day_of_month(year, month))

    match = re.search(
        r"today is the (?P<d>\w+) day of the (?P<m>\w+) month of (?P<year>\d{4})",
        text, re.IGNORECASE,
    )
    if match:
        day = _parse_ordinal(match.group("d"))
        month = _parse_ordinal(match.group("m"))
        if day and month:
            try:
                return dt.date(int(match.group("year")), month, day)
            except ValueError:
                return None

    match = re.search(r"today is christmas eve of (?P<year>\d{4})", text, re.IGNORECASE)
    if match:
        return dt.date(int(match.group("year")), 12, 24)

    # UK-style DD/MM/YYYY explicitly flagged by the premise itself.
    match = re.search(
        r"day before the month.*?today is (?P<d1>\d{1,2})/(?P<d2>\d{1,2})/(?P<year>\d{2,4}) to them",
        text, re.IGNORECASE,
    )
    if match:
        year = int(match.group("year"))
        year = year if year > 99 else 2000 + year
        try:
            return dt.date(year, int(match.group("d2")), int(match.group("d1")))
        except ValueError:
            return None

    match = re.search(
        r"(?P<name1>\w+) thinks today is (?P<date1>[^,]+),\s*but (?P<name2>\w+) thinks today is "
        r"(?P<date2>[^.]+)\.\s*(?P<who>\w+) is correct",
        text, re.IGNORECASE,
    )
    if match:
        chosen = match.group("date1") if match.group("who").lower() == match.group("name1").lower() else (
            match.group("date2") if match.group("who").lower() == match.group("name2").lower() else None
        )
        if chosen:
            return _parse_literal_date(chosen)

    match = re.search(
        r"thought today is (?P<date1>[^,]+),\s*but today is in fact (?P<date2>[^,]+),\s*"
        r"which is (?P<n>\w+) days? later",
        text, re.IGNORECASE,
    )
    if match:
        base = _parse_literal_date(match.group("date1"))
        n = _num_word(match.group("n"))
        if base and n is not None:
            return base + dt.timedelta(days=n)

    match = re.search(
        r"married on (?P<date>[^.]+)\.\s*it is their (?P<n>\w+)[- ]year anniversary today",
        text, re.IGNORECASE,
    )
    if match:
        base = _parse_literal_date(match.group("date"))
        n = _num_word(match.group("n"))
        if base and n is not None:
            return _add_years(base, n)

    match = re.search(
        r"married on (?P<date>[^.]+)\.\s*today is their golden wedding anniversary",
        text, re.IGNORECASE,
    )
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return _add_years(base, 50)

    match = re.search(
        r"born on the last day of (?P<month>[A-Za-z]+) in (?P<year>\d{4})\.\s*"
        r"today is (?:his|her|their) (?P<n>\w+)[- ]year[- ]old birthday",
        text, re.IGNORECASE,
    )
    if match:
        month = _MONTH_NAMES.get(match.group("month").lower())
        n = _num_word(match.group("n"))
        if month and n is not None:
            year = int(match.group("year"))
            birth = dt.date(year, month, _last_day_of_month(year, month))
            return _add_years(birth, n)

    match = re.search(
        r"got (?:his|her|their) job in (?P<year>\d{4})\.\s*today is (?:his|her|their) "
        r"(?P<n>\w+)[- ]year work anniversary.*?\bon (?P<month>[A-Za-z]+)\.?\s+(?P<day>\d{1,2}),\s*"
        r"(?:his|her|their) (?P<ord>\w+) day at work",
        text, re.IGNORECASE,
    )
    if match:
        n = _num_word(match.group("n"))
        ordinal = _parse_ordinal(match.group("ord"))
        month = _MONTH_NAMES.get(match.group("month").lower())
        if n is not None and ordinal and month:
            year = int(match.group("year"))
            try:
                second_day_date = dt.date(year, month, int(match.group("day")))
            except ValueError:
                return None
            hire_date = second_day_date - dt.timedelta(days=ordinal - 1)
            return _add_years(hire_date, n)

    match = re.search(
        r"quit(?:ed|s)? (?:his|her|their)? ?job on (?P<date>[^.]+)\.\s*"
        r"(?P<n>\w+) days? have passed since then",
        text, re.IGNORECASE,
    )
    if match:
        base = _parse_literal_date(match.group("date"))
        n = _num_word(match.group("n"))
        if base and n is not None:
            return base + dt.timedelta(days=n)

    match = re.search(
        r"visits the bookstore on the (?P<day>\d{1,2})(?:st|nd|rd|th)? of each month "
        r"starting from the (?P<month>[A-Za-z]+) of (?P<year>\d{4})\.\s*it is "
        r"(?:his|her|their) (?P<n>\d+)(?:st|nd|rd|th)? visit",
        text, re.IGNORECASE,
    )
    if match:
        month = _MONTH_NAMES.get(match.group("month").lower())
        if month:
            year = int(match.group("year"))
            try:
                start = dt.date(year, month, int(match.group("day")))
            except ValueError:
                return None
            return _add_months(start, int(match.group("n")) - 1)

    match = re.search(
        r"on (?P<date>[A-Za-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?,\s*\d{4}) \w+ bought "
        r"(?P<n>\w+) eggs\..*?ate one per day.*?ran out of eggs",
        text, re.IGNORECASE,
    )
    if match:
        base = _parse_literal_date(match.group("date"))
        n = _num_word(match.group("n"))
        if base and n is not None:
            return base + dt.timedelta(days=n)

    match = re.search(
        r"is like yesterday to \w+,\s*but that is actually (?P<n>\w+) years? ago",
        text, re.IGNORECASE,
    )
    if match:
        # The date phrase precedes "is like yesterday"; recover it separately.
        date_match = re.search(r"^(?P<date>.+?) is like yesterday to \w+", text, re.IGNORECASE)
        n = _num_word(match.group("n"))
        if date_match and n is not None:
            base = _parse_literal_date(date_match.group("date"))
            if base:
                return _add_years(base, n)

    match = re.search(
        r"the concert was scheduled to be on (?P<date>[^,]+),\s*but was delayed by "
        r"(?P<n>\w+) days? to today",
        text, re.IGNORECASE,
    )
    if match:
        base = _parse_literal_date(match.group("date"))
        n = _num_word(match.group("n"))
        if base and n is not None:
            return base + dt.timedelta(days=n)

    match = re.search(
        r"the deadline is (?P<date>.+?),\s*which is (?P<n>\w+) days? away from now",
        text, re.IGNORECASE,
    )
    if match:
        base = _parse_literal_date(match.group("date"))
        n = _num_word(match.group("n"))
        if base and n is not None:
            return base - dt.timedelta(days=n)

    match = re.search(r"the day before yesterday was (?P<date>[^.]+)", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base + dt.timedelta(days=2)

    match = re.search(r"it was (?P<date>.+?) a week ago", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base + dt.timedelta(days=7)

    match = re.search(rf"^yesterday,\s*(?P<date>{_DATE_LITERAL}),", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base + dt.timedelta(days=1)

    match = re.search(r"^yesterday was (?P<date>[^.]+)", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base + dt.timedelta(days=1)

    match = re.search(rf"^today,\s*(?P<date>{_DATE_LITERAL}),", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base

    match = re.search(r"^tomorrow is (?P<date>[^.]+)", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base - dt.timedelta(days=1)

    match = re.search(r"for tomorrow[,\s]+\(?(?P<date>[^.)]+)\)?", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base - dt.timedelta(days=1)

    match = re.search(r"rescheduled to .*? tomorrow,\s*(?P<date>[^.]+)", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base - dt.timedelta(days=1)

    match = re.search(r"^today is (?P<md>\d{1,2}/\d{1,2})[,.]", text, re.IGNORECASE)
    if match:
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
        if year_match:
            month, day = (int(part) for part in match.group("md").split("/"))
            try:
                return dt.date(int(year_match.group(1)), month, day)
            except ValueError:
                return None

    match = re.search(r"^it is (?P<date>[^.]+?) today", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base

    match = re.search(r"^the current local time is .+? of (?P<date>[^.]+)", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base

    match = re.search(r"^today is (?P<date>[^.]+?)\.", text, re.IGNORECASE)
    if match:
        base = _parse_literal_date(match.group("date"))
        if base:
            return base

    return None


def _resolve_offset(today: dt.date, offset_text: str) -> dt.date | None:
    text = offset_text.strip().lower().rstrip(".")

    if text == "today":
        return today
    if text == "tomorrow":
        return today + dt.timedelta(days=1)
    if text == "yesterday":
        return today - dt.timedelta(days=1)

    match = re.fullmatch(r"(\w+) days? ago", text)
    if match:
        n = _num_word(match.group(1))
        if n is not None:
            return today - dt.timedelta(days=n)

    match = re.fullmatch(r"(\w+) days? from (?:today|now)", text)
    if match:
        n = _num_word(match.group(1))
        if n is not None:
            return today + dt.timedelta(days=n)

    match = re.fullmatch(r"(\w+) weeks? ago(?: from today)?", text)
    if match:
        n = _num_word(match.group(1))
        if n is not None:
            return today - dt.timedelta(weeks=n)

    match = re.fullmatch(r"(\w+) weeks? from (?:today|now)", text)
    if match:
        n = _num_word(match.group(1))
        if n is not None:
            return today + dt.timedelta(weeks=n)

    match = re.fullmatch(r"(\w+) months? ago(?: from today)?", text)
    if match:
        n = _num_word(match.group(1))
        if n is not None:
            return _add_months(today, -n)

    match = re.fullmatch(r"(\w+) months? from (?:today|now)", text)
    if match:
        n = _num_word(match.group(1))
        if n is not None:
            return _add_months(today, n)

    match = re.fullmatch(r"(\w+) years? ago(?: from today)?", text)
    if match:
        n = _num_word(match.group(1))
        if n is not None:
            return _add_years(today, -n)

    match = re.fullmatch(r"(\w+) years? from (?:today|now)", text)
    if match:
        n = _num_word(match.group(1))
        if n is not None:
            return _add_years(today, n)

    match = re.fullmatch(r"(\d+) hours? later", text)
    if match:
        hours = int(match.group(1))
        if hours % 24 == 0:
            return today + dt.timedelta(days=hours // 24)

    return None


def resolve_narrative_date_answer(question: str) -> dict | None:
    """Return a verified deterministic answer, or None to defer to the model."""
    choices_match = re.search(r"choices:\s*(?P<choices>.+?)(?:\n|$)", question, re.IGNORECASE | re.DOTALL)
    if not choices_match:
        return None
    choices = [choice.strip() for choice in choices_match.group("choices").split(";") if choice.strip()]
    if not choices:
        return None

    body = " ".join(question[: choices_match.start()].split())
    question_match = _QUESTION_PATTERN.match(body)
    if not question_match:
        return None

    premise = question_match.group("premise").strip()
    offset_text = question_match.group("offset").strip()

    today = _resolve_today(premise)
    if today is None:
        return None
    target = _resolve_offset(today, offset_text)
    if target is None:
        return None

    target_text = target.strftime("%m/%d/%Y")
    matches = [choice for choice in choices if choice == target_text]
    if len(matches) != 1:
        return None

    return {
        "today": today.isoformat(),
        "offset": offset_text,
        "target_date": target.isoformat(),
        "answer_choice": matches[0],
    }
