"""MCP client — SDK OAuth session classes (split from core/mcp_client.py, Phase B B23)."""
from __future__ import annotations

from typing import Any

# --- SPLIT BODY BELOW (verbatim from core/mcp_client.py) ---


# ---------------------------------------------------------------------------
# SDK OAuth path (client_credentials only — see module docstring)
# ---------------------------------------------------------------------------

class _MemoryTokenStorage:
    """Token storage that lives in process memory only. Tokens
    are never written to disk by this bus — a crashed process
    re-authenticates, which is the safe direction."""

    def __init__(self) -> None:
        self._tokens: Any = None
        self._client_info: Any = None

    async def get_tokens(self) -> Any:
        return self._tokens

    async def set_tokens(self, tokens: Any) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> Any:
        return self._client_info

    async def set_client_info(self, info: Any) -> None:
        self._client_info = info


class _SDKSession:
    """Uniform adapter over the raw SDK's Streamable HTTP client
    + ClientSession, so the server task treats FastMCP and SDK
    clients identically (list_tools / call_tool / async with)."""

    def __init__(self, url: str, provider: Any, headers: dict[str, str]) -> None:
        self._url = url
        self._provider = provider
        self._headers = headers
        self._http_client: Any = None
        self._transport_cm: Any = None
        self._session: Any = None

    async def __aenter__(self) -> "_SDKSession":
        import httpx2  # noqa: F401 — lazy; the SDK's own transport lib
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        self._http_client = httpx2.AsyncClient(
            auth=self._provider,
            headers=self._headers or None)
        self._transport_cm = streamablehttp_client(
            self._url, http_client=self._http_client)
        read, write, _get_session_id = await self._transport_cm.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        try:
            if self._session is not None:
                await self._session.__aexit__(*exc)
        finally:
            self._session = None
            try:
                if self._transport_cm is not None:
                    await self._transport_cm.__aexit__(*exc)
            finally:
                self._transport_cm = None
                if self._http_client is not None:
                    await self._http_client.aclose()
                    self._http_client = None

    async def list_tools(self) -> list[Any]:
        result = await self._session.list_tools()
        return list(result.tools)

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        return await self._session.call_tool(name, args)
