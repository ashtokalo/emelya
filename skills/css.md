---
description: CSS — inspecting styles, layout debugging, responsive design, minification
---
# CSS

## Inspecting styles

Use read tool to examine CSS files. Common things to look for:
- Unused selectors or duplicate rules
- Specificity conflicts (overly specific selectors vs. simpler ones)
- `!important` overuse — usually a sign of specificity issues
- Media queries and breakpoints

Use grep to find specific rules:
- Search for a selector: search for `.classname` or `#id` across CSS files
- Find all media queries: search for `@media` in CSS files

## Debugging layout issues

Common layout problems and what to check with read/edit tools:
- **Overflow**: look for fixed widths, missing `overflow` handling, `box-sizing` not set to `border-box`
- **Flexbox**: check `display: flex`, `justify-content`, `align-items`, `flex-wrap`
- **Grid**: check `display: grid`, `grid-template-columns`, `gap`
- **Centering**: `margin: 0 auto` (block), `text-align: center` (inline), flexbox for both axes
- **Collapsing margins**: adjacent vertical margins collapse; use padding or gap instead

## Responsive design checks

Look for with read tool:
- `<meta name="viewport" content="width=device-width, initial-scale=1">` in HTML
- Mobile-first media queries: `@media (min-width: ...)` preferred over `max-width`
- Relative units (`rem`, `em`, `%`, `vw/vh`) vs. fixed `px`
- `max-width` on containers to prevent overly wide content

## Minification

Use bash tool:
- `npx clean-css-cli -o style.min.css style.css` — minify with clean-css
- `npx cssnano style.css style.min.css` — minify with cssnano
- For quick removal of comments and whitespace without tools: use Python or sed via bash

## File organization

Common patterns to recognize:
- Single file: `style.css` or `styles.css`
- Per-component: `components/button.css`, `components/card.css`
- CSS modules: `*.module.css` (React/Vue scoped styles)
- Preprocessors: `*.scss`, `*.less` — compiled to CSS

Use read/edit tools for file inspection and modifications. Use bash tool for all commands.