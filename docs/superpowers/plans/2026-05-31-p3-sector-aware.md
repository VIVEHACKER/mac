# P3: Sector-Aware Scoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the compounder scan sector-aware — classify each ticker by SIC, and for financials (SIC 6000–6799) exclude FCF-based metrics from scoring so banks/REITs are judged on ROIC/growth/P-B instead of inflated FCF artifacts.

**Architecture:** A new `fetch_sectors.py` writes a `{symbol,sic,sector}` CSV from the SEC submissions API. `engine/compounder.py` gains an optional `sectors` map that nulls invalid metrics for financial names before cross-sectional Z-scoring (backward-compatible: `sectors=None` = current behavior). The CLI loads the sector map and threads it through; the dossier shows the sector and flags FCF exclusion. No weight changes, no new archetype.

**Tech Stack:** Python 3.12 stdlib (`csv`, `urllib`, `argparse`), existing `data.*` / `engine.compounder*` / `trader.cli`, pytest. Reuses `sec_edgar_ingest.load_cik_map`.

Spec: `docs/superpowers/specs/2026-05-31-p3-sector-aware-design.md`.

---

## File Structure

- Create `scripts/fetch_sectors.py` — `sic_to_sector()` (pure) + `main()` (SIC fetch → sectors CSV).
- Modify `engine/compounder.py` — `SECTOR_INVALID_METRICS` const; `score_archetypes(universe, sectors=None)`; `rank_compounders(universe, top_n, sectors=None)`.
- Modify `engine/compounder_dossier.py` — `Dossier.sector` field; `build_dossier(candidate, sector="unknown")`; markdown sector line + FCF-excluded note.
- Modify `trader/cli.py` — `compounder-scan --sectors-csv`; load `{symbol: sector}`; pass to `rank_compounders` + `build_dossier`.
- Create `tests/test_scripts/test_fetch_sectors.py`
- Modify `tests/test_engine/test_compounder.py`, `tests/test_engine/test_compounder_dossier.py`, `tests/test_trader_cli.py`

Run: `.venv/bin/python -m pytest <path> -q`; `.venv/bin/ruff check <file>`; `.venv/bin/mypy <file>`.

---

## Task 1: SIC → sector classifier + fetch script

**Files:**
- Create: `scripts/fetch_sectors.py`
- Test: `tests/test_scripts/test_fetch_sectors.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from scripts.fetch_sectors import sic_to_sector


def test_financials_range():
    assert sic_to_sector(6798) == "financials"  # REIT
    assert sic_to_sector(6021) == "financials"  # bank
    assert sic_to_sector(6411) == "financials"  # insurance
    assert sic_to_sector(6000) == "financials"
    assert sic_to_sector(6799) == "financials"


def test_non_financials():
    assert sic_to_sector(7372) == "tech"         # software
    assert sic_to_sector(2834) == "healthcare"   # pharma
    assert sic_to_sector(3721) == "industrials"  # aircraft
    assert sic_to_sector(5999) == "consumer"     # not financials (boundary just below)
    assert sic_to_sector(6800) == "other"        # just above financials range


def test_none_is_other():
    assert sic_to_sector(None) == "other"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_scripts/test_fetch_sectors.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.fetch_sectors'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Classify universe tickers by SIC (SEC submissions API) into coarse sectors.

Only `financials` (SIC 6000-6799) changes scoring behavior (FCF metrics are
excluded for financials in engine.compounder); other groups are best-effort
display labels. Pure `sic_to_sector` is unit-tested; network fetch is in main().
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def sic_to_sector(sic: int | None) -> str:
    """Map a SIC code to a coarse sector group. Only 'financials' affects scoring."""
    if sic is None:
        return "other"
    s = int(sic)
    if 6000 <= s <= 6799:
        return "financials"
    if (3570 <= s <= 3579) or (3670 <= s <= 3699) or (7370 <= s <= 7379):
        return "tech"
    if (2833 <= s <= 2836) or (3840 <= s <= 3851) or (8000 <= s <= 8099):
        return "healthcare"
    if (1300 <= s <= 1399) or (2900 <= s <= 2999):
        return "energy"
    if 4900 <= s <= 4999:
        return "utilities"
    if (1000 <= s <= 1499) or (2800 <= s <= 2899):
        return "materials"
    if 3400 <= s <= 3999:
        return "industrials"
    if (2000 <= s <= 2399) or (5000 <= s <= 5999) or (7000 <= s <= 8999):
        return "consumer"
    return "other"


def main() -> None:
    import argparse
    import json
    import urllib.request

    from data.universe import load_universe_members_csv
    from scripts.sec_edgar_ingest import load_cik_map

    parser = argparse.ArgumentParser(description="Fetch SIC sectors for a universe.")
    parser.add_argument("--universe-csv", type=Path, required=True)
    args = parser.parse_args()

    symbols = sorted({m.symbol.upper() for m in load_universe_members_csv(args.universe_csv)})
    cik_map = load_cik_map()
    out_dir = ROOT / "data" / "sectors"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.universe_csv.stem}-sectors.csv"

    rows: list[tuple[str, str, str]] = []
    for i, sym in enumerate(symbols, 1):
        cik = cik_map.get(sym.replace("-", ".").upper()) or cik_map.get(sym.upper())
        sic = None
        if cik is not None:
            try:
                url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
                req = urllib.request.Request(
                    url, headers={"User-Agent": "RegimeResearch jjuni@local.research"}
                )
                with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
                    sub = json.load(r)
                raw_sic = sub.get("sic")
                sic = int(raw_sic) if raw_sic not in (None, "") else None
            except Exception as e:  # noqa: BLE001
                print(f"[{i}/{len(symbols)}] {sym}: ERROR {e}", file=sys.stderr)
        rows.append((sym, "" if sic is None else str(sic), sic_to_sector(sic)))
        time.sleep(0.13)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "sic", "sector"])
        w.writerows(rows)
    fin = sum(1 for _, _, sec in rows if sec == "financials")
    print(f"Wrote {out_path} ({len(rows)} tickers, {fin} financials)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_scripts/test_fetch_sectors.py -q`
