"""Document reader — read text documents (docx, odt, pdf, doc, rtf) with pagination."""

CATEGORY = "documents"

import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

DEFINITION = {
    "type": "function",
    "function": {
        "name": "doc_read",
        "description": (
            "Read a text document (docx, odt, pdf, doc, rtf). "
            "Returns metadata on first call, supports pagination for large documents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path",
                },
                "offset": {
                    "type": "integer",
                    "description": "Start from this paragraph/line (0-based, default: 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of paragraphs/lines to return (default: 50)",
                },
            },
            "required": ["path"],
        },
    },
}

# XML namespaces
_DOCX_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_ODT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _read_docx(path: Path) -> list:
    """Read docx paragraphs using stdlib zipfile + xml."""
    paragraphs = []
    try:
        with zipfile.ZipFile(path) as z:
            xml_data = z.read("word/document.xml")
        tree = ElementTree.fromstring(xml_data)
        for p in tree.iter(f"{{{_DOCX_NS}}}p"):
            texts = []
            for t in p.iter(f"{{{_DOCX_NS}}}t"):
                if t.text:
                    texts.append(t.text)
            line = "".join(texts).strip()
            if line:
                paragraphs.append(line)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        return [f"Error reading docx: {e}"]
    return paragraphs


def _read_odt(path: Path) -> list:
    """Read odt paragraphs using stdlib zipfile + xml."""
    paragraphs = []
    try:
        with zipfile.ZipFile(path) as z:
            xml_data = z.read("content.xml")
        tree = ElementTree.fromstring(xml_data)
        for p in tree.iter(f"{{{_ODT_NS}}}p"):
            text = "".join(p.itertext()).strip()
            if text:
                paragraphs.append(text)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        return [f"Error reading odt: {e}"]
    return paragraphs


def _read_via_tool(path: Path, tool: str, tool_args: list) -> list:
    """Read document via external CLI tool."""
    try:
        result = subprocess.run(
            [tool] + tool_args + [str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [line for line in result.stdout.splitlines() if line.strip()]
        if result.stderr:
            return [f"Error: {result.stderr.strip()}"]
    except FileNotFoundError:
        return None  # tool not installed
    except subprocess.TimeoutExpired:
        return [f"Error: {tool} timed out"]
    return [f"Error: {tool} returned no output"]


def _read_pdf(path: Path) -> list:
    """Read PDF via pdftotext (poppler-utils)."""
    lines = _read_via_tool(path, "pdftotext", ["-layout", "-"])
    if lines is None:
        # Try python-based fallback — read as text with basic extraction
        lines = _read_via_tool(path, "python3", [
            "-c",
            f"import subprocess; subprocess.run(['strings', '{path}'], timeout=10)",
        ])
        if lines is None:
            return [
                "Error: cannot read PDF — pdftotext not installed.",
                "Install: sudo apt install poppler-utils",
            ]
    return lines


def _read_doc(path: Path) -> list:
    """Read DOC via catdoc or antiword."""
    for tool in ("catdoc", "antiword"):
        lines = _read_via_tool(path, tool, [])
        if lines is not None:
            return lines
    return [
        "Error: cannot read DOC — catdoc/antiword not installed.",
        "Install: sudo apt install catdoc",
    ]


def _read_rtf(path: Path) -> list:
    """Read RTF via unrtf."""
    lines = _read_via_tool(path, "unrtf", ["--text"])
    if lines is None:
        return [
            "Error: cannot read RTF — unrtf not installed.",
            "Install: sudo apt install unrtf",
        ]
    # unrtf adds header noise, skip until first empty line
    started = False
    clean = []
    for line in lines:
        if not started:
            if line.startswith("-"):
                started = True
            continue
        if line.strip():
            clean.append(line)
    return clean or lines


READERS = {
    ".docx": _read_docx,
    ".odt": _read_odt,
    ".pdf": _read_pdf,
    ".doc": _read_doc,
    ".rtf": _read_rtf,
}


def execute(args: dict, ctx) -> str:
    path = ctx.sandbox.validate(args["path"])
    offset = args.get("offset", 0)
    limit = args.get("limit", 50)

    if not path.exists():
        return f"Error: file not found: {args['path']}"

    ext = path.suffix.lower()
    reader = READERS.get(ext)
    if not reader:
        supported = ", ".join(READERS.keys())
        return f"Error: unsupported format '{ext}'. Supported: {supported}"

    paragraphs = reader(path)
    total = len(paragraphs)

    if total == 0:
        return "Document is empty"

    # Metadata header
    size_kb = path.stat().st_size / 1024
    header = f"[{ext} document, {size_kb:.0f}KB, {total} paragraphs]"

    # Paginate
    end = min(offset + limit, total)
    if offset >= total:
        return f"{header}\nError: offset {offset} is beyond end ({total} paragraphs)"

    lines = []
    lines.append(header)
    for i in range(offset, end):
        lines.append(f"{i + 1}. {paragraphs[i]}")
    lines.append(f"[Showing {offset + 1}-{end} of {total}]")

    return "\n".join(lines)
