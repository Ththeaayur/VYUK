"""
Cryptocurrency Market Price Plugin for Voice AI Assistant.
GATED (v5.66, Money track, crypto-price audit): run() makes NO
network call and returns an honest refusal. The previous
implementation read CoinGecko's keyless endpoint, whose tier the
audit found described for prototyping/open-source/educational use,
not commercial display — a sold product may not display it, and
no commercial-clean source was adoptable inside the track (a keyed
plan + prominent attribution + CC-1 entry is the owner's purchase
decision). The tool stays REGISTERED under its name so users hear
the honest answer, not a hallucinated price.
"""
# vyuk-plugin: capabilities=network,read
# v5.66 gate: kept like stock_ticker (v5.54) — gated content, unchanged egress class

PLUGIN = {
    "name": "get_crypto_price",
    "description": "Get current price and 24h change for cryptocurrencies like Bitcoin (BTC), Ethereum (ETH), Solana (SOL).",
    "parameters": {
        "type": "object",
        "properties": {
            "coin_id": {
                "type": "string",
                "description": "The coin name or ID (e.g., 'bitcoin', 'ethereum', 'solana', 'dogecoin', 'cardano')"
            }
        },
        "required": ["coin_id"]
    }
}

COIN_MAP = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
    "doge": "dogecoin",
    "ada": "cardano",
    "xrp": "ripple"
}


def run(coin_id: str) -> str:
    cleaned = (coin_id or "").strip().lower()
    if not cleaned:
        return "No coin given — tell me which cryptocurrency you mean."
    coin = COIN_MAP.get(cleaned, cleaned)
    return (
        f"Crypto prices are off right now, so I can't look up "
        f"{coin}. The price source VYUK used before — CoinGecko's "
        f"keyless tier — doesn't allow its prices to be shown in a "
        f"commercial product, and I won't use a source whose terms "
        f"don't permit it. Prices come back once a properly licensed "
        f"source is set up — your other market questions I can still "
        f"try to help with."
    )
