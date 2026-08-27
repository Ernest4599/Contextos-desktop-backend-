"""
Common processing engine shared by paste-conversation and file-upload
input paths (steps 6-16 of the algorithm). Takes messages already split
into {role, text} dicts, runs LLM extraction once, and yields
Server-Sent Events so the frontend can drive the live progress UI
(the animated ring, checklist, and Extraction Summary counts).

"Deleting raw conversation" has nothing to actually delete on the
backend - messages only ever live in this function's local memory and
are never written to disk or a database, so the event is emitted for
UI parity but no separate delete step is needed.
"""
from __future__ import annotations

import json
from typing import Dict, List

from services.context_extractor import (
    ExtractionError,
    build_context_package,
    extract_context,
    total_items_extracted,
)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def run_processing_pipeline(messages: List[Dict[str, str]]):
    total = len(messages)

    yield _sse("step", {"step": "reading_conversation", "status": "start"})
    for i, _ in enumerate(messages, start=1):
        yield _sse("progress", {"step": "reading_conversation", "current": i, "total": total})
    yield _sse("step", {"step": "reading_conversation", "status": "complete"})

    yield _sse("step", {"step": "detecting_topics", "status": "start"})

    try:
        extracted = extract_context(messages)
    except ExtractionError as e:
        yield _sse("error", {"message": str(e)})
        return
    except Exception as e:
        print(f"[PIPELINE] Unexpected error during extraction: {e}")
        yield _sse("error", {"message": "Something went wrong while processing your conversation. Please try again."})
        return

    yield _sse("step", {
        "step": "detecting_topics",
        "status": "complete",
        "goals": len(extracted["goals"]),
        "current_state": len(extracted["current_state"]),
    })

    yield _sse("step", {
        "step": "extracting_decisions",
        "status": "complete",
        "decisions": len(extracted["decisions"]),
    })

    yield _sse("step", {
        "step": "finding_tasks",
        "status": "complete",
        "tasks": len(extracted["tasks"]),
        "completed_work": len(extracted["completed_work"]),
    })

    yield _sse("step", {
        "step": "identifying_constraints",
        "status": "complete",
        "constraints": len(extracted["constraints"]),
    })

    yield _sse("step", {
        "step": "finding_open_questions",
        "status": "complete",
        "open_questions": len(extracted["open_questions"]),
        "key_facts": len(extracted["key_facts"]),
    })

    yield _sse("step", {
        "step": "next_action",
        "status": "complete",
        "next_action": extracted["next_action"],
    })

    yield _sse("step", {
        "step": "building_context_package",
        "status": "complete",
        "total_items": total_items_extracted(extracted),
    })

    package = build_context_package(extracted)

    yield _sse("step", {"step": "deleting_raw_conversation", "status": "complete"})

    yield _sse("complete", {
        "status": "complete",
        "context_package": package,
        "extraction_summary": {
            "topics_detected": len(extracted["goals"]) + len(extracted["current_state"]),
            "decisions_found": len(extracted["decisions"]),
            "tasks_identified": len(extracted["tasks"]) + len(extracted["completed_work"]),
            "constraints_found": len(extracted["constraints"]),
            "open_questions": len(extracted["open_questions"]),
            "total_items_extracted": total_items_extracted(extracted),
        },
    })
