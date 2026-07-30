"""Detection and reset-time parsing for Claude subscription limits."""

from __future__ import annotations

import codecs
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

ANSI_RE = re.compile(
    r"""
    \x1B
    (?:
        \][^\x07\x1B]*(?:\x07|\x1B\\)   # OSC
      | \[[0-?]*[ -/]*[@-~]              # CSI
      | [PX^_].*?\x1B\\                  # DCS/SOS/PM/APC
      | [@-_]                              # 2-character escape
    )
    """,
    re.VERBOSE | re.DOTALL,
)

LIMIT_RE = re.compile(
    r"you(?:'|’)ve\s+hit\s+your\s+"
    r"(?P<kind>session|weekly|opus|sonnet)\s+limit",
    re.IGNORECASE,
)

RESET_RE = re.compile(
    r"(?:(?:your\s+)?(?:session|weekly|opus|sonnet)\s+)?"
    r"limit\s+resets(?:\s+(?:at|on))?\s+"
    r"(?P<when>[^\n\r│╭╰]{1,100})",
    re.IGNORECASE,
)

ISO_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?\b"
)

TIME_RE = re.compile(
    r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)

MONTH_RE = re.compile(
    r"\b(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?",
    re.IGNORECASE,
)

WEEKDAY_RE = re.compile(
    r"\b(?P<weekday>Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|"
    r"Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b",
    re.IGNORECASE,
)

MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


@dataclass(frozen=True)
class LimitEvent:
    kind: str
    cooldown_until: datetime
    reset_text: str | None
    used_fallback: bool


def normalize_terminal_text(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = text.replace("\r", "\n")
    text = text.replace("\b", "")
    return "".join(
        character for character in text if character in "\n\t" or ord(character) >= 32
    )


def parse_reset_time(
    text: str,
    *,
    now: datetime,
    kind: str,
) -> tuple[datetime, bool]:
    """Parse Claude's localized-enough English reset label.

    Claude currently renders reset labels such as ``3:00 PM``,
    ``Wednesday at 3 PM``, and ``Jul 31, 3 PM``. If the presentation changes,
    use a conservative duration based on the limit type.
    """

    iso_match = ISO_RE.search(text)
    if iso_match:
        value = iso_match.group(0).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=now.tzinfo)
            return parsed.astimezone(now.tzinfo), False
        except ValueError:
            pass

    time_match = TIME_RE.search(text)
    if time_match:
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute") or 0)
        ampm = time_match.group("ampm").lower().replace(".", "")
        if 1 <= hour <= 12 and minute <= 59:
            hour = hour % 12 + (12 if ampm == "pm" else 0)

            month_match = MONTH_RE.search(text)
            if month_match:
                month = MONTHS[month_match.group("month")[:3].lower()]
                day = int(month_match.group("day"))
                year = int(month_match.group("year") or now.year)
                try:
                    candidate = now.replace(
                        year=year,
                        month=month,
                        day=day,
                        hour=hour,
                        minute=minute,
                        second=0,
                        microsecond=0,
                    )
                    if month_match.group(
                        "year"
                    ) is None and candidate < now - timedelta(days=1):
                        candidate = candidate.replace(year=year + 1)
                    return candidate, False
                except ValueError:
                    pass

            weekday_match = WEEKDAY_RE.search(text)
            if weekday_match:
                target = WEEKDAYS[weekday_match.group("weekday")[:3].lower()]
                days_ahead = (target - now.weekday()) % 7
                candidate = (now + timedelta(days=days_ahead)).replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0,
                )
                if candidate <= now:
                    candidate += timedelta(days=7)
                return candidate, False

            days_ahead = 1 if re.search(r"\btomorrow\b", text, re.IGNORECASE) else 0
            candidate = (now + timedelta(days=days_ahead)).replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate, False

    fallback = timedelta(hours=5, minutes=5)
    if kind.lower() in {"weekly", "opus", "sonnet"}:
        fallback = timedelta(days=7, minutes=5)
    return now + fallback, True


class LimitDetector:
    """Incrementally detect a blocking Claude usage-limit message."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime],
        max_buffer: int = 16_384,
    ) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        self._raw_buffer = ""
        self._now = now
        self._max_buffer = max_buffer
        self._triggered = False

    def feed(self, data: bytes) -> LimitEvent | None:
        if self._triggered:
            return None

        self._raw_buffer += self._decoder.decode(data)
        self._raw_buffer = self._raw_buffer[-self._max_buffer :]
        normalized = normalize_terminal_text(self._raw_buffer)

        limit_matches = list(LIMIT_RE.finditer(normalized))
        if not limit_matches:
            return None

        match = limit_matches[-1]
        kind = match.group("kind").lower()
        nearby = normalized[max(0, match.start() - 2_000) : match.end() + 2_000]
        reset_matches = list(RESET_RE.finditer(nearby))
        reset_text = reset_matches[-1].group("when").strip() if reset_matches else None
        until, used_fallback = parse_reset_time(
            reset_text or "",
            now=self._now(),
            kind=kind,
        )
        self._triggered = True
        return LimitEvent(
            kind=kind,
            cooldown_until=until,
            reset_text=reset_text,
            used_fallback=used_fallback,
        )
