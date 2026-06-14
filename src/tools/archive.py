"""Archive tool — list, inspect and extract from archives."""

CATEGORY = "archives"

import io
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

DEFINITION = {
    "type": "function",
    "function": {
        "name": "archive",
        "description": (
            "Work with archives (zip, tar, gz, bz2, xz, 7z, rar, deb). "
            "Auto-detects format by magic bytes, not extension. "
            "Lists contents by default, extracts a specific entry if given."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Archive file path",
                },
                "entry": {
                    "type": "string",
                    "description": "Path inside archive to extract (omit to list all contents)",
                },
                "extract_to": {
                    "type": "string",
                    "description": "Directory to extract entry to (default: print content to output)",
                },
            },
            "required": ["path"],
        },
    },
}

# Magic bytes → format
_MAGIC = [
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),      # empty zip
    (b"\x1f\x8b", "gz"),
    (b"BZ", "bz2"),
    (b"\xfd7zXZ", "xz"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!\x1a\x07", "rar"),
    (b"!<arch>\n", "deb"),
]
_TAR_MAGIC_OFFSET = 257
_TAR_MAGIC = b"ustar"


def _detect_format(path: Path) -> str:
    """Detect archive format by magic bytes."""
    try:
        with open(path, "rb") as f:
            header = f.read(512)
    except OSError:
        return None

    for magic, fmt in _MAGIC:
        if header[:len(magic)] == magic:
            return fmt

    # tar has magic at offset 257
    if len(header) > 262 and header[_TAR_MAGIC_OFFSET:_TAR_MAGIC_OFFSET + 5] == _TAR_MAGIC:
        return "tar"

    return None


def _format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


# --- ZIP (also handles: apk, jar, epub, docx, xlsx, odt) ---

