import json
import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from bs4 import BeautifulSoup

from canton_fair_alert.models import FairWindow


class ParseError(ValueError):
    pass


class CantonFairHtmlParser:
    version = "1.0"
    _phase_pattern = re.compile(r"(?:第\s*([一二三123])\s*期|Phase\s*([123]))", re.I)
    _full_range = re.compile(
        r"(20\d{2})\s*(?:年|[-/.])\s*(1[0-2]|0?[1-9])\s*(?:月|[-/.])\s*"
        r"([0-3]?\d)\s*日?\s*(?:至|到|[-–—~至])\s*"
        r"(?:(20\d{2})\s*(?:年|[-/.])\s*)?"
        r"(1[0-2]|0?[1-9])\s*(?:月|[-/.])\s*([0-3]?\d)\s*日?"
    )
    _english_range = re.compile(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+([0-3]?\d)\s*(?:to|[-–—~])\s*"
        r"(?:(January|February|March|April|May|June|July|August|September|October|November|December)\s+)?"
        r"([0-3]?\d)\s*,?\s*(20\d{2})",
        re.I,
    )
    _months = {
        name.lower(): index
        for index, name in enumerate(
            (
                "January",
                "February",
                "March",
                "April",
                "May",
                "June",
                "July",
                "August",
                "September",
                "October",
                "November",
                "December",
            ),
            start=1,
        )
    }

    def parse(self, html: bytes, source_name: str, source_url: str) -> Sequence[FairWindow]:
        soup = BeautifulSoup(html, "lxml")
        edition = self._edition(soup.get_text(" ", strip=True))
        dom_results = self._parse_dom(soup, edition, source_name, source_url)
        if len(dom_results) == 3:
            return dom_results
        json_results = self._parse_json_ld(soup, edition, source_name, source_url)
        if len(json_results) == 3:
            return json_results
        text_results = self._parse_text(
            soup.get_text(" ", strip=True), edition, source_name, source_url
        )
        if not text_results:
            raise ParseError("no deterministic phase/date ranges found")
        return text_results

    def _parse_dom(
        self,
        soup: BeautifulSoup,
        edition: Optional[int],
        source_name: str,
        source_url: str,
    ) -> List[FairWindow]:
        results = []
        for node in soup.select("[data-phase][data-start][data-end]"):
            try:
                phase = int(str(node.get("data-phase")))
                start = date.fromisoformat(str(node.get("data-start")))
                end = date.fromisoformat(str(node.get("data-end")))
            except (TypeError, ValueError):
                continue
            results.append(self._window(edition, phase, start, end, source_name, source_url))
        return self._deduplicate(results)

    def _parse_json_ld(
        self,
        soup: BeautifulSoup,
        edition: Optional[int],
        source_name: str,
        source_url: str,
    ) -> List[FairWindow]:
        texts: List[str] = []
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                value: Any = json.loads(script.get_text())
            except (json.JSONDecodeError, TypeError):
                continue
            texts.extend(self._json_strings(value))
        return self._parse_text(" ".join(texts), edition, source_name, source_url)

    def _json_strings(self, value: Any) -> Iterable[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from self._json_strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from self._json_strings(item)

    def _parse_text(
        self,
        text: str,
        edition: Optional[int],
        source_name: str,
        source_url: str,
    ) -> List[FairWindow]:
        results = []
        matches = list(self._phase_pattern.finditer(text))
        for index, match in enumerate(matches):
            phase = self._phase_number(match.group(1) or match.group(2))
            section_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            section = text[match.end() : section_end]
            date_range = self._full_range.search(section[:300])
            try:
                if date_range:
                    start, end = self._dates(date_range.groups())
                else:
                    english_range = self._english_range.search(section[:300])
                    if not english_range:
                        continue
                    month_name, start_day, end_month_name, end_day, year = english_range.groups()
                    month = self._months[month_name.lower()]
                    end_month = self._months[(end_month_name or month_name).lower()]
                    start = date(int(year), month, int(start_day))
                    end = date(int(year), end_month, int(end_day))
            except ValueError:
                continue
            results.append(self._window(edition, phase, start, end, source_name, source_url))
        return self._deduplicate(results)

    @staticmethod
    def _dates(groups: Tuple[str, ...]) -> Tuple[date, date]:
        start_year, start_month, start_day, end_year, end_month, end_day = groups
        first = date(int(start_year), int(start_month), int(start_day))
        second = date(int(end_year or start_year), int(end_month), int(end_day))
        return first, second

    @staticmethod
    def _phase_number(value: str) -> int:
        return {"一": 1, "二": 2, "三": 3}.get(value, int(value) if value.isdigit() else 0)

    @staticmethod
    def _edition(text: str) -> Optional[int]:
        patterns = (
            r"第\s*(\d{2,3})\s*届",
            r"(?:the\s+)?(\d{2,3})(?:st|nd|rd|th)\s+Canton\s+Fair",
            r"(?:the\s+)?(\d{2,3})(?:st|nd|rd|th)\s*\((?:spring|autumn)\)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _season(start: date) -> str:
        if start.month in {4, 5}:
            return "spring"
        if start.month in {10, 11}:
            return "autumn"
        return "unknown"

    def _window(
        self,
        edition: Optional[int],
        phase: int,
        start: date,
        end: date,
        source_name: str,
        source_url: str,
    ) -> FairWindow:
        return FairWindow(
            edition=edition,
            season=self._season(start),
            phase=phase,
            start_date=start,
            end_date=end,
            event_type="exhibition",
            source_name=source_name,
            source_url=source_url,
        )

    @staticmethod
    def _deduplicate(windows: Sequence[FairWindow]) -> List[FairWindow]:
        by_phase: Dict[int, FairWindow] = {}
        for window in windows:
            by_phase.setdefault(window.phase, window)
        return [by_phase[key] for key in sorted(by_phase)]
