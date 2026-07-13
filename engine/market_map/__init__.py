"""마켓 히트맵 (Surge Desk 스타일) — 거시 레짐 시계열 + 테마 자금흐름.

돈이 어디로 흐르는지 한 장으로 보여주는 정적 HTML 페이지를 생성한다.
compute(순수 계산) / themes(매핑) / fetch(외부 데이터) / render(HTML) / build(조립).
"""

from engine.market_map.build import build_market_map

__all__ = ["build_market_map"]
