# API Keys and Fallback Policy

This project can use API keys, but every keyed source must have a fallback path.
The tool should produce a data-gap note instead of blocking a research run when a
provider is unavailable, rate-limited, expired, or blocked by the local network.

This is a research system, not an execution system. Do not store broker trading
keys here unless order-routing code is explicitly added and reviewed.

## Current Environment Variables

Set keys in the current PowerShell session:

```powershell
$env:MARKETAUX_API_KEY="your-marketaux-token"
$env:ALPHAVANTAGE_API_KEY="your-alpha-vantage-key"
$env:TRADING_COPILOT_CONTACT="Your Name your.email@example.com"
```

Persist keys for future PowerShell sessions:

```powershell
[Environment]::SetEnvironmentVariable("MARKETAUX_API_KEY", "your-marketaux-token", "User")
[Environment]::SetEnvironmentVariable("ALPHAVANTAGE_API_KEY", "your-alpha-vantage-key", "User")
[Environment]::SetEnvironmentVariable("TRADING_COPILOT_CONTACT", "Your Name your.email@example.com", "User")
```

Never commit secrets to git. If a key is exposed in chat, logs, screenshots, or a
committed file, rotate it from the provider dashboard.

## Provider Setup

| Variable | Status | Used For | Where To Get It | Fallback If Blocked |
|---|---|---|---|---|
| `MARKETAUX_API_KEY` | Wired | `news-fast` tagged market news | Sign up at `https://www.marketaux.com/`, then copy the API token from the dashboard. Docs: `https://www.marketaux.com/documentation` | Google News RSS plus SEC events; report Marketaux as a data gap |
| `ALPHAVANTAGE_API_KEY` | Wired | `calendar` earnings calendar primary source | Free key page is linked in Alpha Vantage docs: `https://www.alphavantage.co/support/#api-key`; premium plans: `https://www.alphavantage.co/premium/` | Nasdaq no-key earnings calendar endpoint; report Alpha Vantage as a data gap |
| `TRADING_COPILOT_CONTACT` | Wired as contact header input where relevant | SEC EDGAR fair-access identification | No key. Use a real contact string, e.g. name plus email | SEC failure becomes a data gap; Yahoo fundamentals fallback may still run |

## Planned Korean Market Keys

These are not all wired yet, but this is the intended source priority when Korean
coverage is expanded beyond the current no-key KRX/KIND and Yahoo setup.

| Variable | Priority Use | Where To Get It | Fallback If Blocked |
|---|---|---|---|
| `OPENDART_API_KEY` | Korean disclosures, filings, major contracts, guidance, audit opinions | OpenDART authentication key application: `https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do` | DART/KIND web pages, company IR pages, news search; mark disclosure feed as a data gap |
| `DATA_GO_KR_SERVICE_KEY` | Official Korean listed-stock reference data | Data.go.kr API application for "Financial Services Commission_KRX Listed Stock Information": `https://www.data.go.kr/en/data/15094775/openapi.do` | KRX/KIND `corpList.do` download already used by `screen-all --market kospi/kosdaq/kr` |
| `KRX_OPENAPI_KEY` | Official KRX market data if approved | KRX Data Marketplace Open API: `https://openapi.krx.co.kr/contents/OPP/MAIN/main/index.cmd` | Yahoo Finance `.KS` / `.KQ` chart data and KRX/KIND universe; mark KRX feed as a data gap |
| `BENZINGA_API_KEY` | Professional real-time news, calendars, conference calls | Benzinga APIs: `https://www.benzinga.com/apis/`; docs: `https://docs.benzinga.com/` | Marketaux, RSS, SEC/OpenDART, exchange calendars, company IR |

## Fallback Ladder By Workflow

| Workflow | Primary With Key | No-Key / Backup Sources | Failure Behavior |
|---|---|---|---|
| `news-fast` | Marketaux | Google News RSS, SEC EDGAR | Continue with available items and list missing providers |
| `calendar` | Alpha Vantage earnings calendar | Nasdaq earnings calendar no-key endpoint, company IR calendar manually | Continue with Nasdaq or data-gap note |
| `fundamentals` US | SEC companyfacts | Yahoo fundamentals-timeseries | Continue with Yahoo or data-gap note |
| `screen-all --market us` | Nasdaq Trader symbol files plus Yahoo quotes | Cached output if added later | Quote failures lower confidence, not a crash |
| `screen-all --market kospi/kosdaq/kr` | Future: data.go.kr or KRX Open API | Current: KRX/KIND listed-company download plus Yahoo `.KS/.KQ` quotes | Continue with rows that have quotes; list unavailable quotes as risks |
| Korean disclosures | Future: OpenDART | DART/KIND pages, company IR, news | Continue with no disclosure signals and explicit data gap |
| `macro` | FRED CSV, BLS/Fed official pages | GovSpending FRED mirror, known BLS schedule fallback | Continue with fallback and source notes |

## Recommended Setup Order

1. Start with `MARKETAUX_API_KEY` free tier if faster news becomes useful.
2. Add `ALPHAVANTAGE_API_KEY` if the Nasdaq fallback is too sparse for earnings calendar work.
3. Add `OPENDART_API_KEY` before relying on Korean contract, guidance, or disclosure signals.
4. Add `DATA_GO_KR_SERVICE_KEY` or `KRX_OPENAPI_KEY` only when the current KRX/KIND plus Yahoo setup is not stable enough.
5. Consider Benzinga only if you need professional-grade news latency, calendars, or conference-call datasets.

## Operational Rules

- Use one provider key per service. Do not create multiple accounts to bypass rate limits.
- Cache source responses when a full-market screen is run repeatedly.
- Treat unofficial endpoints, including Yahoo and Nasdaq no-key endpoints, as best-effort sources.
- Put source URLs into every report so any important fact can be checked manually.
- If primary and fallback disagree, show both values and mark the field as "needs verification".
