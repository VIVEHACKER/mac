"""마켓 히트맵 정적 HTML 렌더 — surgedesk.co.kr 홈의 에디토리얼 디자인 포팅.

서버(파이썬)에서 표를 전부 렌더한다. 브라우저 JS는 '히트맵을 최근 주로
스크롤'하는 스니펫 하나뿐이라 파일 하나로 어디서든 열린다.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from urllib.parse import urlencode

from engine.market_map.compute import (
    MACRO_SCALE,
    THEME_SCALE,
    MacroRow,
    ThemeRow,
    pct_pair,
)
from engine.market_map.panels import (
    RATE_REGION_LABELS,
    FlowPanel,
    ForecastPanel,
    OOSPanel,
    SelectionPanel,
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
tr.subrow{display:none}
tr.subrow.open{display:table-row}
tr.subrow th{padding-left:28px!important;font-weight:600;color:var(--soft);background:#FAFBFC}
th.has-subs{cursor:pointer}
th.has-subs .tri{color:var(--green);font-size:9px;margin-right:4px;display:inline-block;transition:transform .15s}
th.has-subs.open .tri{transform:rotate(90deg)}
table.panel{border-collapse:collapse;font-size:12.5px;width:100%;font-variant-numeric:tabular-nums}
table.panel th{color:var(--soft);font-weight:600;font-size:11px;text-align:right;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
table.panel td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
table.panel th:nth-child(2),table.panel td:nth-child(2){text-align:left}
table.panel tr.topn td{font-weight:700;background:#F6FBF8}
table.panel a{color:var(--ink);text-decoration:none;border-bottom:1px dashed var(--green)}
table.panel a:hover{color:var(--green)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:10.5px;font-weight:800;letter-spacing:.02em}
.badge.buy{background:#E3F5EB;color:#00794A}
.badge.hold{background:#EEF1F5;color:#5A6672}
.badge.avoid{background:#F6E9E7;color:#A44E30}
.badge.closed{background:#EEF1F5;color:#5A6672}
.badge.open-mtm{background:#FFF4E2;color:#8A6410}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}
.fcard{border:1px solid var(--line);border-radius:10px;padding:14px 16px;background:#fff;box-shadow:0 1px 3px rgba(14,20,32,.04)}
.fcard h3{font-size:14px;font-weight:800;margin-bottom:2px}
.fcard .sub{color:var(--soft);font-size:11.5px;margin-bottom:10px}
.fcard .foot{color:var(--soft);font-size:11px;margin-top:10px;border-top:1px dashed var(--line);padding-top:8px;line-height:1.55}
.pline{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:12px}
.pline .lbl{width:34px;color:var(--soft);font-weight:600}
.pline .bar{flex:1;height:8px;background:#F0F3F7;border-radius:4px;overflow:hidden}
.pline .bar i{display:block;height:100%;border-radius:4px}
.pline .val{width:44px;text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.pline.modal .val{color:var(--green-d)}
.numline{font-size:13px;margin:3px 0}
.numline b{font-variant-numeric:tabular-nums}
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
function toggleSubs(group){
  var head=document.getElementById("subhead-"+group);
  if(head)head.classList.toggle("open");
  document.querySelectorAll(".sub-"+group).forEach(function(tr){tr.classList.toggle("open")});
}
"""


def dashboard_deeplink(
    dashboard_url: str,
    *,
    ticker: str | None = None,
    market: str = "us",
    tab: str = "recommender",
) -> str:
    """대시보드 딥링크 — ?tab=recommender&ticker=NVDA&market=us (app.py 가 해석)."""
    params: dict[str, str] = {"tab": tab}
    if ticker:
        params["ticker"] = ticker
        params["market"] = market
    return f"{dashboard_url.rstrip('/')}/?{urlencode(params)}"


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


def _theme_row_cells(row: ThemeRow, partial_last: bool) -> str:
    tds: list[str] = []
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
    return "".join(tds)


