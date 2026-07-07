"""Agent Orchestrator — ReAct execution loop with MCP tool interception."""

import json
import logging
import re
from typing import AsyncIterator
from dataclasses import dataclass, field

from .model_loader import ModelLoader, get_model_tool_capability
from .mcp_client import MCPClientManager

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful desktop AI assistant running locally on the user's device.
You have access to tools for file system operations, note management, and browser automation.
Always prioritize user privacy — all processing happens locally.
Be concise, helpful, and proactive. Use tools when they would help accomplish the task."""

MAX_TOOL_ROUNDS = 5

_ECHO_PATTERNS = [
    re.compile(r"you have access to", re.IGNORECASE),
    re.compile(r"file system operations", re.IGNORECASE),
    re.compile(r"note management", re.IGNORECASE),
    re.compile(r"browser automation", re.IGNORECASE),
    re.compile(r"prioritize user privacy", re.IGNORECASE),
    re.compile(r"local processing", re.IGNORECASE),
    re.compile(r'tool.*json.*block', re.IGNORECASE),
    re.compile(r'\{"tool":\s*"tool_name"', re.IGNORECASE),
]

_MIN_RESPONSE_LENGTH = 10


@dataclass
class AgentEvent:
    """Events streamed from the agent to the client."""
    type: str  # token | clear | tool_call | tool_result | thinking | error | done
    content: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    done: bool = False


class AgentOrchestrator:
    """
    ReAct agent with buffered streaming.

    Each LLM round buffers all tokens internally, then after generation
    completes, checks whether the response is a tool call or text.
    - Tool call: emits tool_call → tool_result, then loops for next round.
    - Text: streams buffered tokens to the client, then emits done.

    This avoids React batching issues where 'clear' events fail to wipe
    previously streamed tokens in the same render cycle.
    """

    def __init__(self, model_loader: ModelLoader, mcp_manager: MCPClientManager):
        self.model = model_loader
        self.mcp = mcp_manager
        self.conversation_history: list[dict] = []
        self._tool_call_count = 0

    async def initialize(self):
        await self.mcp.connect_all()
        tools = self.mcp.get_tool_schemas()
        logger.info("Agent initialized with %d tools: %s", len(tools), [t["name"] for t in tools])

    async def chat(self, user_message: str) -> AsyncIterator[AgentEvent]:
        self.conversation_history.append({"role": "user", "content": user_message})

        model_name = self.model.info.name
        tool_cap = get_model_tool_capability(model_name)
        logger.info("Model: %s (tool capability: %s)", model_name, tool_cap)

        if tool_cap == "weak":
            async for event in self._stream_without_tools():
                yield event
            return

        for round_num in range(MAX_TOOL_ROUNDS):
            logger.info("ReAct round %d", round_num + 1)

            messages = self._build_messages()

            # --- Buffered generation: stream tokens only after confirming no tool call ---
            # This avoids React batching issues where 'clear' wipes streamed tokens
            yield AgentEvent(type="thinking", content="Processing...")

            full_response = ""
            token_buffer = []  # buffered tokens, not yet sent to client

            async for token in self.model.generate(messages=messages, max_tokens=512):
                if token.startswith("{") and "finish_reason" in token:
                    continue
                full_response += token
                token_buffer.append(token)

            logger.info("LLM response (round %d): %s", round_num + 1, full_response[:200])

            # --- Check for tool call BEFORE streaming anything ---
            tool_call = None
            if '"tool"' in full_response and '{' in full_response:
                tool_call = self._extract_tool_call(full_response)

            if tool_call is not None and not self._is_prompt_echo(full_response):
                # Tool call — stream nothing, show structured tool call
                tool_name = tool_call["tool"]
                tool_args = tool_call["arguments"]

                yield AgentEvent(
                    type="tool_call",
                    content=f"Using {tool_name}...",
                    tool_name=tool_name,
                    tool_args=tool_args,
                )

                try:
                    tool_result = await self.mcp.call_tool(tool_name, tool_args)
                except Exception as e:
                    tool_result = json.dumps({"error": str(e)})
                    logger.error("Tool execution failed: %s", e)

                self._tool_call_count += 1

                yield AgentEvent(
                    type="tool_result",
                    content=tool_result,
                    tool_name=tool_name,
                    tool_result=tool_result,
                )

                self.conversation_history.append({"role": "assistant", "content": full_response})
                self.conversation_history.append({
                    "role": "tool",
                    "content": f"Tool '{tool_name}' result:\n{tool_result}",
                })
                self._trim_history()
                continue  # Next ReAct round — LLM will see tool result

            else:
                # Normal text response — stream buffered tokens
                if self._is_valid_response(full_response):
                    for buffered_token in token_buffer:
                        yield AgentEvent(type="token", content=buffered_token)
                    self.conversation_history.append({"role": "assistant", "content": full_response})
                    self._trim_history()
                    yield AgentEvent(type="done", content="", done=True)
                    return
                else:
                    logger.warning("Garbage response detected")
                    yield AgentEvent(
                        type="token",
                        content="I'm having trouble generating a good response. Could you rephrase?",
                    )
                    break

        yield AgentEvent(type="done", content="", done=True)

    async def _stream_without_tools(self) -> AsyncIterator[AgentEvent]:
        messages = self._build_messages()
        async for token in self.model.generate(messages=messages, max_tokens=512):
            if token.startswith("{") and "finish_reason" in token:
                continue
            yield AgentEvent(type="token", content=token)
        yield AgentEvent(type="done", content="", done=True)

    def _build_messages(self) -> list[dict]:
        tool_cap = get_model_tool_capability(self.model.info.name)
        if tool_cap == "good":
            tool_descriptions = self.mcp.get_tool_definitions_for_llm()
            system = SYSTEM_PROMPT + "\n\n" + tool_descriptions
        else:
            system = SYSTEM_PROMPT

        messages = [{"role": "system", "content": system}]
        for msg in self.conversation_history[-10:]:
            messages.append(msg)
        return messages

    def _extract_tool_call(self, text: str) -> dict | None:
        idx = text.find('"tool"')
        if idx == -1:
            return None

        brace_start = text.rfind('{', 0, idx)
        if brace_start == -1:
            return None

        depth = 0
        in_string = False
        escape = False
        for i in range(brace_start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    json_str = text[brace_start:i + 1]
                    try:
                        obj = json.loads(json_str)
                        if "tool" in obj and "arguments" in obj:
                            return {"tool": obj["tool"], "arguments": obj["arguments"]}
                    except json.JSONDecodeError:
                        return None
                    break
        return None

    def _is_json_complete(self, text: str) -> bool:
        depth = 0
        in_string = False
        escape = False
        for c in text:
            if escape:
                escape = False
                continue
            if c == '\\' and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return True
        return False

    def _is_prompt_echo(self, text: str) -> bool:
        matches = sum(1 for p in _ECHO_PATTERNS if p.search(text))
        return matches >= 3

    def _is_valid_response(self, text: str) -> bool:
        if len(text.strip()) < _MIN_RESPONSE_LENGTH:
            return False
        if self._is_prompt_echo(text):
            return False
        garbage = [
            re.compile(r'^[\s\W]*$'),
            re.compile(r'(.)\1{10,}'),
        ]
        for g in garbage:
            if g.search(text):
                return False
        return True

    def _trim_history(self):
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-10:]

    def reset(self):
        self.conversation_history = []
        self._tool_call_count = 0
        logger.info("Agent conversation reset")
