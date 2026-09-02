"""Centralized assistant response formatting for V2 chatbot answers."""

from __future__ import annotations

from dataclasses import dataclass

from migration_factory.control_tower.application.redaction import redact_model_summary


@dataclass(frozen=True)
class AssistantResponseSection:
    title: str
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssistantResponseCard:
    headline: str
    status: str
    summary: str
    sections: tuple[AssistantResponseSection, ...] = ()
    next_step: str = ""
    evidence_refs: tuple[str, ...] = ()
    safety_note: str = ""


class V2AssistantResponseComposer:
    """Render concise markdown answers from structured assistant cards."""

    _preferred_section_order = (
        "Change",
        "Validation",
        "Reason",
        "Safe next step",
        "Status",
        "Artifacts",
        "Next",
    )

    def render(self, card: AssistantResponseCard) -> str:
        headline = self._clean(card.headline) or self._default_headline(card.status)
        parts: list[str] = [headline]

        summary = self._clean(card.summary)
        if summary:
            parts.extend(["", summary])

        section_map: dict[str, list[AssistantResponseSection]] = {}
        for section in card.sections:
            section_map.setdefault(section.title, []).append(section)

        if card.evidence_refs and "Artifacts" not in section_map:
            section_map["Artifacts"] = [
                AssistantResponseSection(
                    title="Artifacts",
                    lines=tuple(f"`{self._clean(ref)}`" for ref in card.evidence_refs if self._clean(ref)),
                )
            ]

        if card.safety_note and "Safe next step" not in section_map:
            section_map["Safe next step"] = [
                AssistantResponseSection(
                    title="Safe next step",
                    lines=(card.safety_note,),
                )
            ]

        ordered_titles = self._ordered_titles(section_map)
        for title in ordered_titles:
            for section in section_map.get(title, []):
                body = self._render_section(section)
                if not body:
                    continue
                parts.extend(["", f"## {self._clean(title)}", body])

        if card.next_step and "Next" not in section_map:
            next_body = self._render_lines((card.next_step,))
            if next_body:
                parts.extend(["", "## Next", next_body])

        return redact_model_summary("\n".join(parts).strip())

    def _ordered_titles(self, section_map: dict[str, list[AssistantResponseSection]]) -> tuple[str, ...]:
        remaining = [title for title in section_map if title not in self._preferred_section_order]
        ordered = [title for title in self._preferred_section_order if title in section_map]
        ordered.extend(sorted(remaining))
        return tuple(ordered)

    def _render_section(self, section: AssistantResponseSection) -> str:
        cleaned_lines = tuple(self._clean(line) for line in section.lines if self._clean(line))
        return self._render_lines(cleaned_lines)

    def _render_lines(self, lines: tuple[str, ...]) -> str:
        rendered: list[str] = []
        for line in lines:
            if not line:
                continue
            if self._looks_like_markdown(line):
                rendered.append(line)
            else:
                rendered.append(f"- {line}")
        return "\n".join(rendered)

    def _looks_like_markdown(self, line: str) -> bool:
        stripped = line.lstrip()
        return stripped.startswith(("- ", "* ", "```", ">", "#", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."))

    def _clean(self, text: str) -> str:
        return redact_model_summary(str(text or "")).strip()

    def _default_headline(self, status: str) -> str:
        normalized = str(status or "info").strip().lower()
        if normalized == "done":
            return "Change completed"
        if normalized == "blocked":
            return "Change blocked safely"
        if normalized == "warning":
            return "Change needs attention"
        if normalized == "failed":
            return "Change failed"
        if normalized == "pending":
            return "Change pending"
        return "Assistant response"