Expected: PASS (3 passed). Then `.venv/bin/ruff check scripts/fetch_sectors.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_sectors.py tests/test_scripts/test_fetch_sectors.py
git commit -m "feat(p3): SIC->sector classifier + sector fetch script"
```

---

## Task 2: Sector-aware scoring in the engine

**Files:**
- Modify: `engine/compounder.py`
- Test: `tests/test_engine/test_compounder.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_engine/test_compounder.py`)

```python
from engine.compounder import SECTOR_INVALID_METRICS, score_archetypes  # noqa: E402


def test_sector_invalid_metrics_defines_financials():
    assert "fcf_margin" in SECTOR_INVALID_METRICS["financials"]
    assert "fcf_conversion" in SECTOR_INVALID_METRICS["financials"]
    assert "pfcf" in SECTOR_INVALID_METRICS["financials"]


def test_financial_sector_excludes_fcf_from_scoring():
    # A "bank" with a huge FCF artifact vs a peer; with sectors, FCF is excluded.
    bank = _series("BANKX", [100, 110, 121, 133], [20, 24, 30, 40], [900, 900, 900, 900],
                   100.0, 10.0, 50.0, 5.0)  # absurd FCF (artifact)
    peer = _series("PEER", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38],
                   100.0, 10.0, 50.0, 5.0)
    universe = {"BANKX": (bank, 60.0), "PEER": (peer, 60.0)}

    no_sector = score_archetypes(universe)
    with_sector = score_archetypes(universe, sectors={"BANKX": "financials", "PEER": "tech"})

    # Without sector info, the bank's profitable-compounder score uses its huge FCF margin.
    # With sector info, FCF metrics are nulled for the bank, so its components must NOT
    # include fcf_margin, and PEER (non-financial) is unaffected.
    assert "fcf_margin" in no_sector["BANKX"]["profitable_compounder"].components
    assert "fcf_margin" not in with_sector["BANKX"]["profitable_compounder"].components
    assert "fcf_margin" in with_sector["PEER"]["profitable_compounder"].components


def test_score_archetypes_sectors_default_none_unchanged():
    q = _series("QLT", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38],
                100.0, 10.0, 50.0, 5.0)
    universe = {"QLT": (q, 60.0)}
    assert score_archetypes(universe) == score_archetypes(universe, sectors=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder.py -q`
Expected: FAIL — ImportError for `SECTOR_INVALID_METRICS`, and `score_archetypes` takes no `sectors` kwarg.

- [ ] **Step 3: Write minimal implementation**

In `engine/compounder.py`, add the constant after the existing `_WEIGHTS` block (near line 22+):