def render_theme_table(
    rows: Sequence[ThemeRow],
    weeks: Sequence[date],
    partial_last: bool = False,
    sub_rows: Mapping[str, Sequence[ThemeRow]] | None = None,
    group_prefix: str = "g",
) -> str:
    """테마 히트맵. sub_rows 가 있으면 부모 행 클릭으로 하위산업 행을 펼친다."""
    body: list[str] = []
    for gi, row in enumerate(rows):
        name = html.escape(row.name)
        tickers = html.escape(row.tickers)
        subs = list(sub_rows.get(row.name, [])) if sub_rows else []
        group = f"{group_prefix}{gi}"
        if subs:
            head_th = (
                f'<th class="has-subs" id="subhead-{group}" onclick="toggleSubs(\'{group}\')" '
                f'title="{tickers} — 클릭하여 하위산업 펼치기">'
                f'<span class="tri">▶</span>{name} '
                f'<span style="color:#9a8b7a;font-weight:600">({row.n})</span></th>'
            )
        else:
            head_th = (
                f'<th title="{tickers}">{name} '
                f'<span style="color:#9a8b7a;font-weight:600">({row.n})</span></th>'
            )
        body.append(f"<tr>{head_th}{_theme_row_cells(row, partial_last)}</tr>")
        for sub in subs:
            sub_th = (
                f'<th title="{html.escape(sub.tickers)}">└ {html.escape(sub.name)} '
                f'<span style="color:#9a8b7a;font-weight:600">({sub.n})</span></th>'
            )
            body.append(
                f'<tr class="subrow sub-{group}">{sub_th}{_theme_row_cells(sub, partial_last)}</tr>'
            )
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


def _num(value: float | None, digits: int = 2, dash: str = "—") -> str:
    return f"{value:,.{digits}f}" if value is not None else dash


def render_selection_panel(panel: SelectionPanel, dashboard_url: str) -> str:
    """검증 선정 top-N 테이블 — 각 티커는 대시보드 추천기 딥링크."""
    action_cls = {"BUY": "buy", "HOLD": "hold", "AVOID": "avoid"}
    body: list[str] = []
    for row in panel.rows:
        link = dashboard_deeplink(dashboard_url, ticker=row.ticker, market="us")
        badge = action_cls.get(row.action, "hold")
        body.append(
            "<tr" + (' class="topn"' if row.in_top_n else "") + ">"
            f"<td>{row.rank if row.rank is not None else '—'}</td>"
            f'<td><a href="{html.escape(link)}">{html.escape(row.ticker)}</a></td>'
            f'<td><span class="badge {badge}">{html.escape(row.action)}</span></td>'
            f"<td>{html.escape(row.band)} {row.score:.0f}</td>"
            f"<td>{_num(row.price)}</td>"
            f"<td>{_num(row.target_entry)}</td>"
            f"<td>{_num(row.stop_loss)}</td>"
            f"<td>{_num(row.target_exit)}</td></tr>"
        )
    head = (
        "<tr><th>순위</th><th>티커</th><th>액션</th><th>신뢰도</th>"
        "<th>현재가</th><th>진입(변동성밴드)</th><th>손절</th><th>목표</th></tr>"
    )
    return f'<table class="panel"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'


