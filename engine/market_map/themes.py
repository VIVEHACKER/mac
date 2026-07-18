"""마켓 히트맵 매핑 — 매크로 지표 스펙, 티커 칩, US 슈퍼테마, KR 테마 ETF 프록시.

매핑은 정적 선언이고, 실제 행 구성은 런타임에 '데이터가 잡힌 심볼'과 교집합으로
결정된다 (상장폐지/수집 실패 심볼은 자연 탈락, n 카운트에서 제외).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MacroSpec:
    symbol: str
    name: str
    direction: int  # +1: 상승=risk_on, -1: 상승=risk_off
    source: str  # "yfinance" | "fred" | "catalog"
    min_base: float = 1e-9  # 4주Δ 분모 하한 (0 근처 스프레드 보호)


# 레퍼런스와 동일한 지표 구성. 수익률곡선/HY스프레드는 FRED 공개 CSV(키 불필요).
MACRO_SPECS: list[MacroSpec] = [
    MacroSpec("^KS11", "KOSPI", +1, "yfinance"),
    MacroSpec("^VIX", "VIX (변동성)", -1, "yfinance"),
    MacroSpec("DX-Y.NYB", "DXY (달러)", -1, "yfinance"),
    MacroSpec("EWY", "EWY (한국 ETF)", +1, "yfinance"),
    MacroSpec("HYG", "HYG (하이일드)", +1, "yfinance"),
    MacroSpec("TLT", "TLT (장기국채)", -1, "catalog"),
    MacroSpec("^TNX", "10Y 금리", -1, "yfinance"),
    MacroSpec("T10Y2Y", "수익률곡선", +1, "fred", min_base=0.05),
    MacroSpec("BAMLH0A0HYM2", "HY 스프레드", -1, "fred"),
]

# 섹터 로테이션 (Cyclicals 5거래일 수익률 − Defensives) — SPDR 섹터 ETF
SECTOR_CYCLICALS = ["XLY", "XLI", "XLF", "XLB"]
SECTOR_DEFENSIVES = ["XLP", "XLU", "XLV"]

# 상단 티커 칩 (label, yfinance symbol)
TICKER_CHIPS: list[tuple[str, str]] = [
    ("코스피", "^KS11"),
    ("코스닥", "^KQ11"),
    ("S&P500", "^GSPC"),
    ("나스닥", "^IXIC"),
    ("다우", "^DJI"),
    ("VIX", "^VIX"),
    ("美10Y", "^TNX"),
    ("달러인덱스", "DX-Y.NYB"),
    ("원/달러", "KRW=X"),
    ("비트코인", "BTC-USD"),
]


@dataclass(frozen=True)
class ThemeSpec:
    name: str  # 이모지 포함 표시명
    symbols: list[str] = field(default_factory=list)


# 🇺🇸 US 슈퍼테마 — 로컬 카탈로그 유니버스(SP100 PIT) 심볼을 테마로 묶는다.
# 한 심볼은 한 테마에만 속한다. 유니버스에 없는 심볼은 런타임에 자연 탈락.
US_THEMES: list[ThemeSpec] = [
    ThemeSpec(
        "🤖 AI/빅테크/반도체",
        [
            "NVDA",
            "AMD",
            "AVGO",
            "QCOM",
            "TXN",
            "INTC",
            "MU",
            "AMAT",
            "LRCX",
            "MSFT",
            "GOOGL",  # GOOG(동일 기업 이중 클래스)는 제외 — 단순평균 2배 가중 방지
            "META",
            "AAPL",
            "AMZN",
            "ORCL",
            "CRM",
            "NOW",
            "ADBE",
            "IBM",
            "INTU",
            "PLTR",
            "CSCO",
            "ACN",
        ],
    ),
    ThemeSpec("🛡 방산/우주", ["LMT", "RTX", "GD", "BA"]),
    ThemeSpec("🛢 에너지", ["XOM", "CVX", "COP"]),
    ThemeSpec(
        "💵 금융/금리",
        [
            "JPM",
            "BAC",
            "C",
            "GS",
            "MS",
            "WFC",
            "USB",
            "COF",
            "SCHW",
            "BLK",
            "AXP",
            "BNY",
            "BRK-B",
            "V",
            "MA",
        ],
    ),
    ThemeSpec(
        "💊 바이오/헬스",
        [
            "ABBV",
            "ABT",
            "AMGN",
            "BMY",
            "GILD",
            "JNJ",
            "LLY",
            "MRK",
            "PFE",
            "TMO",
            "DHR",
            "MDT",
            "ISRG",
            "UNH",
            "CVS",
            "AET",
            "ESRX",
        ],
    ),
    ThemeSpec(
        "🛒 유통/소비재",
        [
            "WMT",
            "COST",
            "HD",
            "LOW",
            "PG",
            "KO",
            "PEP",
            "MDLZ",
            "CL",
            "MO",
            "PM",
            "MCD",
            "SBUX",
            "NKE",
            "BKNG",
        ],
    ),
    ThemeSpec("📡 통신/미디어", ["T", "VZ", "TMUS", "CMCSA", "DIS", "NFLX"]),
    ThemeSpec("🚗 모빌리티/EV", ["TSLA", "GM", "UBER"]),
    ThemeSpec(
        "🏭 산업재/기계",
        ["CAT", "DE", "MMM", "GE", "HON", "EMR", "UNP", "UPS", "FDX", "LIN"],
    ),
    ThemeSpec("⚡ 전력/유틸리티", ["NEE", "DUK", "SO", "GEV"]),
    ThemeSpec("🏠 부동산/리츠", ["SPG", "AMT"]),
]

# 🇰🇷 KR 테마 — 테마 ETF 프록시 + 대표 개별 종목(대형주 위주) 평균.
# yfinance 코드가 죽어 있으면 그 심볼/행은 자연 탈락한다. KOSDAQ 은 .KQ.
KR_THEMES: list[ThemeSpec] = [
    ThemeSpec("🤖 반도체/AI", ["091160.KS", "005930.KS", "000660.KS", "042700.KS"]),
    ThemeSpec(
        "🔋 2차전지/EV",
        ["305720.KS", "373220.KS", "006400.KS", "247540.KQ", "051910.KS"],
    ),
    ThemeSpec("💊 바이오", ["244580.KS", "207940.KS", "068270.KS", "000100.KS"]),
    ThemeSpec("🚗 자동차", ["091180.KS", "005380.KS", "000270.KS", "012330.KS"]),
    ThemeSpec("🏦 은행/금융", ["091170.KS", "105560.KS", "055550.KS", "086790.KS"]),
    ThemeSpec("⚡ 에너지/화학", ["117460.KS", "010950.KS", "011170.KS"]),
    ThemeSpec("🏗 철강/소재", ["117680.KS", "005490.KS", "004020.KS"]),
    ThemeSpec("🎮 게임", ["300950.KS", "259960.KS", "036570.KS", "251270.KS"]),
    ThemeSpec("🛡 방산/우주", ["449450.KS", "012450.KS", "079550.KS", "064350.KS"]),
    ThemeSpec("🎬 미디어/엔터", ["266360.KS", "352820.KS", "035900.KQ", "041510.KQ"]),
    ThemeSpec("🚢 조선", ["466920.KS", "009540.KS", "042660.KS", "010140.KS"]),
    ThemeSpec("🌐 인터넷/플랫폼", ["035420.KS", "035720.KS"]),
]

# 하위 호환 별칭 — ETF 단독 프록시 시절 이름 (build/테스트의 기존 import 유지)
KR_THEME_ETFS = KR_THEMES

# 🇺🇸 US 하위산업 드릴다운 — 큰 슈퍼테마를 하위 그룹으로 쪼갠다.
# 키 = US_THEMES 의 표시명, 값의 심볼은 반드시 부모 테마 심볼의 부분집합.
US_SUBTHEMES: dict[str, list[ThemeSpec]] = {
    "🤖 AI/빅테크/반도체": [
        ThemeSpec(
            "반도체/장비",
            ["NVDA", "AMD", "AVGO", "QCOM", "TXN", "INTC", "MU", "AMAT", "LRCX"],
        ),
        ThemeSpec(
            "소프트웨어/클라우드",
            ["MSFT", "ORCL", "CRM", "NOW", "ADBE", "IBM", "INTU", "PLTR", "CSCO", "ACN"],
        ),
        ThemeSpec("플랫폼/커머스", ["GOOGL", "META", "AAPL", "AMZN"]),
    ],
    "💵 금융/금리": [
        ThemeSpec("은행", ["JPM", "BAC", "C", "WFC", "USB"]),
        ThemeSpec("IB/자산운용", ["GS", "MS", "SCHW", "BLK", "BNY", "COF", "AXP", "BRK-B"]),
        ThemeSpec("결제 네트워크", ["V", "MA"]),
    ],
    "💊 바이오/헬스": [
        ThemeSpec("제약", ["ABBV", "AMGN", "BMY", "GILD", "JNJ", "LLY", "MRK", "PFE"]),
        ThemeSpec("기기/진단", ["ABT", "TMO", "DHR", "MDT", "ISRG"]),
        ThemeSpec("보험/헬스서비스", ["UNH", "CVS", "AET", "ESRX"]),
    ],
    "🛒 유통/소비재": [
        ThemeSpec("필수소비", ["PG", "KO", "PEP", "MDLZ", "CL", "MO", "PM"]),
        ThemeSpec("리테일", ["WMT", "COST", "HD", "LOW"]),
        ThemeSpec("브랜드/외식", ["MCD", "SBUX", "NKE", "BKNG"]),
    ],
}


def yfinance_symbols_needed(offline: bool = False) -> list[str]:
    """yfinance 로 받아야 하는 전체 심볼 (중복 제거, 선언 순서 유지)."""
    if offline:
        return []
    symbols: list[str] = []
    for spec in MACRO_SPECS:
        if spec.source == "yfinance":
            symbols.append(spec.symbol)
    symbols.extend(SECTOR_CYCLICALS)
    symbols.extend(SECTOR_DEFENSIVES)
    symbols.extend(label_symbol[1] for label_symbol in TICKER_CHIPS)
    for theme in KR_THEME_ETFS:
        symbols.extend(theme.symbols)
    seen: set[str] = set()
    unique: list[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique
