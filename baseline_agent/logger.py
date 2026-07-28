from __future__ import annotations

"""Small JSONL logger for the standalone baseline agent."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = ROOT / "logs"


def new_run_id() -> str:
    return f"baseline-{uuid4().hex[:12]}"


class BaselineLogger:
    """Write readable logs and structured JSONL events for demo inspection."""

    def __init__(self, *, run_id: Optional[str] = None, log_dir: Optional[str | Path] = None) -> None:
        self.run_id = run_id or new_run_id()
        self.log_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.text_path = self.log_dir / "baseline_agent.log"
        self.jsonl_path = self.log_dir / "baseline_agent.jsonl"

        self._logger = logging.getLogger(f"baseline_agent.{self.run_id}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        if not self._logger.handlers:
            handler = logging.FileHandler(self.text_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
            self._logger.addHandler(handler)

    def event(self, stage: str, **details: Any) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "stage": stage,
            "details": details,
        }
        self._logger.info("%s | %s", stage, json.dumps(details, ensure_ascii=False, default=str))
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record

    def paths(self) -> Dict[str, str]:
        return {
            "text_log": str(self.text_path),
            "jsonl_log": str(self.jsonl_path),
        }
