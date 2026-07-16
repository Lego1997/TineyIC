"""Defense-in-depth checks for persona source URLs and artifact staging."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

import pytest

from tinyic.personas.factory import (
    Evidence,
    EvidenceLedger,
    SchemaValidationError,
    canonicalize_url,
    quality_for,
    validate_agent_spec,
    write_artifact_pair,
)
from tinyic.personas.factory.url_safety import public_host_is_allowed


_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "persona_factory"
    / "ada_value.agent.json"
)
_SENSITIVE_URLS = (
    "https://example.com/report.pdf?access_token=TEST_SECRET_DO_NOT_USE"
    "&X-Amz-Signature=SIGNED_VALUE",
    "https://example.com/report?year=1%3Baccess_token=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?access%255Ftoken=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?%D0%B0ccess_token=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?code=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?oauth_state=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?login_ticket=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?sessionKey=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?signingKey=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?encryptionKey=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?oauthNonce=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?authNonce=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?passwordHash=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?SAMLRequest=TEST_SECRET_DO_NOT_USE",
    "https://example.com/report?payload=%7B%22access_token%22%3A"
    "%22TEST_SECRET_DO_NOT_USE%22%7D",
    "https://example.com/report?next=http%3A%2F%2F127.0.0.1%2Fprivate",
    "https://example.com/report?next=ftp%3A%2F%2F127.0.0.1%2Fprivate",
    "https://example.com/report?next=https%3A%2F%2Fuser%3Apass%40example.org",
    r"https://example.com/report?next=\\127.0.0.1\private",
    "https://example.com/report?next=%5C%5C127.0.0.1%5Cprivate",
    "https://example.com/report;jsessionid=TEST_SECRET_DO_NOT_USE",
)

_NON_PUBLIC_OR_AMBIGUOUS_URLS = (
    "http://localhost/private",
    "http://sub.localhost/private",
    "http://127.0.0.1/private",
    "http://10.0.0.1/private",
    "http://169.254.169.254/latest/meta-data",
    "http://192.0.2.1/documentation",
    "http://224.0.0.1/multicast",
    "http://2130706433/private",
    "http://127.1/private",
    "http://0177.0.0.1/private",
    "http://0x7f000001/private",
    "http://%6cocalhost/private",
    "http://%31%32%37.0.0.1/private",
    "http://127。0。0。1/private",
    "http://ⓛocalhost/private",
    "http://intranet/private",
    "http://./private",
    "http://../private",
    "http://.../private",
    "http://example..org/private",
    "http://-example.org/private",
    "http://[::1]/private",
    "http://[::]/private",
    "http://[fe80::1]/private",
    "http://[ff02::1]/multicast",
)


@pytest.mark.parametrize("url", _SENSITIVE_URLS)
def test_canonicalizer_rejects_credential_bearing_query(url: str) -> None:
    with pytest.raises(ValueError, match="credential-like query"):
        canonicalize_url(url)


@pytest.mark.parametrize("url", _SENSITIVE_URLS)
def test_agent_schema_rejects_credential_bearing_source_url(url: str) -> None:
    specification = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    specification["tinyic"]["sources"][0]["url"] = url

    with pytest.raises(SchemaValidationError) as error:
        validate_agent_spec(specification)

    assert "tinyic.sources[0].url" in str(error.value)


@pytest.mark.parametrize("url", _NON_PUBLIC_OR_AMBIGUOUS_URLS)
def test_canonicalizer_rejects_non_public_or_ambiguous_host(url: str) -> None:
    with pytest.raises(ValueError, match="malformed|public"):
        canonicalize_url(url)


@pytest.mark.parametrize("url", _NON_PUBLIC_OR_AMBIGUOUS_URLS)
def test_agent_schema_rejects_non_public_or_ambiguous_host(url: str) -> None:
    specification = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    specification["tinyic"]["sources"][0]["url"] = url

    with pytest.raises(SchemaValidationError) as error:
        validate_agent_spec(specification)

    assert "tinyic.sources[0].url" in str(error.value)


def test_rejected_url_error_never_echoes_userinfo_secret() -> None:
    secret = "USERINFO_SECRET_DO_NOT_LOG"
    url = f"ftp://user:{secret}@journal.example/private"

    with pytest.raises(ValueError) as error:
        canonicalize_url(url)

    assert secret not in str(error.value)


def test_public_query_survives_while_tracking_and_fragment_are_removed() -> None:
    assert canonicalize_url(
        "https://Example.com/report/?year=2025&utm_source=test#section"
    ) == "https://example.com/report?year=2025"


@pytest.mark.parametrize(
    "payload",
    [
        r'{"p\u0061ssword":"ESCAPED_SECRET"}',
        r'{"access\u005ftoken":"ESCAPED_SECRET"}',
        r'{"p\x61ssword":"ESCAPED_SECRET"}',
        r'{"p%u0061ssword":"ESCAPED_SECRET"}',
        r'{"p\u005cu0061ssword":"ESCAPED_SECRET"}',
        json.dumps(
            {
                "wrapper": json.dumps(
                    {"access_token": "ESCAPED_SECRET"}
                )
            }
        ),
    ],
)
def test_canonicalizer_rejects_escaped_or_nested_json_secret_keys(
    payload: str,
) -> None:
    url = f"https://example.com/report?payload={quote(payload, safe='')}"

    with pytest.raises(ValueError, match="credential-like query") as error:
        canonicalize_url(url)

    assert "ESCAPED_SECRET" not in str(error.value)
    specification = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    specification["tinyic"]["sources"][0]["url"] = url
    with pytest.raises(SchemaValidationError):
        validate_agent_spec(specification)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/report?session%4Bey=ESCAPED_SECRET",
        "https://example.com/report?payload="
        + quote(
            r'{"outer":{"signing\u004bey":"ESCAPED_SECRET"}}',
            safe="",
        ),
        "https://example.com/report?payload="
        + quote(
            json.dumps(
                {
                    "wrapper": json.dumps(
                        {"passwordHash": "ESCAPED_SECRET"}
                    )
                }
            ),
            safe="",
        ),
        "https://example.com/report?next="
        + quote(
            "https://journal.example/callback?authNonce=ESCAPED_SECRET",
            safe="",
        ),
        "https://example.com/report?next="
        + quote(
            quote(
                "https://journal.example/callback?oauthNonce=ESCAPED_SECRET",
                safe="",
            ),
            safe="",
        ),
        "https://example.com/report?payload="
        + quote(
            r'{"SAML%u0052equest":"ESCAPED_SECRET"}',
            safe="",
        ),
    ],
)
def test_canonicalizer_rejects_decoded_recursive_compound_secret_keys(
    url: str,
) -> None:
    with pytest.raises(ValueError, match="credential-like query") as error:
        canonicalize_url(url)

    assert "ESCAPED_SECRET" not in str(error.value)
    specification = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    specification["tinyic"]["sources"][0]["url"] = url
    with pytest.raises(SchemaValidationError):
        validate_agent_spec(specification)


def test_canonicalizer_allows_benign_recursive_json_payload() -> None:
    payload = json.dumps(
        {
            "year": 2025,
            "client_id": "public-client",
            "nested": [
                {
                    "section": "valuation",
                    "monkey": "capuchin",
                    "tokenized": True,
                }
            ],
        }
    )
    url = f"https://example.com/report?payload={quote(payload, safe='')}"

    assert canonicalize_url(url).startswith("https://example.com/report?payload=")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a)[leak](https://user:PATH_SECRET@evil.example/x",
        "https://example.com/a%29%5Bleak%5D%28https%3A%2F%2F"
        "user%3APATH_SECRET%40evil.example%2Fx",
        "https://example.com/archive/https://127.0.0.1/private",
        "https://example.com/a%0AInjected",
    ],
)
def test_canonicalizer_rejects_unsafe_decoded_paths(url: str) -> None:
    with pytest.raises(ValueError, match="credential-like query") as error:
        canonicalize_url(url)

    assert "PATH_SECRET" not in str(error.value)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://ＥＸＡＭＰＬＥ.com/report",
            "https://example.com/report",
        ),
        (
            "https://𝐞𝐱𝐚𝐦𝐩𝐥𝐞.com/report",
            "https://example.com/report",
        ),
        (
            "https://BÜCHER.example/report",
            "https://xn--bcher-kva.example/report",
        ),
        (
            "https://XN--BCHER-KVA.example/report",
            "https://xn--bcher-kva.example/report",
        ),
        (
            "https://CAFÉ.example/report",
            "https://xn--caf-dma.example/report",
        ),
        (
            "https://cafe\u0301.example/report",
            "https://xn--caf-dma.example/report",
        ),
        (
            "https://οσ.example/report",
            "https://xn--0xai.example/report",
        ),
    ],
)
def test_canonicalizer_emits_nfkc_idna_ascii_host(
    url: str,
    expected: str,
) -> None:
    assert canonicalize_url(url) == expected


@pytest.mark.parametrize(
    ("unicode_host", "browser_ascii_host"),
    [
        ("faß.de", "xn--fa-hia.de"),
        ("straße.de", "xn--strae-oqa.de"),
        ("ος.example", "xn--0xag.example"),
    ],
)
def test_idna2003_non_round_trips_fail_closed_without_rewriting_origin(
    unicode_host: str,
    browser_ascii_host: str,
) -> None:
    assert not public_host_is_allowed(unicode_host)
    with pytest.raises(ValueError, match="host must be public"):
        canonicalize_url(f"https://{unicode_host}/report")
    assert canonicalize_url(
        f"https://{browser_ascii_host}/report"
    ) == f"https://{browser_ascii_host}/report"


def test_browser_distinct_ascii_domains_remain_distinct_at_quality_gate() -> None:
    ledger = EvidenceLedger()
    urls = (
        "https://fass.de/a",
        "https://xn--fa-hia.de/b",
        "https://xn--0xag.example/c",
        "https://xn--0xai.example/d",
    )
    for index, url in enumerate(urls, 1):
        ledger.add(Evidence(url, f"Source {index}", "Public excerpt"))

    assert ledger.domains == frozenset(
        {
            "fass.de",
            "xn--fa-hia.de",
            "xn--0xag.example",
            "xn--0xai.example",
        }
    )
    assert quality_for(ledger) == "thin"


def test_accented_unicode_and_ascii_punycode_dedupe_as_one_domain() -> None:
    ledger = EvidenceLedger()
    urls = (
        "https://café.example/report",
        "https://cafe\u0301.example/report",
        "https://xn--caf-dma.example/report",
    )

    for url in urls:
        assert ledger.add(Evidence(url, "Café", "Public excerpt")) == 1

    assert len(ledger.items) == 1
    assert ledger.domains == frozenset({"xn--caf-dma.example"})


@pytest.mark.parametrize(
    "host",
    [
        "www.wikipedia.org",
        "BÜCHER.example",
        "ｅｘａｍｐｌｅ.com",
        "8.8.8.8",
        "2606:4700:4700::1111",
    ],
)
def test_public_host_policy_allows_global_ip_and_ordinary_dns(host: str) -> None:
    assert public_host_is_allowed(host)


def test_canonicalizer_preserves_brackets_around_global_ipv6_literal() -> None:
    assert canonicalize_url(
        "https://[2606:4700:4700::1111]/dns-query"
    ) == "https://[2606:4700:4700::1111]/dns-query"


@pytest.mark.parametrize(
    "query",
    [
        "year=2025",
        "section=valuation",
        "monkey=capuchin",
        "turnkey=ready",
        "hockey=ice",
        "keyboard=qwerty",
        "hashbrown=crispy",
        "request_id=public",
        "nonceword=public",
        "passwordless=true",
        "sessionized=true",
        "signing=public",
        "encryption=public",
        "oauth_mode=public",
        "saml_version=2",
        "tokenized=true",
        "client_id=public-client",
    ],
)
def test_canonicalizer_preserves_benign_query_names(query: str) -> None:
    assert canonicalize_url(f"https://example.com/report?{query}").endswith(
        f"?{query}"
    )


def test_first_replace_post_success_failure_rolls_back_both_artifacts(
    tmp_path: Path,
) -> None:
    agent = tmp_path / "rollback.agent.json"
    dossier = tmp_path / "rollback.dossier.md"
    calls = 0

    def move_then_fail(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        os.replace(source, destination)
        if calls == 1:
            raise OSError("simulated post-success first rename failure")

    with pytest.raises(OSError, match="post-success first rename"):
        write_artifact_pair(
            agent,
            b"new agent",
            dossier,
            b"new dossier",
            force=False,
            replace_fn=move_then_fail,
        )

    assert not agent.exists()
    assert not dossier.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_restore_post_success_failure_still_restores_both_old_files(
    tmp_path: Path,
) -> None:
    agent = tmp_path / "rollback.agent.json"
    dossier = tmp_path / "rollback.dossier.md"
    agent.write_bytes(b"old agent")
    dossier.write_bytes(b"old dossier")
    calls = 0

    def move_then_fail_during_install_and_restore(
        source: Path, destination: Path
    ) -> None:
        nonlocal calls
        calls += 1
        os.replace(source, destination)
        if calls in {2, 3}:
            raise OSError(f"simulated post-success failure {calls}")

    with pytest.raises(OSError, match="post-success failure 2"):
        write_artifact_pair(
            agent,
            b"new agent",
            dossier,
            b"new dossier",
            force=True,
            replace_fn=move_then_fail_during_install_and_restore,
        )

    assert agent.read_bytes() == b"old agent"
    assert dossier.read_bytes() == b"old dossier"
    assert not list(tmp_path.glob("*.tmp"))
