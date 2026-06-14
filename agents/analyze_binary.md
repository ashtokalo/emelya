---
description: Analyze a binary file — detect format, extract structure, find strings and metadata
tools: glob, grep, read, bash, hex_read
max_steps: 8
---
Analyze the binary file `{path}`.

Follow this process:

1. Use bash to identify the file type:
   - `file {path}` — detect format
   - `ls -la {path}` — check size

2. Based on the file type, use appropriate analysis:

   **For ELF executables/libraries:**
   - `readelf -h {path}` — ELF header (arch, type, entry point)
   - `readelf -S {path}` — sections
   - `readelf -l {path}` — program headers / segments
   - `readelf -d {path}` — dynamic section (shared libs)
   - `readelf --syms {path} | head -50` — symbol table
   - `strings {path} | head -50` — readable strings

   **For archives (zip, tar, gz):**
   - For zip: `python3 -c "import zipfile; [print(i.filename, i.file_size) for i in zipfile.ZipFile('{path}').infolist()]"`
   - For tar: `tar -tf {path} | head -50`

   **For images:**
   - `identify {path}` (ImageMagick) or `file {path}`
   - `exiftool {path}` if available

   **For any binary:**
   - Use hex_read to inspect the first 256 bytes (magic bytes, headers)
   - `strings {path} | head -50` — find embedded text
   - `strings {path} | wc -l` — count readable strings

3. Use hex_read to examine interesting regions (headers, specific offsets found in step 2)

Return:
- File type and format details
- Size and structure overview
- Key metadata (for ELF: arch, sections, libs; for images: dimensions, format)
- Notable strings or embedded data
- Hex dump of the header (first 64-128 bytes)