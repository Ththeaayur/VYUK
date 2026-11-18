"""
Stock Market Quote Plugin for Voice AI Assistant.

GATED since v5.54 (Wave 0, MN-4.1 stock-quote audit — wave 17
MN-4.2). This plugin used to read Yahoo Finance's chart
endpoint behind a spoofed browser User-Agent. Yahoo's terms
make that data personal-use (the yfinance README states the
API "is intended for personal use" and the tool "is intended
for research and educational purposes"), so a SOLD product may
not display it — the tool now answers with an honest refusal
and makes NO network call. No commercial-clean replacement
exists on the shelf to adopt in its place: the adoptable
candidate (Tiingo, MN-4.9) needs its paid redistribution tier
bought plus written confirmation — a purchase decision, not a
build step — and the free alternative's terms are unverified
(Stooq, MN-4.5, STILL-OWED), which the commercial rule treats
as a hard block. When a licensed source is configured, this
is the file that learns to call it.

The declared capability header is unchanged: it records the
plugin's trust class (network,read), which a future licensed
source will need again — the gate is a policy inside run(),
not a capability removal.
"""
# vyuk-plugin: capabilities=network,read

PLUGIN = {
    "name": "get_stock_quote",
    "description": "Stock price questions (e.g. AAPL, NVDA, TSLA). Currently answers with an honest unavailable message: VYUK's previous quote source's terms don't permit commercial display, and no commercially-licensed quote source is configured yet.",
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "The stock ticker symbol (e.g. AAPL, MSFT, NVDA, TSLA)"
            }
        },
        "required": ["symbol"]
    }
}


def run(symbol: str) -> str:
    ticker = (symbol or "").strip().upper()
    if not ticker:
        return "No ticker symbol given — tell me which stock you mean."
    return (
        f"Stock quotes are off right now, so I can't look up "
        f"{ticker}. The quote source VYUK used before doesn't "
        "allow its prices to be shown in a commercial product, "
        "and I won't use a source whose terms don't permit it. "
        "Quotes come back once a properly licensed source is "
        "set up — your other market questions I can still try "
        "to help with."
    )
