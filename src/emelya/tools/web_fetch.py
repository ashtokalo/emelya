"""Web fetch tool — fetch URL and return clean content."""

import json
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "Fetch a URL and return its content. HTML is converted to plain text. JSON is pretty-printed.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Max response length in characters (default: 5000)",
                },
            },
            "required": ["url"],
        },
    },
}

MAX_RESPONSE_BYTES = 512 * 1024  # 512KB download limit
DEFAULT_MAX_LENGTH = 5000
USER_AGENT = "Emelya/1.0 (CLI AI Assistant)"

# Tags whose content should be skipped entirely
SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}


class _TextExtractor(HTMLParser):
    """Extract visible text from HTML."""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        if tag in ("br", "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                    "li", "tr", "dt", "dd", "blockquote", "section", "article"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                    "li", "table", "section", "article"):
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        # Collapse whitespace
        lines = []
        for line in text.splitlines():
            stripped = " ".join(line.split())
            if stripped:
                lines.append(stripped)
        return "\n".join(lines)


def _html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        pass
    return extractor.get_text()


_BLOCKED_NETWORKS = None

def _is_private_url(url: str) -> bool:
    """Check if URL points to a dangerous internal IP (SSRF protection).
    Blocks loopback, link-local, and RFC 1918 private ranges.
    Does NOT block all is_private (too broad — catches benchmarking ranges etc.)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    global _BLOCKED_NETWORKS
    if _BLOCKED_NETWORKS is None:
        _BLOCKED_NETWORKS = [
            ipaddress.ip_network("127.0.0.0/8"),       # loopback
            ipaddress.ip_network("10.0.0.0/8"),         # RFC 1918
            ipaddress.ip_network("172.16.0.0/12"),      # RFC 1918
            ipaddress.ip_network("192.168.0.0/16"),     # RFC 1918
            ipaddress.ip_network("169.254.0.0/16"),     # link-local (AWS metadata)
            ipaddress.ip_network("::1/128"),             # IPv6 loopback
            ipaddress.ip_network("fc00::/7"),            # IPv6 ULA
            ipaddress.ip_network("fe80::/10"),           # IPv6 link-local
        ]
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        ip_str = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)[0][4][0]
        ip = ipaddress.ip_address(ip_str)
        return any(ip in net for net in _BLOCKED_NETWORKS)
    except (socket.gaierror, ValueError, OSError, IndexError):
        return False


def execute(args: dict, ctx) -> str:
    url = args.get("url", "")
    max_length = args.get("max_length", DEFAULT_MAX_LENGTH)

    if not url:
        return "Error: url is required"

    if not url.startswith(("http://", "https://")):
        return "Error: url must start with http:// or https://"

    # SSRF protection — block private/internal IPs
    if _is_private_url(url):
        return (
            "Error: URL points to a private/internal address. "
            "web_fetch only allows public URLs. Use bash with curl for internal services."
        )

    # urllib requires an ASCII request target. Preserve URL delimiters and
    # existing percent escapes while encoding Unicode path/query characters.
    request_url = urllib.parse.quote(
        url, safe=":/?#[]@!$&'()*+,;=%",
    )
    req = urllib.request.Request(request_url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return f"Error: {e.reason}"
    except Exception as e:
        return f"Error: {e}"

    # Determine encoding
    encoding = "utf-8"
    if "charset=" in content_type:
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("charset="):
                encoding = part.split("=", 1)[1].strip()

    try:
        text = raw.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", errors="replace")

    # Process based on content type
    if "json" in content_type:
        try:
            data = json.loads(text)
            text = json.dumps(data, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    elif "html" in content_type or text.lstrip().startswith(("<!", "<html", "<HTML")):
        text = _html_to_text(text)

    # Truncate
    if len(text) > max_length:
        text = text[:max_length] + f"\n[truncated, {len(text)} chars total]"

    return text if text.strip() else "(empty response)"
