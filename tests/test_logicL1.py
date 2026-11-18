"""Logic batch L1 (LOG-01 + LOG-02): MCP URL parsing + credential
detection in core/mcp_client.py validate_server_config.

Pins:
* URL userinfo (username/password in the url) is refused outright
  on EVERY host, including loopback — it is credentials, and it
  is never echoed in the returned error string.
* The scheme is case-insensitive (normalized to lower case); a
  hostname is required; an invalid port or malformed authority
  is refused at config time, not at connect.
* Gate C behavior is preserved exactly: non-loopback http +
  credentials (auth object or credential-bearing headers) is a
  HARD refusal; loopback http + credentials is allowed ONLY
  with cleartext_loopback_warning recorded; https + credentials
  and credential-free http are unchanged.
"""

import unittest

from core.mcp_client import validate_server_config as validate_server


def _v(url, **extra):
    entry = {"url": url, "enabled": True}
    entry.update(extra)
    return validate_server("s", entry)


def _auth():
    return {
        "type": "client_credentials",
        "issuer": "https://issuer.example",
        "client_id": "cid",
        "client_secret_env": "MCP_SECRET",
    }


class TestL1UserinfoRejected(unittest.TestCase):
    def test_userinfo_remote_rejected(self):
        cfg, err = _v("http://alice:s3cret@example.com/mcp")
        self.assertIsNone(cfg)
        self.assertIsNotNone(err)

    def test_userinfo_https_remote_rejected(self):
        # Even over https, credentials belong in auth fields,
        # not in the url — the url must never carry them.
        cfg, err = _v("https://alice:s3cret@example.com/mcp")
        self.assertIsNone(cfg)
        self.assertIsNotNone(err)

    def test_userinfo_loopback_rejected(self):
        # Loopback does NOT save URL userinfo: the loopback
        # warning path is for auth objects / headers only.
        for url in (
            "http://alice:s3cret@localhost:8080/mcp",
            "http://alice:s3cret@127.0.0.1/mcp",
            "http://alice:s3cret@[::1]:8080/mcp",
        ):
            with self.subTest(url=url):
                cfg, err = _v(url)
                self.assertIsNone(cfg)
                self.assertIsNotNone(err)

    def test_password_only_or_username_only_rejected(self):
        for url in (
            "http://:s3cret@example.com/mcp",
            "http://alice@example.com/mcp",
        ):
            with self.subTest(url=url):
                cfg, err = _v(url)
                self.assertIsNone(cfg)
                self.assertIsNotNone(err)

    def test_userinfo_never_echoed_in_errors(self):
        # Distinctive values: if any error string echoed the
        # raw url, these would appear in it — and then in logs
        # and diagnostics downstream.
        for url in (
            "http://zz-user-9:zz-pass-9@example.com/mcp",
            "http://zz-user-9:zz-pass-9@localhost:8080/mcp",
        ):
            with self.subTest(url=url):
                cfg, err = _v(url)
                self.assertIsNone(cfg)
                self.assertNotIn("zz-user-9", err)
                self.assertNotIn("zz-pass-9", err)

    def test_empty_userinfo_carries_no_credentials(self):
        # The rule refuses NON-EMPTY userinfo; "http://@host"
        # carries no username and no password, so it is not
        # credentials — it is accepted, not refused.
        cfg, err = _v("http://@example.com/mcp")
        self.assertIsNone(err)
        self.assertIsNotNone(cfg)


class TestL1UrlStructure(unittest.TestCase):
    def test_missing_host_rejected(self):
        for url in (
            "https:///missing-host",
            "http://",
            "http:///path",
            "http://?query=1",
            "http:example.com",
        ):
            with self.subTest(url=url):
                cfg, err = _v(url)
                self.assertIsNone(cfg)
                self.assertIsNotNone(err)

    def test_invalid_port_rejected(self):
        for url in (
            "https://example.com:99999/mcp",
            "https://example.com:notaport/mcp",
            "http://example.com:65536/mcp",
        ):
            with self.subTest(url=url):
                cfg, err = _v(url)
                self.assertIsNone(cfg)
                self.assertIsNotNone(err)

    def test_uppercase_scheme_normalized_and_accepted(self):
        cfg, err = _v("HTTPS://Example.COM/mcp")
        self.assertIsNone(err)
        self.assertIsNotNone(cfg)
        # Scheme lowercased; everything else byte-identical.
        self.assertEqual(cfg["url"], "https://Example.COM/mcp")

    def test_ipv6_literals_accepted(self):
        for url in (
            "http://[::1]:8080/mcp",
            "https://[2001:db8::1]/mcp",
        ):
            with self.subTest(url=url):
                cfg, err = _v(url)
                self.assertIsNone(err)
                self.assertIsNotNone(cfg)
                self.assertEqual(cfg["url"], url)

    def test_valid_endpoints_accepted_unchanged(self):
        for url in (
            "https://srv.example/mcp",
            "https://srv.example:8443/a/b?x=1#frag",
            "http://localhost:8080/mcp",
            "http://srv.example/mcp",
        ):
            with self.subTest(url=url):
                cfg, err = _v(url)
                self.assertIsNone(err)
                self.assertIsNotNone(cfg)
                self.assertEqual(cfg["url"], url)

    def test_unsupported_scheme_rejected(self):
        for url in (
            "ftp://example.com/mcp",
            "example.com/mcp",
            "ws://example.com/mcp",
        ):
            with self.subTest(url=url):
                cfg, err = _v(url)
                self.assertIsNone(cfg)
                self.assertIsNotNone(err)

    def test_whitespace_rejected(self):
        for url in (
            "https://exa mple.com/mcp",
            " https://example.com/mcp",
            "https://example.com/mcp ",
            "https://example.com/mc\tp",
        ):
            with self.subTest(url=url):
                cfg, err = _v(url)
                self.assertIsNone(cfg)
                self.assertIsNotNone(err)


class TestL1GateCPreserved(unittest.TestCase):
    def test_http_auth_remote_still_hard_refused(self):
        cfg, err = _v("http://srv.example/mcp", auth=_auth())
        self.assertIsNone(cfg)
        self.assertIn("https", err)

    def test_http_auth_header_remote_still_hard_refused(self):
        cfg, err = _v(
            "http://srv.example/mcp",
            headers={"Authorization": "Bearer abc"},
        )
        self.assertIsNone(cfg)
        self.assertIsNotNone(err)

    def test_http_auth_loopback_still_allowed_with_warning(self):
        for url in (
            "http://127.0.0.1:8080/mcp",
            "http://localhost:8080/mcp",
            "http://[::1]:8080/mcp",
        ):
            with self.subTest(url=url):
                cfg, err = _v(url, auth=_auth())
                self.assertIsNone(err)
                self.assertIsNotNone(cfg)
                self.assertIn("cleartext_loopback_warning", cfg)

    def test_https_auth_unchanged(self):
        cfg, err = _v("https://srv.example/mcp", auth=_auth())
        self.assertIsNone(err)
        self.assertIsNotNone(cfg)
        self.assertNotIn("cleartext_loopback_warning", cfg)

    def test_http_no_credentials_unchanged(self):
        cfg, err = _v("http://srv.example/mcp")
        self.assertIsNone(err)
        self.assertIsNotNone(cfg)
        self.assertNotIn("cleartext_loopback_warning", cfg)

    def test_http_benign_header_only_unchanged(self):
        cfg, err = _v(
            "http://srv.example/mcp",
            headers={"Accept": "application/json"},
        )
        self.assertIsNone(err)
        self.assertIsNotNone(cfg)


if __name__ == "__main__":
    unittest.main()
