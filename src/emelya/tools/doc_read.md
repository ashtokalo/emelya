# doc_read

Read text content from office documents and PDFs.

## Parameters

- `path` (string, required) — Path to the document file.
- `offset` (int, optional) — Starting position (paragraph for docx/odt, page for PDF). Default: 0.
- `limit` (int, optional) — Number of items to return. Default: 50 for docx/odt, 1 page for PDF.

## Examples

doc_read(path="report.pdf")
doc_read(path="report.pdf", offset=5, limit=3)
doc_read(path="document.docx", offset=10, limit=20)

## Notes

- docx and odt files are read via the standard library.
- PDF files are read via `pdftotext`.
- Legacy .doc files are read via `catdoc`.
