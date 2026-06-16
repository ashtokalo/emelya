"""Web fetch tool — fetch URL and return clean content."""

import json
import urllib.error
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


def execute(args: dict, ctx) -> str:
    url = args.get("url", "")
    max_length = args.get("max_length", DEFAULT_MAX_LENGTH)

    if not url:
        return "Error: url is required"

    if not url.startswith(("http://", "https://")):
        return "Error: url must start with http:// or https://"

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

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
