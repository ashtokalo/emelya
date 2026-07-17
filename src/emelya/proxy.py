"""Proxy support for LLMClient — HTTP and SOCKS5 (stdlib only, no dependencies)."""

import socket
import ssl
import urllib.parse
import urllib.request


def build_proxy_opener(proxy_url: str):
    """Build a urllib opener that routes traffic through the given proxy.

    Supported schemes:
      http://[user:pass@]host:port   — HTTP CONNECT proxy
      https://[user:pass@]host:port  — HTTPS proxy (treated as HTTP CONNECT)
      socks5://[user:pass@]host:port — SOCKS5 proxy (RFC 1928 + 1929)

    Returns an OpenerDirector; use opener.open(req, timeout=...) instead of
    urllib.request.urlopen().  Raises ValueError for unsupported schemes.
    """
    scheme, proxy_host, proxy_port, username, password = _parse_proxy_url(proxy_url)

    if scheme in ("http", "https"):
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        return urllib.request.build_opener(handler)

    if scheme in ("socks5", "socks5h"):
        # Both variants do remote DNS: we always send the hostname to the proxy
        # (DOMAINNAME address type \x03), so local resolution never happens.
        return _build_socks5_opener(proxy_host, proxy_port, username, password)

    raise ValueError(f"Unsupported proxy scheme: {scheme!r} (supported: http, socks5, socks5h)")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_proxy_url(url: str):
    """Parse proxy URL → (scheme, host, port, username, password)."""
    p = urllib.parse.urlparse(url)
    scheme = p.scheme.lower()
    host = p.hostname
    port = p.port or (1080 if scheme.startswith("socks") else 8080)
    return scheme, host, port, p.username, p.password


def _recv_exact(sock, n: int) -> bytes:
    """Receive exactly n bytes from socket."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("SOCKS5: connection closed unexpectedly")
        buf += chunk
    return buf


def _socks5_connect(sock, proxy_host, proxy_port,
                    target_host: str, target_port: int,
                    username: str = None, password: str = None):
    """Perform SOCKS5 handshake on a socket already connected to the proxy.

    After return the socket is ready for traffic to target_host:target_port.
    Raises OSError on any protocol or auth failure.
    """
    host_b = target_host.encode("idna")

    # Greeting: offer user/pass auth if credentials given, else no-auth
    if username and password:
        sock.sendall(b"\x05\x01\x02")       # SOCKS5, 1 method: user/pass
    else:
        sock.sendall(b"\x05\x01\x00")       # SOCKS5, 1 method: no auth

    resp = _recv_exact(sock, 2)
    if resp[0] != 0x05:
        raise OSError("SOCKS5: unexpected server greeting")
    if resp[1] == 0xFF:
        raise OSError("SOCKS5: no acceptable authentication method")

    # Username/password sub-negotiation (RFC 1929)
    if resp[1] == 0x02:
        if not (username and password):
            raise OSError("SOCKS5: server requires authentication")
        user_b = username.encode()
        pass_b = password.encode()
        sock.sendall(b"\x01"
                     + bytes([len(user_b)]) + user_b
                     + bytes([len(pass_b)]) + pass_b)
        auth = _recv_exact(sock, 2)
        if auth[1] != 0x00:
            raise OSError("SOCKS5: authentication failed (wrong credentials)")

    # CONNECT request: SOCKS5, CONNECT, reserved, DOMAINNAME, host, port
    sock.sendall(b"\x05\x01\x00\x03"
                 + bytes([len(host_b)]) + host_b
                 + target_port.to_bytes(2, "big"))

    # Response
    hdr = _recv_exact(sock, 4)
    if hdr[0] != 0x05:
        raise OSError("SOCKS5: invalid response version")
    if hdr[1] != 0x00:
        _ERRORS = {
            0x01: "general failure", 0x02: "connection not allowed",
            0x03: "network unreachable", 0x04: "host unreachable",
            0x05: "connection refused", 0x06: "TTL expired",
            0x07: "command not supported", 0x08: "address type not supported",
        }
        raise OSError(f"SOCKS5: {_ERRORS.get(hdr[1], f'error {hdr[1]:#04x}')}")

    # Discard bound address (we don't use it)
    atype = hdr[3]
    if atype == 0x01:                        # IPv4
        _recv_exact(sock, 4 + 2)
    elif atype == 0x03:                      # domain
        _recv_exact(sock, _recv_exact(sock, 1)[0] + 2)
    elif atype == 0x04:                      # IPv6
        _recv_exact(sock, 16 + 2)


def _build_socks5_opener(proxy_host, proxy_port, username, password):
    """Build urllib opener that tunnels connections through a SOCKS5 proxy."""
    import http.client as _http

    class _SocksHTTPSConn(_http.HTTPSConnection):
        def connect(self_c):
            sock = socket.create_connection(
                (proxy_host, proxy_port), timeout=self_c.timeout)
            _socks5_connect(sock, proxy_host, proxy_port,
                            self_c.host, self_c.port or 443,
                            username=username, password=password)
            ctx = ssl.create_default_context()
            self_c.sock = ctx.wrap_socket(sock, server_hostname=self_c.host)

    class _SocksHTTPConn(_http.HTTPConnection):
        def connect(self_c):
            sock = socket.create_connection(
                (proxy_host, proxy_port), timeout=self_c.timeout)
            _socks5_connect(sock, proxy_host, proxy_port,
                            self_c.host, self_c.port or 80,
                            username=username, password=password)
            self_c.sock = sock

    class _SocksHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self_h, req):
            return self_h.do_open(_SocksHTTPSConn, req)

    class _SocksHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self_h, req):
            return self_h.do_open(_SocksHTTPConn, req)

    return urllib.request.build_opener(_SocksHTTPSHandler(), _SocksHTTPHandler())
