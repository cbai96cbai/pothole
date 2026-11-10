"""Recorded batches: one row per line, compact sorted-key JSON, gzipped without a timestamp."""

import gzip
import json
from pathlib import Path
from typing import Any


def dump(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = (json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows)
    data = ("\n".join(lines) + "\n").encode()
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(data)


def load(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