def render_oos_panel(panel: OOSPanel) -> str:
    """forward-OOS 원장 — 리밸 회차별 포트 vs 벤치. 폐쇄/인터임을 구분 표기."""
    body: list[str] = []
    for row in panel.rows:
        status = (
            '<span class="badge closed">폐쇄</span>'
            if row.closed
            else '<span class="badge open-mtm">진행 (MTM)</span>'
        )
        mark = row.mark_date.isoformat() if row.mark_date else "—"
        exc = row.excess_pct
        exc_color = UP_COLOR if (exc or 0) >= 0 else DOWN_COLOR
        body.append(
            "<tr>"
            f"<td>{html.escape(row.rebal_date)}</td>"
            f"<td>{html.escape(mark)}</td>"
            f"<td>{row.n_names}</td>"
            f"<td>{_signed_str(row.port_pct) + '%' if row.port_pct is not None else '—'}</td>"
            f"<td>{_signed_str(row.bench_pct) + '%' if row.bench_pct is not None else '—'}</td>"
            f'<td style="color:{exc_color};font-weight:700">'
            f"{_signed_str(exc) + '%p' if exc is not None else '—'}</td>"
            f"<td>{status}</td></tr>"
        )
    head = (
        f"<tr><th>리밸일</th><th>마킹일</th><th>종목</th><th>포트</th>"
        f"<th>{html.escape(panel.benchmark)}</th><th>초과</th><th>상태</th></tr>"
    )
    if panel.n_closed:
        summary = (
            f"폐쇄 기간 n={panel.n_closed} 누적: 포트 {_signed_str(panel.cum_port_pct or 0.0)}% · "
            f"{html.escape(panel.benchmark)} {_signed_str(panel.cum_bench_pct or 0.0)}% · "
            f"초과 <b>{_signed_str(panel.cum_excess_pct or 0.0)}%p</b>"
        )
    else:
        summary = "폐쇄 기간 n=0 — 아직 통계적 판단 구간 아님 (첫 기간은 다음 리밸에 폐쇄)"
    return (
        f'<table class="panel"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'
        f'<div class="hint" style="margin:10px 0 0">{summary}</div>'
    )


def _won_eok(value: float) -> str:
    """원 → 억원 부호 표기 (십억 이상은 조원)."""
    eok = value / 1e8
    if abs(eok) >= 10000:
        return f"{'+' if eok >= 0 else '−'}{abs(eok) / 10000:,.2f}조"
    return f"{'+' if eok >= 0 else '−'}{abs(eok):,.0f}억"


def render_flow_panel(panel: FlowPanel) -> str:
    """KR 수급 — 대표주 외국인/기관 기간 순매수. naver 추정(medium) 배지 필수."""
    body: list[str] = []
    for row in panel.rows:
        f_color = UP_COLOR if row.foreign_net >= 0 else DOWN_COLOR
        i_color = UP_COLOR if row.institution_net >= 0 else DOWN_COLOR
        c_color = UP_COLOR if row.combined_net >= 0 else DOWN_COLOR
        body.append(
            "<tr>"
            f"<td>{html.escape(row.name)} "
            f'<span style="color:#9aa7b5">{html.escape(row.code)}</span></td>'
            f'<td style="color:{f_color};font-weight:700">{_won_eok(row.foreign_net)}</td>'
            f'<td style="color:{i_color};font-weight:700">{_won_eok(row.institution_net)}</td>'
            f'<td style="color:{c_color};font-weight:800">{_won_eok(row.combined_net)}</td></tr>'
        )
    head = "<tr><th>종목</th><th>외국인</th><th>기관</th><th>합계</th></tr>"
    return f'<table class="panel"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'


