from __future__ import annotations

from datetime import date, datetime

from data.ingest.sec_13f import parse_13f_infotable_xml

# Real 13F information tables carry a default namespace; the parser must see through it.
_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"

# A post-2023 acceptance (whole-dollar era) — the _parse default.
_ASOF_POST = datetime(2025, 5, 15, 16, 0)
_ASOF_PRE = datetime(2020, 5, 15, 16, 0)  # pre-2023 (thousands era)


def _infotable(rows: str, ns: bool = True) -> bytes:
    open_tag = f'<informationTable xmlns="{_NS}">' if ns else "<informationTable>"
    return f'<?xml version="1.0" encoding="UTF-8"?>{open_tag}{rows}</informationTable>'.encode()


_APPLE_ROW = """
<infoTable>
  <nameOfIssuer>APPLE INC</nameOfIssuer>
  <titleOfClass>COM</titleOfClass>
  <cusip>037833100</cusip>
  <value>1000000</value>
  <shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  <investmentDiscretion>SOLE</investmentDiscretion>
</infoTable>
"""


def _parse(rows: str, ns: bool = True, asof_ts: datetime = _ASOF_POST, **kw):
    return parse_13f_infotable_xml(
        _infotable(rows, ns=ns),
        manager="BERKSHIRE HATHAWAY INC",
        report_date=date(2025, 3, 31),
        asof_ts=asof_ts,
        **kw,
    )


def test_parses_basic_holding_with_namespace() -> None:
    out = _parse(_APPLE_ROW)
    assert len(out) == 1
    rec = out[0]
    assert rec.cusip == "037833100"
    assert rec.issuer_name == "APPLE INC"
    assert rec.manager == "BERKSHIRE HATHAWAY INC"
    assert rec.report_date == date(2025, 3, 31)
    assert rec.asof_ts == _ASOF_POST
    assert rec.shares == 5000.0


def test_parses_without_namespace_too() -> None:
    # Defensive: some legacy/hand-built tables omit the namespace.
    out = _parse(_APPLE_ROW, ns=False)
    assert len(out) == 1
    assert out[0].cusip == "037833100"


def test_value_units_auto_detected_whole_dollars_post_2023() -> None:
    # Filing accepted on/after 2023-01-03 → 'value' is whole dollars; no override needed.
    out = _parse(_APPLE_ROW, asof_ts=_ASOF_POST)
    assert out[0].value_usd == 1_000_000.0


def test_value_units_auto_detected_thousands_pre_2023() -> None:
    # Pre-2023 filing → 'value' is in thousands of dollars → x1000.
    out = _parse(_APPLE_ROW, asof_ts=_ASOF_PRE)
    assert out[0].value_usd == 1_000_000 * 1000.0


def test_explicit_value_multiplier_overrides_auto_detect() -> None:
    # An explicit multiplier always wins over the asof_ts-derived default.
    out = _parse(_APPLE_ROW, asof_ts=_ASOF_PRE, value_multiplier=1.0)
    assert out[0].value_usd == 1_000_000.0


def test_excludes_option_positions_by_default() -> None:
    row = _APPLE_ROW.replace(
        "<investmentDiscretion>SOLE</investmentDiscretion>", "<putCall>Call</putCall>"
    )
    assert _parse(row) == []  # an option line is not share ownership
    kept = _parse(row, include_options=True)
    assert len(kept) == 1
    assert kept[0].put_call == "Call"


def test_putcall_none_sentinel_kept_as_equity() -> None:
    # A filer writing the literal "None" placeholder must NOT be misread as an option and dropped.
    row = _APPLE_ROW.replace(
        "<investmentDiscretion>SOLE</investmentDiscretion>", "<putCall>None</putCall>"
    )
    out = _parse(row)
    assert len(out) == 1
    assert out[0].put_call == ""


def test_prn_principal_yields_none_shares() -> None:
    # sshPrnamtType PRN is a bond principal amount, not a share count.
    row = _APPLE_ROW.replace(
        "<sshPrnamtType>SH</sshPrnamtType>", "<sshPrnamtType>PRN</sshPrnamtType>"
    )
    out = _parse(row)
    assert len(out) == 1
    assert out[0].shares is None
    assert out[0].value_usd is not None  # the dollar value is still meaningful


def test_missing_sshprnamttype_defaults_to_shares() -> None:
    # A malformed row missing the type element must keep the share count (SH is the required norm),
    # not silently drop it to None (which would make an equity position look like a bond).
    row = _APPLE_ROW.replace("<sshPrnamtType>SH</sshPrnamtType>", "")
    out = _parse(row)
    assert len(out) == 1
    assert out[0].shares == 5000.0


def test_resolves_symbol_via_cusip_map() -> None:
    out = _parse(_APPLE_ROW, cusip_symbol_map={"037833100": "AAPL"})
    assert out[0].symbol == "AAPL"
    # Unmapped CUSIP leaves symbol empty (resolution/whale-filter happens at ingest, not here).
    assert _parse(_APPLE_ROW)[0].symbol == ""


def test_symbol_is_upper_cased_regardless_of_map_casing() -> None:
    # Mirror the sec_form4 contract: the stored ticker is always upper-case so PIT catalog joins
    # against InsiderTradeRecord.symbol (upper) do not silently miss.
    out = _parse(_APPLE_ROW, cusip_symbol_map={"037833100": "aapl"})
    assert out[0].symbol == "AAPL"


def test_cusip_map_falls_back_to_8_char_issue_prefix() -> None:
    # The 8-char prefix (issuer + issue, minus only the check digit) identifies the same security.
    out = _parse(_APPLE_ROW, cusip_symbol_map={"03783310": "AAPL"})
    assert out[0].symbol == "AAPL"


def test_cusip_6char_issuer_prefix_does_not_resolve() -> None:
    # The 6-char issuer prefix spans ALL the issuer's securities (bonds/preferred/warrants); it must
    # NOT resolve a common-stock ticker, or a bond CUSIP would be mislabeled as the equity.
    out = _parse(_APPLE_ROW, cusip_symbol_map={"037833": "AAPL"})
    assert out[0].symbol == ""


def test_skips_rows_missing_cusip_or_value() -> None:
    no_cusip = _APPLE_ROW.replace("<cusip>037833100</cusip>", "")
    no_value = _APPLE_ROW.replace("<value>1000000</value>", "")
    assert _parse(no_cusip) == []
    assert _parse(no_value) == []


def test_malformed_xml_returns_empty() -> None:
    out = parse_13f_infotable_xml(
        b"<informationTable><infoTable><cusip>broken",
        manager="X",
        report_date=date(2025, 3, 31),
        asof_ts=_ASOF_POST,
    )
    assert out == []


def test_parses_multiple_holdings() -> None:
    msft = _APPLE_ROW.replace("APPLE INC", "MICROSOFT CORP").replace("037833100", "594918104")
    out = _parse(_APPLE_ROW + msft)
    assert sorted(r.issuer_name for r in out) == ["APPLE INC", "MICROSOFT CORP"]