```python
# Metrics that are meaningless for a sector and must be excluded from scoring.
# Financials (banks/insurers/REITs): free-cash-flow ratios are not comparable.
SECTOR_INVALID_METRICS: dict[str, frozenset[str]] = {
    "financials": frozenset({"fcf_margin", "fcf_conversion", "pfcf"}),
}
```

Change the `score_archetypes` signature and null invalid metrics right after `metrics` is built. Replace:

```python
def score_archetypes(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
) -> dict[str, dict[str, ArchetypeScore]]:
    symbols = list(universe)
    metrics = {s: compute_metrics(universe[s][0], universe[s][1]) for s in symbols}

    # Cross-sectional Z per metric key.
```

with:

```python
def score_archetypes(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    sectors: dict[str, str] | None = None,
) -> dict[str, dict[str, ArchetypeScore]]:
    symbols = list(universe)
    metrics = {s: compute_metrics(universe[s][0], universe[s][1]) for s in symbols}

    # Sector-aware: null metrics that are meaningless for a symbol's sector
    # (e.g. FCF ratios for financials) so they don't enter Z-scoring or weighting.
    if sectors:
        for s in symbols:
            invalid = SECTOR_INVALID_METRICS.get(sectors.get(s, ""), frozenset())
            for m in invalid:
                if m in metrics[s]:
                    metrics[s][m] = None

    # Cross-sectional Z per metric key.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder.py -q`
Expected: the two new tests for the constant + default-none pass. `test_financial_sector_excludes_fcf_from_scoring` should pass once `rank_compounders` threading (next sub-step) is NOT required (the test calls `score_archetypes` directly). If it fails, recheck the nulling runs before `zmaps`.

- [ ] **Step 5: Thread `sectors` through `rank_compounders`**

Replace:

```python
def rank_compounders(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    top_n: int = 20,
) -> list[CandidateScore]:
    all_scores = score_archetypes(universe)
```

with:

```python
def rank_compounders(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    top_n: int = 20,
    sectors: dict[str, str] | None = None,
) -> list[CandidateScore]:
    all_scores = score_archetypes(universe, sectors=sectors)
```

(Leave the rest of `rank_compounders` unchanged — `CandidateScore.metrics` keeps the RAW metrics incl. FCF for dossier transparency; only the scoring excludes them.)

- [ ] **Step 6: Run full compounder suite + types**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder.py -q` (all pass), `.venv/bin/mypy engine/compounder.py` (clean), `.venv/bin/ruff check engine/compounder.py` (clean).

- [ ] **Step 7: Commit**

```bash
git add engine/compounder.py tests/test_engine/test_compounder.py
git commit -m "feat(p3): sector-aware scoring (null FCF metrics for financials)"
```

---

## Task 3: Dossier sector field + note

**Files:**
- Modify: `engine/compounder_dossier.py`
- Test: `tests/test_engine/test_compounder_dossier.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_dossier_carries_sector_and_financial_note():
    q = _series("BNK", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38],
                100.0, 10.0, 50.0, 5.0)
    ranked = rank_compounders({"BNK": (q, 60.0)}, top_n=1)
    d = build_dossier(ranked[0], sector="financials")
    assert d.sector == "financials"
    md = format_dossier_markdown(d)
    assert "financials" in md
    assert "FCF" in md  # the FCF-excluded note appears for financials


def test_dossier_default_sector_unknown_no_note():
    q = _series("XYZ", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38],
                100.0, 10.0, 50.0, 5.0)
    d = build_dossier(rank_compounders({"XYZ": (q, 60.0)}, top_n=1)[0])
    assert d.sector == "unknown"
    assert "FCF-based metrics excluded" not in format_dossier_markdown(d)
```

(The test file already imports `rank_compounders`, `build_dossier`, `format_dossier_markdown`, and defines `_series`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder_dossier.py -q`
Expected: FAIL — `Dossier` has no `sector`; `build_dossier` takes no `sector` arg.

- [ ] **Step 3: Write minimal implementation**

In `engine/compounder_dossier.py`, add `sector` to the dataclass (after `rationale`, before `alt_signals` to keep the default-fields contiguous):

```python
@dataclass(frozen=True)
class Dossier:
    symbol: str
    archetype: str
    score: float
    metrics: dict[str, float | None]
    flags: tuple[str, ...]
    rationale: str
    sector: str = "unknown"
    alt_signals: dict[str, Any] = field(default_factory=dict)
```

Update `build_dossier` to accept and set `sector`:

