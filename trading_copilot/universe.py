from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
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


class KindKoreaUniverseProvider:
    BASE_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"

    def __init__(self, fetch_text: Callable[[str], str] | None = None):
        self.fetch_text = fetch_text or default_fetch_text

    def members(
        self,
        market: str,
        include_etfs: bool = False,
        include_spacs: bool = False,
    ) -> tuple[UniverseMember, ...]:
        key = market.lower().strip()
        if key == "kospi":
            return tuple(
                parse_kind_corp_list(
                    self.fetch_text(kind_corp_list_url("stockMkt")),
                    market="KOSPI",
                    yahoo_suffix="KS",
                )
            )
        if key == "kosdaq":
            return tuple(
                parse_kind_corp_list(
                    self.fetch_text(kind_corp_list_url("kosdaqMkt")),
                    market="KOSDAQ",
                    yahoo_suffix="KQ",
                )
            )
        if key in {"kr", "korea"}:
            return tuple(
                dedupe_members(
                    [
                        *self.members("kospi", include_etfs=include_etfs, include_spacs=include_spacs),
                        *self.members("kosdaq", include_etfs=include_etfs, include_spacs=include_spacs),
                    ]
                )
            )
        raise ValueError("Only market='kospi', 'kosdaq', or 'kr' is supported")


class CompositeUniverseProvider:
    def __init__(
        self,
        us_provider: UniverseProvider | None = None,
        korea_provider: UniverseProvider | None = None,
    ):
        self.us_provider = us_provider or NasdaqTraderUniverseProvider()
        self.korea_provider = korea_provider or KindKoreaUniverseProvider()

    def members(
        self,
        market: str,
        include_etfs: bool = False,
        include_spacs: bool = False,
    ) -> tuple[UniverseMember, ...]:
        key = market.lower().strip()
        if key == "us":
            return self.us_provider.members(key, include_etfs=include_etfs, include_spacs=include_spacs)
        if key in {"kospi", "kosdaq", "kr", "korea"}:
            return self.korea_provider.members(key, include_etfs=include_etfs, include_spacs=include_spacs)
        raise ValueError("Supported markets: us, kospi, kosdaq, kr")


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


def kind_corp_list_url(market_type: str) -> str:
    return f"{KindKoreaUniverseProvider.BASE_URL}?method=download&marketType={market_type}"


def parse_kind_corp_list(text: str, market: str, yahoo_suffix: str) -> list[UniverseMember]:
    rows = html_table_rows(text)
    if not rows:
        return []
    headers = rows[0]
    members: list[UniverseMember] = []
    for values in rows[1:]:
        row = {header: values[index] if index < len(values) else "" for index, header in enumerate(headers)}
        name = normalize_whitespace(row.get("회사명", ""))
        code = normalize_whitespace(row.get("종목코드", ""))
        if not name or not re.fullmatch(r"\d{6}", code):
            continue
        members.append(
            UniverseMember(
                symbol=f"{code}.{yahoo_suffix}",
                name=name,
                market=market,
                source="kind.krx.co.kr corpList",
            )
        )
    return dedupe_members(members)


def html_table_rows(text: str) -> list[list[str]]:
    parser = SimpleTableParser()
    parser.feed(text)
    return parser.rows


class SimpleTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "tr":
            self._current_row = []
        elif tag.lower() in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"td", "th"} and self._current_row is not None and self._current_cell is not None:
            self._current_row.append(normalize_whitespace("".join(self._current_cell)))
            self._current_cell = None
        elif normalized == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None
            self._current_cell = None


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


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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
        encoding = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(encoding, errors="replace")
