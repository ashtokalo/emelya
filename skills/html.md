---
description: HTML — validation, structure, previewing, accessibility
---
# HTML

## Previewing

Start a local server to preview HTML files in a browser:
- `python3 -m http.server 8000` — serve current directory on port 8000
- `python3 -m http.server 8000 -d ./public` — serve specific directory
- `npx serve .` — alternative if Node.js is available

## Validation and checking

- Check document structure with read tool: look for `<!DOCTYPE html>`, `<html lang>`, `<head>`, `<meta charset>`, `<title>`, `<body>`
- `grep -rn 'href=' *.html` — find all links (use bash tool)
- `curl -sI <url>` — check if a linked resource is reachable

## Common patterns

When editing HTML with edit tool, ensure:
- Every opened tag has a matching close (except void elements: `<img>`, `<br>`, `<input>`, `<meta>`, `<link>`)
- `id` attributes are unique within the document
- `<img>` tags have `alt` attributes
- Forms have `<label>` elements associated with inputs via `for`/`id`

## Accessibility basics

Check with read tool:
- `<html lang="...">` is set
- Images have descriptive `alt` text (empty `alt=""` only for decorative images)
- Headings follow hierarchy (`h1` > `h2` > `h3`, no skipping)
- Interactive elements are keyboard-accessible (native `<button>`, `<a>`, `<input>`)
- Color is not the only way to convey information

## Link checking

Use bash tool to verify links are not broken:
```
grep -oP 'href="\K[^"]+' index.html | while read url; do
  curl -sI "$url" | head -1
done
```

Use read/edit tools for inspecting and modifying HTML files.