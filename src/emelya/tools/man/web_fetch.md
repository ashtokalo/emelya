# web_fetch

Fetch the content of a URL and return it as text.

## Parameters

- `url` (string, required) — The URL to fetch.
- `max_length` (int, optional) — Maximum number of characters to return. Default: 5000.

## Examples

web_fetch(url="https://example.com")
web_fetch(url="https://api.example.com/data.json", max_length=10000)

## Notes

- HTML pages are converted to plain text.
- JSON responses are pretty-printed.
- Maximum download size is 512KB.
