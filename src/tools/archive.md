# archive

List, inspect, or extract archive contents.

## Parameters

- `path` (string, required) — Path to the archive file.
- `entry` (string, optional) — Specific file within the archive to extract.
- `extract_to` (string, optional) — Directory to extract contents into.

## Examples

archive(path="backup.zip")
archive(path="data.tar.gz", entry="config.yaml")
archive(path="release.zip", extract_to="/tmp/unpacked")

## Notes

- Format is auto-detected by magic bytes, not file extension.
- Supported formats: zip, tar, gz, bz2, xz, 7z, rar, deb.
