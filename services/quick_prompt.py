"""
Quick Prompt engine: takes Overview / Decisions / Task and builds a
complete, ready-to-use prompt (role, context, locked decisions,
constraints, output format) in a single structured LLM call - covering
steps 3-15 of the algorithm (understanding, constraint detection, role
selection, reasoning strategy, output structure, generation, quality
check, and optimization all happen inside the one model call).
"""
from __future__ import annotations

from typing import Any, Dict

from services.llm_providers import LLMProviderError, call_llm, parse_llm_json

QuickPromptError = LLMProviderError

MAX_FIELD_LENGTH = 2000


class QuickPromptValidationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


QUICK_PROMPT_SYSTEM_PROMPT = """You turn three raw inputs - Overview, Decisions, and Task - into a single, complete, ready-to-use prompt for another AI to execute, with no access to the original conversation.

Rules:
- Do NOT invent facts. If something is not present, treat it as unknown - do not assume.
- Do NOT reverse, contradict, or "improve on" anything in Decisions. Decisions are locked constraints unless Task explicitly asks to evaluate them.
- Preserve the user's original meaning; do not unnecessarily rewrite their intent.

Process:
1. From Overview, extract situation, background, objective, relevant facts, people/entities, tools/technology, current state, and other important context. Mark anything absent as unknown - do not guess.
2. From Decisions, extract every already-made decision, chosen technology/approach, and agreed requirement. These become "locked_decisions" - hard constraints the generated prompt must respect.
3. From Task, extract the primary objective, desired outcome, requested action, expected deliverable, implied requirements, output format, and quality expectations.
4. Combine constraints from all three inputs: technical, time, budget, platform, user requirements, prohibited approaches, scope limitations.
5. If information critical to the task is missing: if a reasonable assumption can be made, state it explicitly as an assumption; otherwise include one targeted clarification request inside the generated prompt.
6. Choose the single most appropriate expert role for the task (e.g. "senior software engineer" for coding, "business strategist" for business tasks, "marketing strategist", "research analyst", "expert writer/editor", "product/UI designer", or "multidisciplinary expert" for complex/mixed tasks).
7. Choose the output format that best serves the requested deliverable if the user did not specify one (e.g. numbered steps, table, code, architecture, report, strategy, bullet points, JSON, explanation, multiple sections).
8. Build the final prompt combining: role, context, locked decisions, objective, constraints, requirements, assumptions, task instructions, output format, and quality standard. Add task-specific instructions appropriate to the task type (e.g. for coding: production-ready code, respect existing architecture, explain key decisions, handle errors; for strategy: practical actions, ranked recommendations, trade-offs; for writing: preserve meaning, match tone, avoid generic language; for analysis: separate facts from assumptions, identify risks, compare alternatives, clear conclusion).
9. Before finalizing, verify: clear role, clear context, clear objective, decisions preserved, constraints preserved, no invented facts, no contradictions, clear deliverable, clear output format, task-specific instructions included. Remove unnecessary repetition, vague instructions, irrelevant context, and excessive wording. Keep all context, decisions, constraints, requirements, and the expected result.
10. Final test: could another capable AI understand exactly what needs to be done without access to the original conversation? If not, improve the prompt before returning it.

Respond with ONLY a JSON object with these exact keys:
- "role": string, the expert role selected
- "prompt": string, the complete final prompt ready to paste into any AI
- "assumptions": array of strings, any assumptions made (empty array if none)
- "output_format": string, the output format chosen

No preamble, no markdown fences, no extra commentary."""


def validate_quick_prompt_input(overview: str, decisions: str, task: str) -> None:
    overview = (overview or "").strip()
    decisions = (decisions or "").strip()
    task = (task or "").strip()

    if not overview and not decisions and not task:
        raise QuickPromptValidationError("Please provide some information first")

    if not task:
        raise QuickPromptValidationError("Please describe what you need help with")

    for field_name, value in (("Overview", overview), ("Decisions", decisions), ("Task", task)):
        if len(value) > MAX_FIELD_LENGTH:
            raise QuickPromptValidationError(f"{field_name} is too long — please trim it")


def generate_quick_prompt(overview: str, decisions: str, task: str) -> Dict[str, Any]:
    validate_quick_prompt_input(overview, decisions, task)

    user_content = (
        f"OVERVIEW:\n{(overview or '').strip() or '(none provided)'}\n\n"
        f"DECISIONS:\n{(decisions or '').strip() or '(none provided)'}\n\n"
        f"TASK:\n{(task or '').strip()}"
    )

    raw = call_llm(QUICK_PROMPT_SYSTEM_PROMPT, user_content)
    parsed = parse_llm_json(raw)

    return {
        "role": parsed.get("role", ""),
        "prompt": parsed.get("prompt", ""),
        "assumptions": parsed.get("assumptions", []) if isinstance(parsed.get("assumptions"), list) else [],
        "output_format": parsed.get("output_format", ""),
    }
