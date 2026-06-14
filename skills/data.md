---
description: Working with structured data (JSON, YAML, CSV)
---
# Structured Data

## JSON

Use bash with jq (if available) or Python for JSON operations:
- Pretty print: `python3 -m json.tool data.json`
- Extract field: `python3 -c "import json; d=json.load(open('data.json')); print(d['key'])"`

For complex queries, write a small Python script with write tool and run with bash.

## CSV

- Preview: `head -5 data.csv`
- Count rows: `wc -l data.csv`
- Extract column: `cut -d',' -f2 data.csv | head -10`

## YAML

Read YAML files with read tool. For parsing, use Python:
```
python3 -c "import yaml; print(yaml.safe_load(open('config.yaml')))"
```
Note: yaml module may not be available. Check with `python3 -c "import yaml"`.

## INI / .env

Read with read tool. These are simple key=value formats, easy to edit with edit tool.