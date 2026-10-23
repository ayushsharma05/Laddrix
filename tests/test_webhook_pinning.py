"""A user's webhook connects only to the address that was checked (#271).

check: hooks.example.com ─► 93.184.216.34 (public) ─► connect to 93.184.216.34, TLS name
       hooks.example.com. A second lookup that answers 10.0.0.5 is never connected to.
"""

import asyncio
import ssl
from datetime import UTC, datetime

import httpx
import pytest
from test_market_alerts import sender_with

from mocktrade.alerts import webhook
from mocktrade.alerts.webhook import (
    MAX_IN_FLIGHT,
    MAX_PENDING,
    WebhookError,
    WebhookSender,
    check_url,
    pinned,
    public,
)
from mocktrade.ops.alerts import Alert
from mocktrade.ops.notify import WebhookNotifier

URL = "https://hooks.example.com/in"


async def test_the_connection_goes_to_the_checked_address_with_the_real_name_for_tls():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    sender, _ = sender_with(handler)
    assert await sender.deliver_now(URL, "s", {}) == ""
    (r,) = seen
    assert r.url.host == "93.184.216.34" and r.url.path == "/in"  # the address checked
    assert r.headers["host"] == "hooks.example.com"
    assert r.extensions["sni_hostname"] == "hooks.example.com"  # the certificate's name
    # a port and an IPv6 address are kept right
    assert pinned("https://h.example.com:8443/a?b=1", "2606:4700::1") == (
        "https://[2606:4700::1]:8443/a?b=1", {"host": "h.example.com:8443"},
        {"sni_hostname": "h.example.com"})  # fmt: skip
    await sender.aclose()


async def test_a_host_that_turns_private_between_tries_is_never_reached():
    answers = iter([["93.184.216.34"], ["10.0.0.5"]])  # public to the check, then private
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(503)  # so a second try happens

    sender, waits = sender_with(handler)

    async def rebinding(_host):
        return next(answers)

    sender._resolver = rebinding
    assert await sender.deliver_now(URL, "s", {}) == "the webhook host is not a public address"
    assert seen == ["93.184.216.34"] and waits == [webhook.BACKOFF_S[0]]
    await sender.aclose()


@pytest.mark.parametrize("address", ["::ffff:10.0.0.1", "::ffff:127.0.0.1", "fe80::1%eth0",
                                     "0.0.0.0", "100.64.0.1", "224.0.0.1", "ff02::1", "::1",
                                     "ff0e::1", "169.254.169.254", "fd00::1"])  # fmt: skip
async def test_every_private_spelling_of_an_address_is_refused(address):
    assert not public(address)

    def never(_request):
        raise AssertionError("must not connect")

    sender, _ = sender_with(never, resolved=("93.184.216.34", address))  # one bad answer: no
    assert await sender.deliver_now(URL, "s", {}) == "the webhook host is not a public address"
    await sender.aclose()


def test_written_private_addresses_are_refused_up_front():
    for url in ("https://[::ffff:127.0.0.1]/x", "https://[fe80::1]/x", "https://0.0.0.0/x",
                "https://100.64.0.1/x", "https://[ff0e::1]/x",  # global multicast: no host
                "https://233.252.0.1/x"):  # fmt: skip
        with pytest.raises(WebhookError, match="public"):
            check_url(url)
    assert public("93.184.216.34") and public("2606:4700::1")


async def test_a_lookup_that_returns_nonsense_is_refused():
    sender, _ = sender_with(lambda r: httpx.Response(200), resolved=("not-an-address",))
    assert "isn't one" in await sender.deliver_now(URL, "s", {})
    await sender.aclose()


async def test_a_redirect_is_not_followed():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://10.0.0.5/steal"})

    sender, waits = sender_with(handler)
    assert await sender.deliver_now(URL, "s", {}) == "refused: answered 302"
    assert len(seen) == 1 and waits == []
    await sender.aclose()


async def test_the_real_client_checks_certificates_and_keeps_no_connections():
    sender = WebhookSender()
    pool = sender._client._transport._pool
    context = pool._ssl_context
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert pool._max_keepalive_connections == 0  # never reused for another host name
    assert sender._client.follow_redirects is False and sender._client._trust_env is False
    await sender.aclose()