def _list_zip(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        lines = []
        total_size = 0
        for e in entries:
            total_size += e.file_size
            if e.is_dir():
                lines.append(f"  {e.filename}/")
            else:
                lines.append(f"  {e.filename}  ({_format_size(e.file_size)})")
        lines.append(f"[{len(entries)} entries, {_format_size(total_size)} uncompressed]")
        return "\n".join(lines)


def _extract_zip(path: Path, entry: str) -> bytes:
    with zipfile.ZipFile(path) as z:
        return z.read(entry)


# --- TAR (tar, tar.gz, tar.bz2, tar.xz) ---

def _list_tar(path: Path) -> str:
    with tarfile.open(path) as t:
        members = t.getmembers()
        lines = []
        total_size = 0
        for m in members:
            total_size += m.size
            if m.isdir():
                lines.append(f"  {m.name}/")
            else:
                lines.append(f"  {m.name}  ({_format_size(m.size)})")
        lines.append(f"[{len(members)} entries, {_format_size(total_size)} uncompressed]")
        return "\n".join(lines)


def _extract_tar(path: Path, entry: str) -> bytes:
    with tarfile.open(path) as t:
        f = t.extractfile(entry)
        if f is None:
            raise FileNotFoundError(f"'{entry}' is a directory or link, not a file")
        return f.read()


# --- GZ (single file) ---

def _list_gz(path: Path) -> str:
    import gzip
    # gz is a single compressed file — show compressed vs uncompressed size
    compressed = path.stat().st_size
    try:
        with gzip.open(path, "rb") as f:
            f.seek(0, 2)
            uncompressed = f.tell()
    except Exception:
        uncompressed = 0
    inner_name = path.stem  # foo.tar.gz → foo.tar
    return (
        f"  {inner_name}  ({_format_size(uncompressed)} uncompressed)\n"
        f"[gzip, {_format_size(compressed)} compressed]"
    )


def _extract_gz(path: Path, entry: str = None) -> bytes:
    import gzip
    with gzip.open(path, "rb") as f:
        return f.read()


# --- BZ2 (single file) ---

def _list_bz2(path: Path) -> str:
    import bz2
    compressed = path.stat().st_size
    try:
        data = bz2.open(path, "rb").read()
        uncompressed = len(data)
    except Exception:
        uncompressed = 0
    inner_name = path.stem
    return (
        f"  {inner_name}  ({_format_size(uncompressed)} uncompressed)\n"
        f"[bzip2, {_format_size(compressed)} compressed]"
    )


def _extract_bz2(path: Path, entry: str = None) -> bytes:
    import bz2
    with bz2.open(path, "rb") as f:
        return f.read()


# --- XZ (single file) ---

def _list_xz(path: Path) -> str:
    import lzma
    compressed = path.stat().st_size
    try:
        with lzma.open(path, "rb") as f:
            f.seek(0, 2)
            uncompressed = f.tell()
    except Exception:
        uncompressed = 0
    inner_name = path.stem
    return (
        f"  {inner_name}  ({_format_size(uncompressed)} uncompressed)\n"
        f"[xz, {_format_size(compressed)} compressed]"
    )


def _extract_xz(path: Path, entry: str = None) -> bytes:
    import lzma
    with lzma.open(path, "rb") as f:
        return f.read()


# --- External tools (7z, rar) ---

def _run_tool(args: list, timeout: int = 30) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout or r.stderr
    except FileNotFoundError:
        tool = args[0]
        return f"Error: '{tool}' not installed. Install: sudo apt install {tool}"
    except subprocess.TimeoutExpired:
        return f"Error: timed out"


def _list_7z(path: Path) -> str:
    return _run_tool(["7z", "l", str(path)])


def _extract_7z(path: Path, entry: str) -> bytes:
    # Extract to stdout
    r = subprocess.run(
        ["7z", "e", "-so", str(path), entry],
        capture_output=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace"))
    return r.stdout


def _list_rar(path: Path) -> str:
    return _run_tool(["unrar", "l", str(path)])


def _extract_rar(path: Path, entry: str) -> bytes:
    r = subprocess.run(
        ["unrar", "p", "-inul", str(path), entry],
        capture_output=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace"))
    return r.stdout


# --- DEB (ar archive with nested tar) ---

def _list_deb(path: Path) -> str:
    output = _run_tool(["ar", "t", str(path)])
    if output.startswith("Error:"):
        return output
    lines = [f"  {name.strip()}" for name in output.strip().split("\n") if name.strip()]
    lines.append(f"[deb package, {len(lines)} members]")
    # Also try dpkg-deb for metadata
    info = _run_tool(["dpkg-deb", "-I", str(path)])
    if not info.startswith("Error:"):
        # Extract key info
        for line in info.split("\n"):
            line = line.strip()
            if line.startswith(("Package:", "Version:", "Architecture:", "Description:")):
                lines.append(f"  {line}")
    return "\n".join(lines)


def _extract_deb(path: Path, entry: str) -> bytes:
    r = subprocess.run(
        ["ar", "p", str(path), entry],
        capture_output=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace"))
    return r.stdout


# --- Format dispatch ---

_HANDLERS = {
    "zip": (_list_zip, _extract_zip),
    "tar": (_list_tar, _extract_tar),
    "gz": (_list_gz, _extract_gz),
    "bz2": (_list_bz2, _extract_bz2),
    "xz": (_list_xz, _extract_xz),
    "7z": (_list_7z, _extract_7z),
    "rar": (_list_rar, _extract_rar),
    "deb": (_list_deb, _extract_deb),
}

# Known zip-based formats
_ZIP_EXTENSIONS = {".apk", ".jar", ".war", ".ear", ".epub", ".xpi", ".crx"}

MAX_EXTRACT_SIZE = 64 * 1024  # 64KB text output limit


def execute(args: dict, ctx) -> str:
    path = ctx.sandbox.validate(args["path"])
    entry = args.get("entry")
    extract_to = args.get("extract_to")

    if not path.exists():
        return f"Error: file not found: {args['path']}"

    # Detect format
    fmt = _detect_format(path)

    # Fallback: check extension for gz/bz2/xz wrapped tar
    if fmt in ("gz", "bz2", "xz"):
        suffixes = "".join(path.suffixes).lower()
        if ".tar." in suffixes:
            fmt = "tar"  # tarfile handles compressed tar automatically

    # Check known zip-based extensions
    if fmt is None and path.suffix.lower() in _ZIP_EXTENSIONS:
        fmt = "zip"

    if fmt is None:
        return (
            f"Error: cannot detect archive format for '{args['path']}'. "
            "Supported: zip, tar, gz, bz2, xz, 7z, rar, deb"
        )

    list_fn, extract_fn = _HANDLERS[fmt]
    size_str = _format_size(path.stat().st_size)
    header = f"[{fmt} archive, {size_str}]"

    if entry is None:
        # List contents
        try:
            listing = list_fn(path)
            return f"{header}\n{listing}"
        except Exception as e:
            return f"{header}\nError listing: {e}"

    # Extract specific entry
    try:
        data = extract_fn(path, entry)
    except (KeyError, FileNotFoundError) as e:
        return f"Error: entry not found: {entry}"
    except Exception as e:
        return f"Error extracting '{entry}': {e}"

    if extract_to:
        # Save to file
        out_path = ctx.sandbox.validate(extract_to)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        return f"Extracted '{entry}' to {extract_to} ({_format_size(len(data))})"

    # Return content
    # Try as text first
    try:
        text = data.decode("utf-8")
        if len(text) > MAX_EXTRACT_SIZE:
            text = text[:MAX_EXTRACT_SIZE] + f"\n[truncated, {_format_size(len(data))} total]"
        return text
    except UnicodeDecodeError:
        # Binary — show hex dump
        from tools.hex_read import _hex_dump
        preview = data[:256]
        dump = _hex_dump(preview)
        return f"[Binary content, {_format_size(len(data))}]\n{dump}"
