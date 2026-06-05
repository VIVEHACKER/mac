from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, date, datetime

from data.catalog import MarketDataCatalog
from data.ingest.sec_form345_bulk import (
    ingest_form345_bulk,
    parse_form345_tables,
    quarter_url,
    read_quarter_zip,
)


def _sub(acc: str, sym: str, doc: str = "4", filing: str = "15-MAR-2023") -> dict:
    return {
        "ACCESSION_NUMBER": acc,
        "DOCUMENT_TYPE": doc,
        "ISSUERTRADINGSYMBOL": sym,
        "ISSUERNAME": f"{sym} CORP",
        "ISSUERCIK": "0000000001",
        "FILING_DATE": filing,
        "PERIOD_OF_REPORT": "01-MAR-2023",
    }


def _own(acc: str, name: str, rel: str, title: str = "") -> dict:
    return {
        "ACCESSION_NUMBER": acc,
        "RPTOWNERNAME": name,
        "RPTOWNER_RELATIONSHIP": rel,
        "RPTOWNER_TITLE": title,
    }


def _txn(
    acc: str,
    code: str,
    shares: str = "1000",
    price: str = "150",
    txn_date: str = "01-MAR-2023",
    form_type: str = "4",
) -> dict:
    return {
        "ACCESSION_NUMBER": acc,
        "TRANS_CODE": code,
        "TRANS_FORM_TYPE": form_type,
        "TRANS_SHARES": shares,
        "TRANS_PRICEPERSHARE": price,
        "TRANS_DATE": txn_date,
    }


def test_parses_open_market_buy() -> None:
    out = parse_form345_tables(
        [_sub("a1", "AAPL")],
        [_own("a1", "COOK TIMOTHY", "Officer", "CEO")],
        [_txn("a1", "P")],
    )
    assert len(out) == 1
    r = out[0]
    assert r.symbol == "AAPL"
    assert r.txn_code == "P"
    assert r.insider_name == "COOK TIMOTHY"
    assert r.insider_role == "CEO"
    assert r.txn_date == date(2023, 3, 1)
    # FILING_DATE = visibility key, set to END of the filing day (EDGAR posts through the evening) so
    # a same-day market-open backtest does not see a filing before it was actually public.
    assert r.asof_ts == datetime(2023, 3, 15, 23, 59, 59)
    assert r.value_usd == 150_000.0
    assert r.source == "sec:form345"


def test_filters_non_buy_codes() -> None:
    out = parse_form345_tables(
        [_sub("a1", "AAPL")],
        [_own("a1", "X", "Officer", "CEO")],
        [_txn("a1", c) for c in ("A", "S", "M", "F")],  # grant/sale/exercise/tax — not buys
    )
    assert out == []


def test_filters_to_universe_symbols() -> None:
    subs = [_sub("a1", "AAPL"), _sub("a2", "NOPE")]
    owners = [_own("a1", "X", "Officer", "CEO"), _own("a2", "Y", "Director")]
    trans = [_txn("a1", "P"), _txn("a2", "P")]
    out = parse_form345_tables(subs, owners, trans, symbols=["AAPL"])
    assert [r.symbol for r in out] == ["AAPL"]


def test_includes_amendments_excludes_form3_and_5() -> None:
    subs = [
        _sub("a4", "AAPL", "4"),
        _sub("aa", "AAPL", "4/A"),
        _sub("a3", "AAPL", "3"),
        _sub("a5", "AAPL", "5"),
    ]
    owners = [_own(a, "X", "Officer", "CEO") for a in ("a4", "aa", "a3", "a5")]
    trans = [_txn(a, "P") for a in ("a4", "aa", "a3", "a5")]
    out = parse_form345_tables(subs, owners, trans)
    assert sorted({r.txn_code for r in out}) == ["P"]
    assert len(out) == 2  # only the 4 and 4/A; form 3 and 5 dropped


def test_joint_owners_combined_name_and_best_role() -> None:
    out = parse_form345_tables(
        [_sub("a1", "AAPL")],
        [_own("a1", "ALICE", "Director"), _own("a1", "BOB", "Officer", "CFO")],
        [_txn("a1", "P")],
    )
    assert len(out) == 1
    assert out[0].insider_name == "ALICE; BOB"
    assert out[0].insider_role == "CFO"  # officer title beats director


def test_officer_without_real_title_falls_back_to_officer() -> None:
    out = parse_form345_tables(
        [_sub("a1", "AAPL")],
        [_own("a1", "X", "Officer", "See Remarks")],  # generic placeholder title
        [_txn("a1", "P")],
    )
    assert out[0].insider_role == "Officer"


def test_ten_percent_owner_role() -> None:
    out = parse_form345_tables(
        [_sub("a1", "AAPL")],
        [_own("a1", "FUND LP", "10% Owner")],
        [_txn("a1", "P")],
    )
    assert out[0].insider_role == "10% owner"


def test_missing_price_yields_none_value() -> None:
    out = parse_form345_tables(
        [_sub("a1", "AAPL")],
        [_own("a1", "X", "Officer", "CEO")],
        [_txn("a1", "P", shares="1000", price="")],
    )
    assert out[0].shares == 1000.0
    assert out[0].price is None
    assert out[0].value_usd is None


