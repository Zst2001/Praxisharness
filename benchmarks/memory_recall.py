"""Evaluate scoped memory selection against the frozen v1 corpus, without a model."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.configuration import ClientPaths
from backend.domain.memory import MemoryItem, MemoryKind, MemoryScope, MemorySettings
from backend.runtime.memory import MemoryContextSelector
from backend.storage.memory import MemoryStore

SUITE = Path(__file__).with_name("memory_recall_v1.json")


def evaluate(root: Path) -> dict:
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    store = MemoryStore(ClientPaths(root))
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for memory_id, project_id, status, title, content in suite["items"]:
        store.create_item(
            MemoryItem(
                memory_id=memory_id,
                title=title,
                content=content,
                kind=MemoryKind.PROCEDURAL,
                scope=MemoryScope.PROJECT,
                project_id=project_id,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
        )
        if status == "deleted":
            store.delete_item(memory_id)
        elif status == "disabled":
            store.set_item_enabled(memory_id, enabled=False)
    selector = MemoryContextSelector(store, MemorySettings(enabled=True))
    results = {}
    for group, queries in suite["queries"].items():
        rows = []
        for project, query, expected in queries:
            selected = selector.select(query, project_id=project, now=now).selected
            ids = [entry.item.memory_id for entry in selected]
            rows.append({"query": query, "top3": ids[:3], "pass": expected in ids[:3] if expected else not ids})
        results[group] = {"passed": sum(row["pass"] for row in rows), "rows": rows}
    return results


if __name__ == "__main__":
    with TemporaryDirectory(prefix="praxis-memory-recall-") as directory:
        print(json.dumps(evaluate(Path(directory)), ensure_ascii=False, indent=2))
