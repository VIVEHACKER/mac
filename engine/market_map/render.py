"""마켓 히트맵 정적 HTML 렌더 — surgedesk.co.kr 홈의 에디토리얼 디자인 포팅.

서버(파이썬)에서 표를 전부 렌더한다. 브라우저 JS는 '히트맵을 최근 주로
스크롤'하는 스니펫 하나뿐이라 파일 하나로 어디서든 열린다.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date, datetime

from engine.market_map.compute import (
    MACRO_SCALE,
    THEME_SCALE,
    MacroRow,
    ThemeRow,
    pct_pair,
)

UP_COLOR = "#D92F2F"  # 상승 = 빨강 (국내 관행)
DOWN_COLOR = "#2563EB"

_CSS = """
:root{--bg:#fff;--soft-bg:#F0F3F7;--ink:#0E1420;--soft:#5A6672;--line:#DDE3EA;--green:#00B357;--green-d:#009648;--up:#D92F2F;--dn:#2563EB}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:'Pretendard Variable',-apple-system,'Helvetica Neue',sans-serif;line-height:1.5;word-break:keep-all;-webkit-font-smoothing:antialiased}
.wrap{max-width:1280px;margin:0 auto;padding:0 24px}
header{border-bottom:1px solid var(--line);background:#fff;position:sticky;top:0;z-index:50}
.nav{display:flex;align-items:center;justify-content:space-between;height:62px}
.logo{font-weight:900;font-size:22px;letter-spacing:-.02em}.logo em{color:var(--green);font-style:normal}
.nav-links a{margin-left:26px;color:var(--ink);text-decoration:none;font-size:14px;font-weight:600}
.nav-links a:hover{color:var(--green)}
.nav-links a.active{color:#0E1420;font-weight:800;border-bottom:2px solid var(--green)}
.cta{background:var(--green);color:#fff!important;padding:11px 22px;border-radius:999px;font-weight:800;font-size:14px;box-shadow:0 1px 0 var(--green-d);margin-left:26px}
.cta:hover{background:var(--green-d)}
#home-ticker{background:#0E1420;height:30px;overflow:hidden;display:flex;align-items:center;border-bottom:1px solid #1b2431}
#home-ticker .tk-marquee{display:flex;animation:tkscroll 55s linear infinite;will-change:transform}
#home-ticker:hover .tk-marquee{animation-play-state:paused}
#home-ticker .tk-set{display:flex;gap:22px;padding-right:22px;white-space:nowrap;font-family:'JetBrains Mono',monospace;flex:0 0 auto}
#home-ticker .tk{font-size:12px;color:#C7CDD6}#home-ticker .tk b{color:#F3F5F8;font-weight:700;margin-right:3px}
@keyframes tkscroll{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.hero{padding:66px 0 40px;text-align:center}
.hero h1{font-weight:900;letter-spacing:-.03em;font-size:clamp(34px,5vw,60px);line-height:1.05}
.hero h1 .g{color:var(--green)}
.hero p{color:var(--soft);font-size:18px;max-width:56ch;margin:22px auto 0}
.hero .micro{margin-top:12px;font-size:13px;color:var(--soft)}
section{padding:44px 0}
.sec-h{display:flex;align-items:baseline;gap:12px;margin-bottom:16px}
.sec-h h2{font-weight:900;letter-spacing:-.02em;font-size:24px}
.sec-h span{color:var(--soft);font-size:13px}
.hint{color:var(--soft);font-size:13px;margin:-6px 0 14px;max-width:90ch;line-height:1.6}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px 18px;overflow:auto;box-shadow:0 1px 3px rgba(14,20,32,.04);scrollbar-width:thin}
.card::-webkit-scrollbar{height:9px}
.card::-webkit-scrollbar-thumb{background:#c3ccd6;border-radius:5px}
.card::-webkit-scrollbar-track{background:#f0f3f7}
.card.heat{padding:0;overflow-x:auto}
.card.heat table.hm tbody th,.card.heat table.hm thead th:first-child{padding-left:14px}
table.hm{border-collapse:separate;border-spacing:0;font-size:11px;width:100%}
table.hm tbody th{position:sticky;left:0;background:#fff;text-align:left;padding:3px 10px 3px 8px;white-space:nowrap;font-weight:700;z-index:5;box-shadow:1px 0 0 var(--line)}
table.hm thead th{color:var(--soft);font-weight:600;text-align:center;font-size:9px;padding:3px 4px;background:#fff}
table.hm thead th:first-child{position:sticky;left:0;z-index:6;text-align:left;box-shadow:1px 0 0 var(--line)}
table.hm td{width:26px;height:22px;text-align:center;color:#fff;font-variant-numeric:tabular-nums;font-size:9px;border-bottom:1px solid #fff}
table.hm.ts{width:max-content;min-width:100%}
table.hm.ts td{width:34px;min-width:34px;height:24px;font-size:10px}
table.hm.ts thead th{min-width:34px}
table.hm.ts tbody th,table.hm.ts thead th:first-child{min-width:180px;width:180px}
.avgcol{font-weight:800;border-left:2px solid var(--line)}
.kick2{font-size:11px;letter-spacing:.2em;color:var(--green);font-weight:800;margin-bottom:10px}
.desk-copy{max-width:760px;margin:0 auto}
.desk-copy h2{font-weight:900;letter-spacing:-.02em;font-size:clamp(24px,3.2vw,36px);line-height:1.12}
.desk-copy h2 .g{color:var(--green)}
.desk-copy>p{color:var(--soft);font-size:16px;margin:16px 0 18px;max-width:46ch}
.desk-feats{list-style:none;margin:0 0 22px}
.desk-feats li{position:relative;padding:8px 0 8px 24px;border-top:1px dashed var(--line);font-size:14.5px}
.desk-feats li::before{content:"→";position:absolute;left:0;color:var(--green);font-weight:800}
.desk-feats b{font-weight:800}
.btn2{display:inline-block;background:var(--green);color:#fff;text-decoration:none;font-weight:800;padding:13px 28px;border-radius:999px;box-shadow:0 2px 0 var(--green-d)}
.btn2:hover{background:var(--green-d)}
.band{background:var(--soft-bg);padding:30px 0;text-align:center;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.band h2{font-weight:900;letter-spacing:-.02em;font-size:clamp(22px,3vw,32px)}.band h2 .g{color:var(--green)}
footer{border-top:1px solid var(--line);color:var(--soft);font-size:12px;padding:30px 0 54px;margin-top:12px;background:var(--soft-bg)}
footer .wrap>div{margin-top:4px}
@media(max-width:820px){
.nav{height:56px}.logo{font-size:19px;flex:0 0 auto}
.nav-links{display:flex;align-items:center;flex:1;min-width:0;margin-left:14px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;white-space:nowrap}
.nav-links::-webkit-scrollbar{display:none}
.nav-links a{margin-left:0;margin-right:15px;font-size:13px;flex:0 0 auto}
.nav-links a.cta{margin-left:6px;margin-right:0;padding:8px 14px;font-size:13px}}
"""

_SCROLL_JS = """
function scrollHeatsRight(){
  document.querySelectorAll(".card.heat").forEach(function(card){
    if(card.scrollWidth>card.clientWidth)card.scrollLeft=card.scrollWidth;
  });
}
[0,100,400,900].forEach(function(t){setTimeout(scrollHeatsRight,t)});
requestAnimationFrame(function(){requestAnimationFrame(scrollHeatsRight)});
addEventListener("load",scrollHeatsRight);
addEventListener("resize",scrollHeatsRight);
"""

EMPTY_TD = '<td style="background:#FBF7EE;color:#c3ccd6">·</td>'


def _signed_str(value: float, digits: int = 1) -> str:
    return f"{'+' if value >= 0 else ''}{value:.{digits}f}"


def _chip_value(value: float) -> str:
    if value < 1000:
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def render_ticker_chips(chips: Sequence[tuple[str, float, float]]) -> str:
    """칩 = (label, 현재가, 전일가). 전일 대비 %, 상승 빨강/하락 파랑."""
    parts: list[str] = []
    for label, cur, prev in chips:
        pct = (cur / prev - 1.0) * 100.0 if prev else 0.0
        color = UP_COLOR if pct >= 0 else DOWN_COLOR
        parts.append(
            f'<span class="tk"><b>{html.escape(label)}</b> {_chip_value(cur)} '
            f'<span style="color:{color}">{_signed_str(pct, 2)}%</span></span>'
        )
    return "".join(parts)


def _thead(weeks: Sequence[date], first_col: str, partial_last: bool = False) -> str:
    head = [f"<th>{html.escape(first_col)}</th>"]
    for i, monday in enumerate(weeks):
        mark = "*" if partial_last and i == len(weeks) - 1 else ""
        head.append(
            f"<th><div>W{i + 1}{mark}</div>"
            f'<div style="font-weight:400;color:#9aa7b5">{monday:%m/%d}</div></th>'
        )
    head.append('<th class="avgcol">평균</th>')
    return f"<thead><tr>{''.join(head)}</tr></thead>"


def render_macro_table(
    rows: Sequence[MacroRow], weeks: Sequence[date], partial_last: bool = False
) -> str:
    body: list[str] = []
    for row in rows:
        tds: list[str] = [f"<th>{html.escape(row.name)}</th>"]
        for cell in row.cells:
            if cell is None:
                tds.append(EMPTY_TD)
                continue
            bg, fg = pct_pair(cell.signed, MACRO_SCALE)
            title = f"{row.name} · 4주Δ {_signed_str(cell.pct)}%"
            tds.append(
                f'<td style="background:{bg};color:{fg};font-weight:700" '
                f'title="{html.escape(title)}">{_signed_str(cell.pct)}</td>'
            )
        avg_pct, avg_signed = row.avg_pct, row.avg_signed
        if avg_pct is None or avg_signed is None:
            tds.append('<td class="avgcol" style="background:#fff;color:#5A6672">—</td>')
        else:
            bg, fg = pct_pair(avg_signed, MACRO_SCALE)
            tds.append(
                f'<td class="avgcol" style="background:{bg};color:{fg};font-weight:800">'
                f"{_signed_str(avg_pct)}</td>"
            )
        body.append(f"<tr>{''.join(tds)}</tr>")
    return (
        f'<table class="hm ts">{_thead(weeks, "지표", partial_last)}'
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def render_theme_table(
    rows: Sequence[ThemeRow], weeks: Sequence[date], partial_last: bool = False
) -> str:
    body: list[str] = []
    for row in rows:
        name = html.escape(row.name)
        tickers = html.escape(row.tickers)
        tds: list[str] = [
            f'<th title="{tickers}">{name} '
            f'<span style="color:#9a8b7a;font-weight:600">({row.n})</span></th>'
        ]
        for i, value in enumerate(row.series):
            if value is None:
                tds.append(EMPTY_TD)
                continue
            bg, fg = pct_pair(value, THEME_SCALE)
            wtd = partial_last and i == len(row.series) - 1
            title = f"{'주중(WTD)' if wtd else '평균 5d'} {_signed_str(value)}%"
            tds.append(
                f'<td style="background:{bg};color:{fg};font-weight:700" '
                f'title="{html.escape(title)}">{_signed_str(value)}</td>'
            )
        avg = row.avg
        if avg is None:
            tds.append('<td class="avgcol" style="background:#fff;color:#5A6672">—</td>')
        else:
            bg, fg = pct_pair(avg, THEME_SCALE)
            tds.append(
                f'<td class="avgcol" style="background:{bg};color:{fg};font-weight:800">'
                f"{_signed_str(avg)}</td>"
            )
        body.append(f"<tr>{''.join(tds)}</tr>")
    return (
        f'<table class="hm ts">{_thead(weeks, "슈퍼테마", partial_last)}'
        f"<tbody>{''.join(body)}</tbody></table>"
    )


_DESK_FEATURES = [
    ("<b>검증 선정</b> — scan_universe 모멘텀 랭크 top-7, PBO/CSCV·effN 신뢰도 게이트"),
    ("<b>차트 리딩</b> — FVG·오더블록·매물대·와이코프 컨플루언스 (ADVISORY)"),
    ("<b>forward-OOS 원장</b> — 배포후보 페이퍼 트래킹, 21영업일 리밸 케이던스"),
    ("<b>거시 예측</b> — FOMC/금통위 확률 + CPI/PPI nowcast, 원장 사후채점"),
]


def render_page(
    *,
    as_of: date,
    generated_at: datetime,
    chips: Sequence[tuple[str, float, float]],
    weeks: Sequence[date],
    macro_rows: Sequence[MacroRow],
    us_rows: Sequence[ThemeRow],
    kr_rows: Sequence[ThemeRow],
    dashboard_url: str = "http://localhost:8501",
    partial_last: bool = False,
    catalog_as_of: date | None = None,
) -> str:
    chip_html = render_ticker_chips(chips)
    ticker = (
        f'<div id="home-ticker"><div class="tk-marquee">'
        f'<div class="tk-set">{chip_html}</div><div class="tk-set">{chip_html}</div>'
        f"</div></div>"
        if chips
        else ""
    )
    first_week = weeks[0] if weeks else as_of
    feats = "".join(f"<li>{f}</li>" for f in _DESK_FEATURES)
    wtd_note = " 마지막 주(*)는 진행 중(WTD) — 5거래일 미만." if partial_last else ""
    kr_section = (
        f"""
<section id="kr-themes"><div class="wrap">
  <div class="sec-h"><h2>🇰🇷 국장 (KR) — 테마 ETF 주간 수익률</h2><span>· 주별 5거래일</span></div>
  <div class="hint">개별 종목 수집 전까지 대표 <b>테마 ETF 프록시</b> 기준. 각 셀: 그 주 ETF의
  5거래일 수익률(%). <b>따뜻한 색 = 상승(자금 유입)</b>, <b>차가운 색 = 하락(자금 유출)</b>.{wtd_note}</div>
  <div class="card heat">{render_theme_table(kr_rows, weeks, partial_last)}</div>
</div></section>"""
        if kr_rows
        else ""
    )

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>재무 Desk — 마켓 히트맵</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap">
<style>{_CSS}</style>
</head>
<body>
<header><div class="wrap">
  <nav class="nav">
    <div class="logo">재무<em>Desk</em></div>
    <div class="nav-links">
      <a href="#" class="active">마켓</a>
      <a href="#macro">거시 레짐</a>
      <a href="#us-themes">테마 흐름</a>
      <a href="{html.escape(dashboard_url)}" class="cta">대시보드 열기</a>
    </div>
  </nav>
</div></header>
{ticker}
<div class="hero"><div class="wrap">
  <h1>돈이 어디로 흐르는지<br><span class="g">한 장으로.</span></h1>
  <p>거시 레짐 신호부터 테마·종목까지 — 로컬 카탈로그와 공개 데이터로 그리는 마켓 맵.</p>
  <div class="micro">기준일 {as_of.isoformat()} · 생성 {generated_at:%Y-%m-%d %H:%M}</div>
</div></div>
<div class="band"><div class="wrap"><h2>테마에서 종목으로. <span class="g">Trader Desk.</span></h2></div></div>
<section><div class="wrap"><div class="desk-copy">
  <div class="kick2">TRADER DESK</div>
  <h2>강한 테마 안에서 <span class="g">주도주</span>를 골라냅니다.</h2>
  <p>히트맵에서 달아오른 테마를 확인하면 바로 대시보드로. 검증된 모멘텀 랭크와
  차트 리딩, forward-OOS 원장을 한 화면에서 봅니다.</p>
  <ul class="desk-feats">{feats}</ul>
  <a class="btn2" href="{html.escape(dashboard_url)}">Trader 대시보드 열기 →</a>
</div></div></section>
<section id="macro"><div class="wrap">
  <div class="sec-h"><h2>거시 레짐부터. 시계열 히트맵.</h2><span>· {first_week:%Y-%m} 부터 주별 흐름</span></div>
  <div class="hint">각 셀: 4주 전 대비 % 변화 (해당 신호가 risk-on 으로 작용하면 <b>따뜻한 색</b>,
  risk-off 면 <b>차가운 색</b>). 섹터 로테이션 = Cyclicals 5거래일 수익률 − Defensives 수익률
  (양수 = risk-on 자금 회전). 수익률곡선·HY 스프레드는 FRED 공개 시계열.{wtd_note}</div>
  <div class="card heat">{render_macro_table(macro_rows, weeks, partial_last)}</div>
</div></section>
<section id="us-themes"><div class="wrap">
  <div class="sec-h"><h2>🇺🇸 미장 (US) — 테마별 종목 평균 수익률</h2><span>· 주별 5거래일 · 로컬 카탈로그{f" (최신 종가 {catalog_as_of.isoformat()} — 이후 주는 빈 칸, `trader ingest` 로 갱신)" if catalog_as_of else ""}</span></div>
  <div class="hint">각 셀: 그 주 해당 슈퍼테마 매핑 종목들의 평균 5거래일 수익률(%).
  <b>따뜻한 색 = 상승(자금 유입)</b>, <b>차가운 색 = 하락(자금 유출)</b>.
  각 테마 옆 (n) = 데이터가 잡힌 매핑 종목 수, 행 이름에 마우스를 올리면 종목 리스트.{wtd_note}</div>
  <div class="card heat">{render_theme_table(us_rows, weeks, partial_last)}</div>
</div></section>
{kr_section}
<footer><div class="wrap">
  <div>데이터: Yahoo Finance · FRED(공개 CSV) · 로컬 DuckDB 카탈로그 — 지연/무보증 데이터.</div>
  <div>정보 제공 목적으로 생성된 페이지이며 투자 조언이 아닙니다. 생성 {generated_at:%Y-%m-%d %H:%M:%S}.</div>
</div></footer>
<script>{_SCROLL_JS}</script>
</body>
</html>
"""
