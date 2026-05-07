from __future__ import annotations

from data.models import MacroObservation, OptionSentimentRecord


def vix_from_macro(rows: list[MacroObservation], market: str = "US") -> list[OptionSentimentRecord]:
    return [
        OptionSentimentRecord(
            date=row.asof_date,
            market=market,
            vix=row.value,
            source=row.source or "fred:VIXCLS",
        )
        for row in rows
    ]