async def test_direct_and_queued_sends_stay_bounded():
    gate = asyncio.Event()
    now = {"open": 0, "most": 0}

    async def slow(_request):
        now["open"] += 1
        now["most"] = max(now["most"], now["open"])
        await gate.wait()
        now["open"] -= 1
        return httpx.Response(200)

    sender, _ = sender_with(slow)
    results = []
    for _ in range(MAX_PENDING):
        sender.send(URL, "s", {}, results.append)
    await asyncio.sleep(0.05)
    assert now["most"] == MAX_IN_FLIGHT  # connections at once, however many wait
    assert await sender.deliver_now(URL, "s", {}) == ("too many webhook sends waiting: try "
                                                      "again shortly")  # fmt: skip
    gate.set()
    await sender.aclose()
    assert results.count("") == MAX_PENDING and now["most"] == MAX_IN_FLIGHT


async def test_the_operators_alert_webhook_never_piles_up():
    from mocktrade.ops import notify

    gate = asyncio.Event()
    calls = []

    async def slow(_request):
        calls.append(1)
        await gate.wait()
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    n = WebhookNotifier("https://ops.example.com/x", client=client)
    for i in range(notify.MAX_PENDING + 5):
        n(Alert(f"k{i}", "ERROR", "down", True, datetime.now(UTC)))
    assert len(n._tasks) == notify.MAX_PENDING
    gate.set()
    await n.aclose()
    assert len(calls) == notify.MAX_PENDING


def _certificates(tmp_path, name):
    """A throwaway CA and a server certificate for `name` (files), and a context trusting it."""
    from datetime import timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    def make(subject, issuer, key, signer, *, ca, san=None):
        now = datetime.now(UTC)
        b = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
             .public_key(key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now - timedelta(hours=1)).not_valid_after(now + timedelta(hours=1))
             .add_extension(x509.BasicConstraints(ca=ca, path_length=None),
                            critical=True))  # fmt: skip
        b = b.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                            critical=False).add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()),
            critical=False)  # fmt: skip
        if ca:
            b = b.add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False,
                                              False), critical=True)  # fmt: skip
        if san:
            b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
        return b.sign(signer, hashes.SHA256())

    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test CA")])
    ca = make(ca_name, ca_name, ca_key, ca_key, ca=True)
    leaf = make(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]), ca_name, key, ca_key,
                ca=False, san=name)  # fmt: skip
    pem = serialization.Encoding.PEM
    (tmp_path / "cert.pem").write_bytes(leaf.public_bytes(pem))
    (tmp_path / "key.pem").write_bytes(key.private_bytes(
        pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))  # fmt: skip
    trust = ssl.create_default_context(cadata=ca.public_bytes(pem).decode())
    return trust


async def test_tls_is_checked_against_the_host_name_not_the_address(tmp_path):
    trust = _certificates(tmp_path, "hooks.test")
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    hosts = []

    async def answer(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        hosts.append(next(h.split(":", 1)[1].strip() for h in head.split("\r\n")
                          if h.lower().startswith("host:")))  # fmt: skip
        length = next((int(h.split(":")[1]) for h in head.split("\r\n")
                       if h.lower().startswith("content-length:")), 0)  # fmt: skip
        await reader.readexactly(length)
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0, ssl=server_tls)
    port = server.sockets[0].getsockname()[1]
    client = httpx.AsyncClient(verify=trust, trust_env=False,  # as the real client: no proxy
                               limits=httpx.Limits(max_keepalive_connections=0))  # fmt: skip
    sender = WebhookSender(client=client)
    try:
        # the address is 127.0.0.1, the name on the certificate hooks.test: accepted
        assert await sender._post(f"https://hooks.test:{port}/in", "s", b"{}", "127.0.0.1") is None
        assert hosts == [f"hooks.test:{port}"]
        # the same address under another name: the certificate doesn't match, not sent
        wrong = await sender._post(f"https://other.test:{port}/in", "s", b"{}", "127.0.0.1")
        assert wrong == "not reached: ConnectError" and len(hosts) == 1
    finally:
        await sender.aclose()
        server.close()
        await server.wait_closed()


async def test_a_test_send_under_way_counts_as_waiting():
    gate = asyncio.Event()

    async def slow(_request):
        await gate.wait()
        return httpx.Response(200)

    sender, _ = sender_with(slow)
    results = []
    for _ in range(MAX_PENDING - 1):
        sender.send(URL, "s", {}, results.append)
    direct = asyncio.ensure_future(sender.deliver_now(URL, "s", {}))  # the last free place
    await asyncio.sleep(0)
    sender.send(URL, "s", {}, results.append)
    assert results == ["too many webhook sends waiting: this one was dropped"]
    gate.set()
    assert await direct == ""
    await sender.aclose()