```python
def build_dossier(candidate: CandidateScore, sector: str = "unknown") -> Dossier:
    best = candidate.scores[candidate.best_archetype]
    return Dossier(
        symbol=candidate.symbol,
        archetype=candidate.best_archetype,
        score=candidate.best_score,
        metrics=candidate.metrics,
        flags=best.flags,
        rationale=_rationale(candidate),
        sector=sector,
    )
```

In `format_dossier_markdown`, change the header line to include the sector and add the financial note. Replace the header line:

```python
        f"### {d.symbol} — {d.archetype.replace('_', ' ')} ({d.score:.0f}/100)",
```

with:

```python
        f"### {d.symbol} — {d.archetype.replace('_', ' ')} ({d.score:.0f}/100) [{d.sector}]",
```

and immediately after the `d.rationale,` line in the `lines` list, insert a conditional note. Find:

```python
        d.rationale,
        "",
```

and replace with:

```python
        d.rationale,
        *(["", "_FCF-based metrics excluded from scoring (not meaningful for financials)._"]
          if d.sector == "financials" else []),
        "",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder_dossier.py -q` (all pass), `.venv/bin/mypy engine/compounder_dossier.py` (clean), `.venv/bin/ruff check engine/compounder_dossier.py` (clean).

- [ ] **Step 5: Commit**

```bash
git add engine/compounder_dossier.py tests/test_engine/test_compounder_dossier.py
git commit -m "feat(p3): dossier sector field + FCF-excluded note for financials"
```

---

## Task 4: CLI `--sectors-csv` plumbing

**Files:**
- Modify: `trader/cli.py`
- Test: `tests/test_trader_cli.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_trader_cli.py`)

```python
def test_compounder_scan_sectors_csv_excludes_financial_fcf(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_bars(_long_bars("BNKX", 10.0, 0.0010))
    catalog.put_bars(_long_bars("TCH", 10.0, 0.0011))
    catalog.put_fundamentals([
        FundamentalRecord("BNKX", "us", date(2023, 12, 31), datetime(2024, 3, 1),
                           revenue=200.0, net_income=40.0, free_cash_flow=900.0,
                           total_equity=100.0, total_debt=10.0, shares_out=50.0, eps=5.0),
        FundamentalRecord("BNKX", "us", date(2020, 12, 31), datetime(2021, 3, 1),
                           revenue=100.0, net_income=10.0, free_cash_flow=400.0,
                           total_equity=100.0, total_debt=10.0, shares_out=50.0, eps=2.0),
        FundamentalRecord("TCH", "us", date(2023, 12, 31), datetime(2024, 3, 1),
                           revenue=200.0, net_income=40.0, free_cash_flow=30.0,
                           total_equity=100.0, total_debt=10.0, shares_out=50.0, eps=5.0),
        FundamentalRecord("TCH", "us", date(2020, 12, 31), datetime(2021, 3, 1),
                           revenue=100.0, net_income=10.0, free_cash_flow=8.0,
                           total_equity=100.0, total_debt=10.0, shares_out=50.0, eps=2.0),
    ])
    sectors = tmp_path / "sectors.csv"
    sectors.write_text("symbol,sic,sector\nBNKX,6021,financials\nTCH,7372,tech\n", encoding="utf-8")

    result = cli.main([
        "compounder-scan", "BNKX,TCH",
        "--as-of", "2024-06-30", "--top-n", "2", "--no-fetch",
        "--sectors-csv", str(sectors),
        "--catalog-db", str(catalog_db),
    ])
    captured = capsys.readouterr()
    assert result == 0
    assert "[financials]" in captured.out  # BNKX dossier tagged
    assert "FCF-based metrics excluded" in captured.out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trader_cli.py::test_compounder_scan_sectors_csv_excludes_financial_fcf -q`
Expected: FAIL — `unrecognized arguments: --sectors-csv`.

- [ ] **Step 3: Write minimal implementation**

In `trader/cli.py`, in the `compounder-scan` subparser block (near the `--archetype`/`--snapshot` args), add:

```python
    compounder.add_argument("--sectors-csv", type=Path, default=None,
                            help="CSV (symbol,sic,sector) to enable sector-aware scoring "
                            "(financials: FCF metrics excluded).")
```

In `_run_compounder_scan`, after `ranked = rank_compounders(...)` is currently called, load the sector map first and pass it through. Replace:

