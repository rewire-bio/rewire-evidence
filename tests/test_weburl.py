"""The URL parser, held to what Node's `URL` does.

`tests/fixtures/js-semantics.json` holds the protocol `new URL(text)` finds for 1,600 inputs,
or null where it throws, and the host and hostname it reads out of those and 101 more.
`scripts/fixtures/record_js_fixture.mjs` records it with Node's built-ins.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import quote

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from rewire_evidence import js
from rewire_evidence.weburl import SPECIAL, Host, UndecidedHost, can_parse, host_of, protocol_of
from tests.recorded import SEMANTICS

URLS: list[tuple[str, str | None, bool]] = [
    (text, protocol, parses) for text, protocol, parses in SEMANTICS["url"]
]


HOSTS: list[tuple[str, dict[str, str] | None]] = [(text, held) for text, held in SEMANTICS["url_host"]]


def test_the_corpus_is_the_size_it_was_recorded_at() -> None:
    assert len(URLS) > 1600
    assert sum(protocol is not None for _, protocol, _ in URLS) > 400
    assert len(SEMANTICS["url_idna"]) == 9
    assert len(HOSTS) > 1800
    assert {text for text, _, _ in URLS} <= {text for text, _ in HOSTS}
    # Hosts of every kind that is written out: an IPv6 address, a file URL, and escapes.
    hostnames = [held["hostname"] for _, held in HOSTS if held]
    assert sum(name.startswith("[") for name in hostnames) > 50
    assert sum(held is not None for text, held in HOSTS if text.strip().lower().startswith("file:")) > 90
    assert sum("%" in name for name in hostnames) > 10


@pytest.mark.parametrize(("text", "held"), HOSTS, ids=[repr(text)[:40] for text, _ in HOSTS])
def test_a_host_is_read_as_node_reads_it(text: str, held: dict[str, str] | None) -> None:
    found = host_of(text)
    assert (None if found is None else found._asdict()) == held


@pytest.mark.parametrize(
    ("text", "protocol", "parses_trimmed"), URLS, ids=[repr(text)[:40] for text, _, _ in URLS]
)
def test_a_url_is_read_or_refused_as_node_reads_or_refuses_it(
    text: str, protocol: str | None, *, parses_trimmed: bool
) -> None:
    assert protocol_of(text) == protocol
    # zod's .url() asks the parser about the text once JavaScript has trimmed it.
    assert can_parse(js.trim(text)) is parses_trimmed


@pytest.mark.parametrize(("text", "protocol"), SEMANTICS["url_idna"])
def test_a_host_only_idna_can_judge_is_not_judged(text: str, protocol: str | None) -> None:
    del protocol
    with pytest.raises(UndecidedHost, match="needs IDNA mapping"):
        protocol_of(text)
    with pytest.raises(UndecidedHost, match="needs IDNA mapping"):
        host_of(text)


labels = st.from_regex(r"[a-z][a-z0-9-]{0,8}", fullmatch=True)
hosts = st.lists(labels, min_size=1, max_size=4).map(".".join).filter(lambda host: "xn--" not in host)
paths = st.from_regex(r"(/[A-Za-z0-9._~-]{0,6}){0,3}", fullmatch=True)


@given(st.sampled_from(sorted(SPECIAL - {"file"})), hosts, st.integers(0, 65535) | st.none(), paths)
def test_a_url_with_a_scheme_a_named_host_and_a_port_in_range_parses(
    scheme: str, host: str, port: int | None, path: str
) -> None:
    text = f"{scheme}://{host}{'' if port is None else f':{port}'}{path}"
    assert protocol_of(text) == f"{scheme}:"
    assert protocol_of(text.upper()) == f"{scheme}:"
    assert protocol_of(f" \t{text}\n ") == f"{scheme}:"


@given(hosts, st.integers(65536, 10**9))
def test_a_port_out_of_range_is_refused(host: str, port: int) -> None:
    assert protocol_of(f"https://{host}:{port}/") is None


@given(hosts, st.integers(0, 6000), st.integers(0, 10**7))
def test_a_port_is_the_number_its_digits_write_however_many_zeros_come_first(
    host: str, zeros: int, port: int
) -> None:
    # Python refuses to read an integer of more than 4,300 digits, and a port may have more.
    text = f"https://{host}:{'0' * zeros}{port}/"
    assert protocol_of(text) == ("https:" if port <= 65535 else None)


@given(st.integers(0, 3), st.integers(1, 6000), st.sampled_from("123456789"))
def test_an_address_with_a_part_too_long_to_read_is_refused(parts: int, digits: int, digit: str) -> None:
    long = digit * (digits + 10)
    assert protocol_of(f"http://{'1.' * parts}{long}/") is None
    assert protocol_of(f"http://{long}{'.1' * parts}/") is None


@given(st.text())
def test_what_parses_has_a_protocol_and_what_does_not_has_none(text: str) -> None:
    try:
        protocol = protocol_of(text)
    except UndecidedHost:
        with pytest.raises(UndecidedHost):
            host_of(text)
        return
    assert can_parse(text) is (protocol is not None)
    assert protocol is None or protocol.endswith(":")
    # A host is read out of exactly the text that parses.
    assert (host_of(text) is None) is (protocol is None)


pieces = st.lists(st.sampled_from([0, 0, 0, 1, 0xFFFF]) | st.integers(0, 0xFFFF), min_size=8, max_size=8)


@given(pieces, st.sampled_from(["https", "ws", "foo", "file"]), st.integers(1, 65535) | st.none())
def test_an_ipv6_address_is_written_as_rfc_5952_writes_it(
    held: list[int], scheme: str, port: int | None
) -> None:
    # The standard's serialiser and RFC 5952 choose the same run of zeros, which is the one
    # Python's `ipaddress` compresses. An address that maps an IPv4 one is left out, because
    # later versions of Python write its last two pieces as that address and the standard
    # does not.
    address = ipaddress.IPv6Address(b"".join(piece.to_bytes(2, "big") for piece in held))
    assume(address.ipv4_mapped is None)
    name = f"[{address.compressed}]"
    port = None if scheme == "file" else port
    host = (
        name
        if port in {None, 80 if scheme == "ws" else 443 if scheme == "https" else None}
        else f"{name}:{port}"
    )
    after = "" if port is None else f":{port}"
    for written in (":".join(f"{piece:X}" for piece in held), address.compressed, address.exploded):
        assert host_of(f"{scheme}://[{written}]{after}/a") == Host(host, name)
        assert protocol_of(f"{scheme}://[{written}]{after}/a") == f"{scheme}:"


opaque = st.text(
    st.characters(exclude_categories=["Cs"], exclude_characters="\x00\t\n\r #/:<>?@[\\]^|"),
    min_size=1,
    max_size=12,
)


@given(opaque)
def test_a_host_of_a_scheme_that_is_not_special_keeps_what_was_written_with_escapes(host: str) -> None:
    printable = "".join(chr(code) for code in range(0x21, 0x7F))
    name = quote(host, safe=printable).lower()
    assert host_of(f"s3://{host}/key") == Host(name, name)
    assert host_of(f"s3://{host}:9000/key") == Host(f"{name}:9000", name)


@given(st.text(alphabet="htps:/@[].0123456789abcdefx%-_ \\#?", max_size=24))
def test_a_host_that_is_ascii_is_always_judged(text: str) -> None:
    assert protocol_of(text) is None or protocol_of(text) == protocol_of(text.strip())