def test_dot_separated_share_class_matches_hyphen_universe() -> None:
    # SEC bulk uses dot share-class (BRK.B); the universe passes hyphen (BRK-B). They must match,
    # and the record is stored in the canonical (hyphen) form the catalog is queried by.
    out = parse_form345_tables(
        [_sub("a1", "BRK.B")],
        [_own("a1", "BUFFETT W", "Officer", "Chairman")],
        [_txn("a1", "P")],
        symbols=["BRK-B"],
    )
    assert len(out) == 1
    assert out[0].symbol == "BRK-B"


def test_zero_price_row_is_dropped() -> None:
    # A zero-price P row (off-market/gift miscoded) is not a real open-market buy — drop it entirely
    # (nulling the notional but keeping the row would still fake a count-weighted cluster).
    out = parse_form345_tables(
        [_sub("a1", "AAPL")],
        [_own("a1", "X", "Officer", "CEO")],
        [_txn("a1", "P", shares="1000", price="0")],
    )
    assert out == []


def test_nonpositive_shares_row_is_dropped() -> None:
    assert (
        parse_form345_tables(
            [_sub("a1", "AAPL")],
            [_own("a1", "X", "Officer", "CEO")],
            [_txn("a1", "P", shares="-5", price="10")],
        )
        == []
    )


def test_joint_officers_surface_the_most_senior_title() -> None:
    # Two co-filing officers in TSV order VP then CEO: the role must let the CEO win, not row order.
    out = parse_form345_tables(
        [_sub("a1", "AAPL")],
        [_own("a1", "ALICE", "Officer", "VP Sales"), _own("a1", "BOB", "Officer", "CEO")],
        [_txn("a1", "P")],
    )
    from signals.insider import DEFAULT_ROLE_WEIGHTS, _role_weight

    assert _role_weight(out[0].insider_role, DEFAULT_ROLE_WEIGHTS) == 1.0  # CEO, despite VP first


def test_ingest_isolates_a_bad_quarter(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    good = _make_zip()

    def flaky(url: str) -> bytes:
        if "2023q1" in url:
            raise RuntimeError("SEC 404")
        return good

    # The bad first quarter must not abort the second.
    n = ingest_form345_bulk(
        [(2023, 1), (2023, 2)],
        cat,
        symbols=["AAPL"],
        fetch_zip=flaky,
        sleep=lambda *_: None,
    )
    assert n == 1  # only the good quarter stored, no crash


def test_excludes_disposed_p_rows() -> None:
    # A P row flagged disposed (TRANS_ACQUIRED_DISP_CD='D') is not an open-market buy.
    disp = {**_txn("a1", "P"), "TRANS_ACQUIRED_DISP_CD": "D"}
    acq = {**_txn("a1", "P"), "TRANS_ACQUIRED_DISP_CD": "A"}
    out = parse_form345_tables(
        [_sub("a1", "AAPL")], [_own("a1", "X", "Officer", "CEO")], [disp, acq]
    )
    assert len(out) == 1  # only the acquisition


def test_excludes_form5_transaction_rows() -> None:
    # A Form-4 submission can carry late-reported Form-5 transaction rows (TRANS_FORM_TYPE=5); those
    # are not Form-4 insider buys and must be excluded.
    out = parse_form345_tables(
        [_sub("a1", "AAPL")],
        [_own("a1", "X", "Officer", "CEO")],
        [_txn("a1", "P", form_type="5"), _txn("a1", "P", form_type="4")],
    )
    assert len(out) == 1  # only the Form-4 transaction row


def test_quarter_url() -> None:
    assert quarter_url(2023, 1) == (
        "https://www.sec.gov/files/structureddata/data/"
        "insider-transactions-data-sets/2023q1_form345.zip"
    )


def _make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, rows, cols in (
            (
                "SUBMISSION.tsv",
                [_sub("a1", "AAPL")],
                [
                    "ACCESSION_NUMBER",
                    "DOCUMENT_TYPE",
                    "ISSUERTRADINGSYMBOL",
                    "ISSUERNAME",
                    "ISSUERCIK",
                    "FILING_DATE",
                    "PERIOD_OF_REPORT",
                ],
            ),
            (
                "REPORTINGOWNER.tsv",
                [_own("a1", "COOK", "Officer", "CEO")],
                ["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"],
            ),
            (
                "NONDERIV_TRANS.tsv",
                [_txn("a1", "P")],
                [
                    "ACCESSION_NUMBER",
                    "TRANS_CODE",
                    "TRANS_FORM_TYPE",
                    "TRANS_SHARES",
                    "TRANS_PRICEPERSHARE",
                    "TRANS_DATE",
                ],
            ),
        ):
            out = io.StringIO()
            w = csv.DictWriter(out, fieldnames=cols, delimiter="\t")
            w.writeheader()
            w.writerows(rows)
            z.writestr(name, out.getvalue())
    return buf.getvalue()


def test_read_quarter_zip() -> None:
    subs, owners, trans = read_quarter_zip(_make_zip())
    assert subs[0]["ISSUERTRADINGSYMBOL"] == "AAPL"
    assert trans[0]["TRANS_CODE"] == "P"


def test_ingest_bulk_end_to_end(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    zb = _make_zip()
    n = ingest_form345_bulk(
        [(2023, 1)],
        cat,
        symbols=["AAPL"],
        fetch_zip=lambda url: zb,
        sleep=lambda *_: None,
    )
    assert n == 1
    out = cat.get_insider_trades("AAPL")
    assert len(out) == 1 and out[0].txn_code == "P"
    # PIT: the buy filed 2023-03-15 is not visible the day before
    assert cat.get_insider_trades("AAPL", as_of=datetime(2023, 3, 14, tzinfo=UTC)) == []
