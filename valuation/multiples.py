from __future__ import annotations

from statistics import median


def fair_value_from_pe(*, eps: float, peer_pe: list[float]) -> float:
    clean = [value for value in peer_pe if value > 0]
    if not clean:
        raise ValueError("peer_pe must contain at least one positive value")
    return eps * median(clean)


def fair_value_from_pb(*, book_value_per_share: float, peer_pb: list[float]) -> float:
    clean = [value for value in peer_pb if value > 0]
    if not clean:
        raise ValueError("peer_pb must contain at least one positive value")
    return book_value_per_share * median(clean)