def render_forecast_panel(panel: ForecastPanel) -> str:
    """거시 예측 카드 — 금리 확률 바 + CPI/PPI nowcast, 사후채점 트랙레코드 병기."""
    cards: list[str] = []
    for rate in panel.rates:
        label = RATE_REGION_LABELS.get(rate.region, rate.region.upper())
        flag = "🇺🇸" if rate.region == "us" else "🇰🇷"
        lines: list[str] = []
        for key, korean in (("cut", "인하"), ("hold", "동결"), ("hike", "인상")):
            prob = rate.probs.get(key, 0.0)
            is_modal = key == rate.modal
            bar_color = "var(--green)" if is_modal else "#c3ccd6"
            lines.append(
                f'<div class="pline{" modal" if is_modal else ""}"><span class="lbl">{korean}</span>'
                f'<span class="bar"><i style="width:{prob * 100:.0f}%;background:{bar_color}"></i></span>'
                f'<span class="val">{prob * 100:.0f}%</span></div>'
            )
        track = (
            f"채점 n={rate.n_scored} · 적중 {rate.hit_rate * 100:.0f}% · "
            f"Brier {rate.mean_brier:.3f}"
            if rate.n_scored and rate.hit_rate is not None and rate.mean_brier is not None
            else "사후채점 기록 없음"
        )
        stale = "" if rate.pending else " · <b>지난 회의 기록</b>"
        cards.append(
            f'<div class="fcard"><h3>{flag} {html.escape(label)} {html.escape(rate.meeting)}</h3>'
            f'<div class="sub">현재 {_num(rate.current_rate)}% · 기록 {html.escape(rate.recorded_at)}{stale}</div>'
            f"{''.join(lines)}"
            f'<div class="foot">{track}</div></div>'
        )
    for macro in panel.macros:
        flag = "🇺🇸" if macro.region == "us" else "🇰🇷"
        pi = (
            f" (PI80 {_signed_str(macro.pi80[0])}~{_signed_str(macro.pi80[1])})"
            if macro.pi80
            else ""
        )
        track_parts: list[str] = [f"채점 n={macro.n_scored}"]
        if macro.mae is not None:
            track_parts.append(f"MAE {macro.mae:.2f}%p")
        if macro.pi80_coverage is not None:
            track_parts.append(f"PI80 적중 {macro.pi80_coverage * 100:.0f}%")
        yoy = (
            f'<div class="numline">YoY nowcast <b>{_signed_str(macro.forecast_yoy)}%</b></div>'
            if macro.forecast_yoy is not None
            else ""
        )
        skill = f" · skill {macro.skill_pct:.0f}%" if macro.skill_pct is not None else ""
        cards.append(
            f'<div class="fcard"><h3>{flag} {html.escape(macro.label)} · {html.escape(macro.target)}</h3>'
            f'<div class="sub">기록 {html.escape(macro.recorded_at)}{skill}'
            f"{'' if macro.pending else ' · <b>지난 발표 기록</b>'}</div>"
            f'<div class="numline">MoM nowcast <b>{_signed_str(macro.forecast_mom) if macro.forecast_mom is not None else "—"}%</b>{html.escape(pi)}</div>'
            f"{yoy}"
            f'<div class="foot">{" · ".join(track_parts) if macro.n_scored else "사후채점 기록 없음"}</div></div>'
        )
    return f'<div class="cards">{"".join(cards)}</div>'


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
    us_sub_rows: Mapping[str, Sequence[ThemeRow]] | None = None,
    selection: SelectionPanel | None = None,
    oos: OOSPanel | None = None,
    forecasts: ForecastPanel | None = None,
    flows: FlowPanel | None = None,
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
  <div class="sec-h"><h2>🇰🇷 국장 (KR) — 테마 주간 수익률</h2><span>· 주별 5거래일 · ETF 프록시 + 대표 개별종목</span></div>
  <div class="hint">각 셀: 그 주 테마 매핑 심볼(테마 ETF + 대표 개별종목)들의 평균
  5거래일 수익률(%). <b>따뜻한 색 = 상승(자금 유입)</b>, <b>차가운 색 = 하락(자금 유출)</b>.
  행 이름에 마우스를 올리면 심볼 리스트.{wtd_note}</div>
  <div class="card heat">{render_theme_table(kr_rows, weeks, partial_last)}</div>
</div></section>"""
        if kr_rows
        else ""
    )
    selection_section = ""
    if selection is not None and selection.rows:
        pbo_note = (
            f"방향은 robust, 크기는 fragile (PBO {selection.pbo:.2f}). "
            if selection.pbo is not None
            else "방향은 robust, 크기는 fragile. "
        )
        sel_hint = (
            f"walk-forward 검증 전략(<b>{html.escape(selection.strategy_id)}</b>)의 신호 재활용 "
            f"랭킹 — 예측이 아니라 랭킹. {pbo_note}"
            f"가격/펀더멘털 <b>핀 스냅샷 {selection.asof:%Y-%m-%d}</b> 기준, "
            f"유니버스 {selection.universe_size}종목. 강조 행 = 전략 보유 top-{selection.top_n}. "
            f"티커 클릭 → 대시보드 추천기에서 평가."
        )
        selection_section = f"""
<section id="selection"><div class="wrap">
  <div class="sec-h"><h2>검증 선정 — top {selection.top_n}</h2><span>· scan_universe · 핀 스냅샷</span></div>
  <div class="hint">{sel_hint}</div>
  <div class="card">{render_selection_panel(selection, dashboard_url)}</div>
