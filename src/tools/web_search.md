# web_search

Search the web via DuckDuckGo. Returns titles, URLs and snippets.

## Parameters

- `query` (string, required) — Search query.
- `max_results` (int, optional) — Maximum results to return. Default: 5.

## Examples

web_search(query="python telegram bot tutorial")
web_search(query="llama.cpp tool calling", max_results=3)

## Notes

- Uses DuckDuckGo HTML lite — no API key needed.
- Returns title, URL, and snippet for each result.
- Use web_fetch to read a specific URL from results.
