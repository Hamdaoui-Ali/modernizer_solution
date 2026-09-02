from dataclasses import dataclass


CUSTOM_AGENT_NAME = "aimf-planning-assist"
CUSTOM_AGENT_TOOLS = ["view"]
FORBIDDEN_ACTIONS_TEXT = (
    "Forbidden actions: changing unit order, tools, blockers, approval requirement, "
    "executable, or source files."
)

CUSTOM_AGENT_PROMPT = (
    "You are a read-only planning assist agent for migration planning. "
    "Provide advisory review only and do not propose structural mutations to deterministic planning outputs. "
    f"{FORBIDDEN_ACTIONS_TEXT}"
)


@dataclass(frozen=True)
class CopilotCustomAgentConfig:
    name: str
    tools: list[str]
    prompt: str


def get_copilot_custom_agent_config() -> CopilotCustomAgentConfig:
    """Return wrapper-facing static custom-agent configuration."""
    return CopilotCustomAgentConfig(
        name=CUSTOM_AGENT_NAME,
        tools=list(CUSTOM_AGENT_TOOLS),
        prompt=CUSTOM_AGENT_PROMPT,
    )
