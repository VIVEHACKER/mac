"""Bank of Korea ECOS provider — Korean macro series for the forecaster.

Implements the same ``MacroDataProvider.series(series_id)`` contract as
``FredCsvProvider`` so ``macro_forecast`` runs unchanged on Korean data.

``series_id`` encodes the ECOS statistic and item as ``"STAT_CODE/ITEM_CODE"``
(e.g. ``"901Y009/0"`` for the CPI total index, ``"404Y014/*AA"`` for the PPI
total index). The API key is read from ``ECOS_API_KEY``; absent that it falls
back to the public ``sample`` key, which returns only a short window — enough to
smoke-test, but a free key (https://ecos.bok.or.kr/api/) unlocks full history.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Callable
from urllib.parse import quote

from .macro import (
    DEFAULT_SERIES_NAMES,
    FredSeries,
    MacroDataError,
    MacroObservation,
    default_fetch_text,
)

ECOS_BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"

KR_SERIES_NAMES = {
    "901Y009/0": "Korea Consumer Price Index",
    "404Y014/*AA": "Korea Producer Price Index",
    "901Y009": "Korea Consumer Price Index",
    "404Y014": "Korea Producer Price Index",
}


def ecos_time_to_date(time_str: str) -> date:
    """ECOS TIME for a monthly series is 'YYYYMM'; map to the first of the month."""
    s = time_str.strip()
    if len(s) == 6:
        return date(int(s[:4]), int(s[4:6]), 1)
    if len(s) == 4:
        return date(int(s), 1, 1)
    if len(s) == 8:  # daily YYYYMMDD
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    raise MacroDataError(f"ECOS: unrecognised TIME format {time_str!r}")


def parse_ecos_json(text: str, series_id: str, source: str) -> FredSeries:
    payload = json.loads(text)
    block = payload.get("StatisticSearch")
    if not isinstance(block, dict):
        # ECOS returns {"RESULT": {"CODE": ..., "MESSAGE": ...}} on errors.
        result = payload.get("RESULT") or {}
        raise MacroDataError(
            f"{series_id}: ECOS error {result.get('CODE', '?')} {result.get('MESSAGE', text[:120])}"
        )
    rows = block.get("row")
    if not isinstance(rows, list) or not rows:
        result = block.get("RESULT") or {}
        raise MacroDataError(
            f"{series_id}: ECOS returned no rows ({result.get('MESSAGE', 'empty')})"
        )
    observations: list[MacroObservation] = []
    for row in rows:
        raw = row.get("DATA_VALUE")
        if raw in (None, "", "."):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        observations.append(MacroObservation(series_id, ecos_time_to_date(row["TIME"]), value))
    if not observations:
        raise MacroDataError(f"{series_id}: ECOS rows had no numeric observations")
    observations.sort(key=lambda o: o.observed_at)
    return FredSeries(
        series_id=series_id,
        name=KR_SERIES_NAMES.get(series_id, DEFAULT_SERIES_NAMES.get(series_id, series_id)),
        source=source,
        observations=tuple(observations),
    )


class EcosProvider:
    """Fetches Korean macro series from the Bank of Korea ECOS Open API."""

    def __init__(
        self,
        api_key: str | None = None,
        fetch_text: Callable[[str], str] | None = None,
        cycle: str = "M",
        start: str = "200001",
        end: str | None = None,
        max_rows: int = 5000,
    ):
        self.api_key = api_key or os.environ.get("ECOS_API_KEY") or "sample"
        self.fetch_text = fetch_text or default_fetch_text
        self.cycle = cycle
        self.is_sample = self.api_key == "sample"
        # The public "sample" key is hard-capped at 10 rows per call by ECOS.
        # We honour the cap but STITCH consecutive 10-month windows so the full
        # history is still retrievable without a key (~14 calls for 2015+).
        # A free key (https://ecos.bok.or.kr/api/) does it in one call.
        if self.is_sample and start == "200001":
            start = "201501"  # bound the stitch to ~14 windows
        self.start = start
        self.end = end or _default_end_month()
        self.max_rows = 10 if self.is_sample else max_rows

    def _split(self, series_id: str) -> tuple[str, str | None]:
        if "/" in series_id:
            stat, item = series_id.split("/", 1)
            return stat.strip(), item.strip()
        return series_id.strip(), None

    def _url(self, stat: str, item: str | None, start: str, end: str) -> str:
        parts = [
            ECOS_BASE_URL,
            quote(self.api_key, safe=""),
            "json",
            "kr",
            "1",
            str(self.max_rows),
            quote(stat, safe=""),
            self.cycle,
            start,
            end,
        ]
        if item:
            parts.append(quote(item, safe=""))
        return "/".join(parts)

    def series(self, series_id: str) -> FredSeries:
        stat, item = self._split(series_id)
        if not (self.is_sample and self.cycle == "M"):
            url = self._url(stat, item, self.start, self.end)
            return parse_ecos_json(self.fetch_text(url), series_id, url)

        # Sample key + monthly cycle: stitch 10-month windows to beat the cap.
        merged: dict[date, MacroObservation] = {}
        last_url = ""
        errors: list[str] = []
        for win_start, win_end in _month_windows(self.start, self.end, size=10):
            last_url = self._url(stat, item, win_start, win_end)
            try:
                window = parse_ecos_json(self.fetch_text(last_url), series_id, last_url)
            except MacroDataError as exc:
                errors.append(str(exc))
                continue  # a window may legitimately predate the series
            for obs in window.observations:
                merged[obs.observed_at] = obs
        if not merged:
            raise MacroDataError(
                f"{series_id}: ECOS sample stitch returned no data "
                f"({errors[-1] if errors else 'no windows'})"
            )
        observations = tuple(merged[d] for d in sorted(merged))
        return FredSeries(
            series_id=series_id,
            name=KR_SERIES_NAMES.get(series_id, DEFAULT_SERIES_NAMES.get(series_id, series_id)),
            source=last_url,
            observations=observations,
        )


def _default_end_month() -> str:
    # Avoid importing wall-clock date at module import; compute lazily.
    today = date.today()
    return f"{today.year}{today.month:02d}"


def _month_windows(start: str, end: str, size: int = 10):
    """Yield (YYYYMM, YYYYMM) windows of ``size`` months covering [start, end]."""
    cur = int(start[:4]) * 12 + int(start[4:6]) - 1
    last = int(end[:4]) * 12 + int(end[4:6]) - 1
    while cur <= last:
        hi = min(cur + size - 1, last)
        yield f"{cur // 12}{cur % 12 + 1:02d}", f"{hi // 12}{hi % 12 + 1:02d}"
        cur = hi + 1