```python
    ranked = rank_compounders(universe, top_n=args.top_n)
    if args.archetype:
        ranked = [c for c in ranked if c.best_archetype == args.archetype]

    lines = [f"# Compounder Scan — as-of {as_of} — {len(universe)} names scored", ""]
    for c in ranked:
        lines.append(format_dossier_markdown(build_dossier(c)))
        lines.append("")
```

with:

```python
    sectors: dict[str, str] = {}
    if args.sectors_csv is not None and args.sectors_csv.exists():
        import csv as _csv

        with args.sectors_csv.open(encoding="utf-8", newline="") as fh:
            for row in _csv.DictReader(fh):
                sym = (row.get("symbol") or "").upper()
                if sym:
                    sectors[sym] = row.get("sector") or "unknown"

    ranked = rank_compounders(universe, top_n=args.top_n, sectors=sectors or None)
    if args.archetype:
        ranked = [c for c in ranked if c.best_archetype == args.archetype]

    lines = [f"# Compounder Scan — as-of {as_of} — {len(universe)} names scored", ""]
    for c in ranked:
        lines.append(format_dossier_markdown(build_dossier(c, sector=sectors.get(c.symbol, "unknown"))))
        lines.append("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trader_cli.py::test_compounder_scan_sectors_csv_excludes_financial_fcf -q` (pass), then `.venv/bin/python -m pytest tests/test_trader_cli.py -q` (no regression), `.venv/bin/ruff check trader/cli.py`, `.venv/bin/mypy trader/cli.py` (no new errors).

- [ ] **Step 5: Commit**

```bash
git add trader/cli.py tests/test_trader_cli.py
git commit -m "feat(p3): compounder-scan --sectors-csv (sector-aware scoring + dossier)"
```

---

## Task 5: Runtime — fetch sectors + re-scan (operational)

**Files:** none (runtime + verification). Single session (no concurrent DB writers).

- [ ] **Step 1: Fetch sectors for the universe**

Run: `.venv/bin/python scripts/fetch_sectors.py --universe-csv data/universes/sp400-600-current.csv`
Expected: `data/sectors/sp400-600-current-sectors.csv` written for ~1,003 tickers, with a printed financials count (expect ~150–250 for S&P 400+600). ~2–3 min (rate-limited).

- [ ] **Step 2: Re-scan with sector awareness**

Run:
```bash
.venv/bin/trader compounder-scan ALL \
  --universe-csv data/universes/sp400-600-current.csv \
  --snapshot data/snapshots/fundamentals-2026-05-31-merged.csv \
  --sectors-csv data/sectors/sp400-600-current-sectors.csv \
  --as-of 2026-05-31 --top-n 30 --no-fetch \
  --output out/compounder-scan-sp400-600.md
```
Expected: exit 0. Compare to the prior (no-sector) run: financials (FBRT/EQH/BANC) should drop or be re-scored on non-FCF metrics; dossiers for financials show `[financials]` + the FCF-excluded note. Non-financial top names (TGTX/CYTK/AVAV/TDC…) essentially unchanged. Sanity-check 2–3 financial dossiers.

- [ ] **Step 3: Full gate + commit the sectors CSV**

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check scripts/ engine/ trader/cli.py
.venv/bin/mypy scripts/fetch_sectors.py engine/compounder.py engine/compounder_dossier.py trader/cli.py
```
Expected: all pass/clean. Then (the sectors CSV is small, ~30KB, NOT gitignored):
```bash
git add data/sectors/sp400-600-current-sectors.csv
git commit -m "feat(p3): sector map for S&P 400+600; sector-aware re-scan"
```
(`out/` is gitignored — scan report stays local.)

---

## Notes for the implementer

- **Backward compatibility is required**: `sectors=None` must reproduce P1/P2 behavior exactly. The default-none test (Task 2 Step 1) guards this — do not change scoring when no sector map is supplied.
- **`CandidateScore.metrics` stays RAW** (FCF values shown in the dossier) — only the SCORING excludes them. The dossier's note explains why FCF is shown but not scored for financials.
- **Single ingest/fetch session** — `fetch_sectors.py` only reads SEC (no DB write), so it won't lock the catalog; but don't run it concurrently with any catalog writer.
- **Reuse `sec_edgar_ingest.load_cik_map`** — do not duplicate the CIK-map fetch.
- Follow existing script conventions (`sys.path.insert`, `# noqa: E402`, lazy network imports inside `main()`).
