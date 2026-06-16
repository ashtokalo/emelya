"""Web search tool — search via DuckDuckGo HTML lite."""

import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web. Returns titles, URLs and snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max results (default: 5)",
                },
            },
            "required": ["query"],
        },
    },
}

USER_AGENT = "Emelya/1.0 (CLI AI Assistant)"
DDG_URL = "https://html.duckduckgo.com/html/"


class _DDGParser(HTMLParser):
    """Parse DuckDuckGo HTML lite search results."""

    def __init__(self):
        super().__init__()
        self.results = []
        self._current = None
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")

        # Result link: <a class="result__a" href="...">
        if tag == "a" and "result__a" in cls:
            href = attrs_dict.get("href", "")
            # DDG wraps URLs in redirect; extract actual URL
            if "uddg=" in href:
                try:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = parsed.get("uddg", [href])[0]
                except Exception:
                    pass
            self._current = {"title": "", "url": href, "snippet": ""}
            self._in_title = True

        # Snippet: <a class="result__snippet" ...>
        if tag == "a" and "result__snippet" in cls:
            self._in_snippet = True

    def handle_endtag(self, tag):
        if tag == "a":
            if self._in_title:
                self._in_title = False
            if self._in_snippet:
                self._in_snippet = False
                if self._current:
                    self.results.append(self._current)
                    self._current = None

    def handle_data(self, data):
        if self._in_title and self._current:
            self._current["title"] += data.strip()
        if self._in_snippet and self._current:
            self._current["snippet"] += data.strip()


def execute(args: dict, ctx) -> str:
    query = args.get("query", "")
    max_results = args.get("max_results", 5)

    if not query:
        return "Error: query is required"

    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    req = urllib.request.Request(
        DDG_URL,
        data=data,
        headers={"User-Agent": USER_AGENT},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code}"
    except Exception as e:
        return f"Error: {e}"

    parser = _DDGParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    results = parser.results[:max_results]

    if not results:
        return "No results found"

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
        lines.append("")

    return "\n".join(lines).strip()