</div></section>"""
    oos_section = ""
    if oos is not None and oos.rows:
        t0 = oos.rows[0].rebal_date
        bt_note = (
            f"백테스트 기대 초과 <b>{oos.backtest_excess_ann * 100:+.1f}%/yr</b>"
            f"(수수료 반영, validated_strategies.json) 대비 대조가 목적."
            if oos.backtest_excess_ann is not None
            else "백테스트 기대치와의 대조가 목적."
        )
        oos_hint = (
            f"사전등록 페이퍼 원장 (<b>{html.escape(oos.strategy_id)}</b>, T0 {html.escape(t0)}, "
            f"21영업일 리밸). 마크 = 조정종가, 포트 수익 = 마크된 심볼만 가중 재정규화. "
            f"<b>진행(MTM) 행은 폐쇄 전 노이즈</b> — 판단은 폐쇄 기간 누적으로. "
            f"{bt_note}"
        )
        oos_section = f"""
<section id="oos"><div class="wrap">
  <div class="sec-h"><h2>forward-OOS 원장 — 전략 vs {html.escape(oos.benchmark)}</h2><span>· n={oos.n_entries} 리밸 · 폐쇄 {oos.n_closed}</span></div>
  <div class="hint">{oos_hint}</div>
  <div class="card">{render_oos_panel(oos)}</div>
</div></section>"""
    forecast_section = ""
    if forecasts is not None and (forecasts.rates or forecasts.macros):
        forecast_section = f"""
<section id="forecast"><div class="wrap">
  <div class="sec-h"><h2>거시 예측 — 원장 사후채점 포함</h2><span>· trading-copilot 원장 (파일 읽기, 재계산 없음)</span></div>
  <div class="hint">기준금리 확률과 CPI/PPI nowcast 는 <b>기록 시점</b> 값이며, 트랙레코드(적중률·Brier·MAE·PI80)는
  발표 후 사후채점 원장에서 집계. 표본이 작을 때는 참고용.</div>
  {render_forecast_panel(forecasts)}
</div></section>"""
    flow_section = ""
    if flows is not None and flows.rows:
        flow_section = f"""
<section id="flows"><div class="wrap">
  <div class="sec-h"><h2>🇰🇷 수급 — 대표주 외국인·기관 순매수</h2><span>· 최근 {flows.lookback_days}거래일 · 테마 대장주</span></div>
  <div class="hint"><b>⚠ 추정치(격리)</b> — naver 종가×거래량 기반 <b>{html.escape(flows.confidence)}</b> 신뢰도.
  reported 값(KRX 크리덴셜)이 아니므로 정확한 금액이 아니라 <b>방향성</b> 참고용. 양수(빨강)=순매수, 음수(파랑)=순매도.</div>
  <div class="card">{render_flow_panel(flows)}</div>
</div></section>"""

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
      {'<a href="#selection">검증 선정</a>' if selection_section else ""}
      {'<a href="#oos">OOS 원장</a>' if oos_section else ""}
      {'<a href="#forecast">거시 예측</a>' if forecast_section else ""}
      {'<a href="#flows">수급</a>' if flow_section else ""}
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
  각 테마 옆 (n) = 데이터가 잡힌 매핑 종목 수, 행 이름에 마우스를 올리면 종목 리스트.
  <b>▶ 표시 테마는 클릭하면 하위산업으로 펼쳐진다.</b>{wtd_note}</div>
  <div class="card heat">{render_theme_table(us_rows, weeks, partial_last, sub_rows=us_sub_rows, group_prefix="us")}</div>
</div></section>
{kr_section}
{selection_section}
{oos_section}
{forecast_section}
{flow_section}
<footer><div class="wrap">
  <div>데이터: Yahoo Finance · FRED(공개 CSV) · 로컬 DuckDB 카탈로그 — 지연/무보증 데이터.</div>
  <div>정보 제공 목적으로 생성된 페이지이며 투자 조언이 아닙니다. 생성 {generated_at:%Y-%m-%d %H:%M:%S}.</div>
</div></footer>
<script>{_SCROLL_JS}</script>
</body>
</html>
"""
