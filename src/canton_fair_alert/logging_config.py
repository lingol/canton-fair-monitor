import logging
import uuid
from typing import Optional


def configure_logging(level: int = logging.INFO) -> str:
    run_id = uuid.uuid4().hex
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return run_id


def log_context(
    run_id: str,
    command: str,
    result: str,
    category: Optional[str] = None,
    **fields: object,
) -> str:
    values = {"run_id": run_id, "command": command, "result": result, **fields}
    if category:
        values["category"] = category
    return " ".join(f"{key}={value}" for key, value in values.items() if value is not None)
