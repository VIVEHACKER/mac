from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Protocol
from urllib.request import Request, urlopen


class UniverseProvider(Protocol):
    def members(
        self,
        market: str,
        include_etfs: bool = False,
        include_spacs: bool = False,
    ) -> tuple["UniverseMember", ...]:
        pass


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    name: str
    market: str
    source: str


class NasdaqTraderUniverseProvider:
    NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
    OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

    def __init__(self, fetch_text: Callable[[str], str] | None = None):
        self.fetch_text = fetch_text or default_fetch_text

    def members(
        self,
        market: str,
        include_etfs: bool = False,
        include_spacs: bool = False,
    ) -> tuple[UniverseMember, ...]:
        key = market.lower().strip()
        if key != "us":
            raise ValueError("Only market='us' is currently supported")

        members: list[UniverseMember] = []
        members.extend(parse_nasdaq_listed(self.fetch_text(self.NASDAQ_LISTED_URL), include_etfs, include_spacs))
        members.extend(parse_other_listed(self.fetch_text(self.OTHER_LISTED_URL), include_etfs, include_spacs))
        return tuple(dedupe_members(members))


def parse_nasdaq_listed(text: str, include_etfs: bool, include_spacs: bool) -> list[UniverseMember]:
    members: list[UniverseMember] = []
    for row in pipe_rows(text):
        if row.get("Test Issue") != "N":
            continue
        if row.get("ETF") == "Y" and not include_etfs:
            continue
        symbol = clean_symbol(row.get("Symbol", ""))
        name = row.get("Security Name", "").strip()
        if not symbol or is_structured_security(name) or (is_spac(name) and not include_spacs):
            continue
        members.append(
            UniverseMember(
                symbol=symbol,
                name=name,
                market="NASDAQ",
                source="nasdaqtrader",
            )
        )
    return members


def parse_other_listed(text: str, include_etfs: bool, include_spacs: bool) -> list[UniverseMember]:
    members: list[UniverseMember] = []
    for row in pipe_rows(text):
        if row.get("Test Issue") != "N":
            continue
        if row.get("ETF") == "Y" and not include_etfs:
            continue
        symbol = clean_symbol(row.get("ACT Symbol", ""))
        name = row.get("Security Name", "").strip()
        if not symbol or is_structured_security(name) or (is_spac(name) and not include_spacs):
            continue
        members.append(
            UniverseMember(
                symbol=symbol,
                name=name,
                market=exchange_name(row.get("Exchange", "")),
                source="nasdaqtrader",
            )
        )
    return members


def pipe_rows(text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    headers = lines[0].split("|")
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        values = line.split("|")
        rows.append({header: values[index] if index < len(values) else "" for index, header in enumerate(headers)})
    return rows


def clean_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def exchange_name(code: str) -> str:
    return {
        "A": "NYSEAMERICAN",
        "N": "NYSE",
        "P": "NYSEARCA",
        "Z": "BATS",
        "V": "IEX",
    }.get(code.strip().upper(), code.strip().upper() or "OTHER")


_STRUCTURED_TOKENS = frozenset(
    {
        "unit",
        "units",
        "right",
        "rights",
        "warrant",
        "warrants",
        "preferred",
        "preference",
    }
)


def is_structured_security(name: str) -> bool:
    tokens = re.findall(r"[A-Za-z']+", name.lower())
    return any(token in _STRUCTURED_TOKENS for token in tokens)


def is_spac(name: str) -> bool:
    normalized = name.lower()
    return (
        "acquisition corp" in normalized
        or "acquisition inc" in normalized
        or "acquisition company" in normalized
        or "blank check" in normalized
    )


def dedupe_members(members: list[UniverseMember]) -> list[UniverseMember]:
    seen: set[str] = set()
    result: list[UniverseMember] = []
    for member in members:
        if member.symbol in seen:
            continue
        seen.add(member.symbol)
        result.append(member)
    return result


def default_fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")
