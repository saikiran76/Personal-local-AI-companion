"""Agent Orchestrator — ReAct execution loop with MCP tool interception."""

import json
import logging
from typing import AsyncIterator
from dataclasses import dataclass, field

from .model_loader import ModelLoader
from .mcp_client import MCPClientManager

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful desktop AI assistant running locally on the user's device.
You have access to tools for file system operations, note management, and browser automation.
Always prioritize user privacy — all processing happens locally.
Be concise, helpful, and proactive. Use tools when they would help accomplish the task."""

# Max ReAct iterations to prevent infinite loops
MAX_TOOL_ROUNDS = 5


@dataclass
class AgentEvent:
    """Events streamed from the agent to the client."""
    type: str  # token | tool_call | tool_result | thinking | error | done
    content: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    done: bool = False


class AgentOrchestrator:
    """
    ReAct execution loop agent.

    Flow:
    1. Build messages with system prompt + tool descriptions
    2. Send to LLM, buffer full response
    3. Check if response contains a tool call
    4. If tool call found:
       a. Yield tool_call event (no raw JSON to client)
       b. Execute via MCP
       c. Yield tool_result event
       d. Append both to history
       e. Loop back to step 1
    5. If no tool call:
       a. Stream final response tokens to client
       b. Yield done event
    """

    def __init__(self, model_loader: ModelLoader, mcp_manager: MCPClientManager):
        self.model = model_loader
        self.mcp = mcp_manager
        self.conversation_history: list[dict] = []

    async def initialize(self):
        """Connect to MCP servers and discover available tools."""
        await self.mcp.connect_all()
        tools = self.mcp.get_tool_schemas()
        logger.info("Agent initialized with %d tools: %s", len(tools), [t["name"] for t in tools])

    async def chat(self, user_message: str) -> AsyncIterator[AgentEvent]:
        """
        Process a chat message through the ReAct loop.
        """
        self.conversation_history.append({"role": "user", "content": user_message})

        # ReAct loop — allow multiple tool rounds
        for round_num in range(MAX_TOOL_ROUNDS):
            logger.info("ReAct round %d", round_num + 1)

            messages = self._build_messages()

            # Buffer the full LLM response (don't stream yet — we need to check for tool calls)
            full_response = ""
            async for token in self.model.generate(messages=messages, max_tokens=1024):
                # Skip finish_reason JSON tokens
                if token.startswith("{") and "finish_reason" in token:
                    continue
                full_response += token

            logger.info("LLM response (round %d): %s", round_num + 1, full_response[:200])

            # Check for tool call in the response
            tool_call = self._extract_tool_call(full_response)

            if tool_call is None:
                # No tool call — stream the final response to client
                # Yield the response in chunks for smooth streaming
                chunk_size = 12
                for i in range(0, len(full_response), chunk_size):
                    yield AgentEvent(type="token", content=full_response[i:i + chunk_size])

                self.conversation_history.append({"role": "assistant", "content": full_response})
                self._trim_history()
                yield AgentEvent(type="done", content="", done=True)
                return

            # Tool call found — execute it (don't send raw JSON to client)
            tool_name = tool_call["tool"]
            tool_args = tool_call["arguments"]

            yield AgentEvent(
                type="tool_call",
                content=f"Using {tool_name}...",
                tool_name=tool_name,
                tool_args=tool_args,
            )

            # Execute tool via MCP
            try:
                tool_result = await self.mcp.call_tool(tool_name, tool_args)
            except Exception as e:
                tool_result = json.dumps({"error": str(e)})
                logger.error("Tool execution failed: %s", e)

            yield AgentEvent(
                type="tool_result",
                content=tool_result,
                tool_name=tool_name,
                tool_result=tool_result,
            )

            # Add to conversation history
            self.conversation_history.append({"role": "assistant", "content": full_response})
            self.conversation_history.append({
                "role": "tool",
                "content": f"Tool '{tool_name}' result:\n{tool_result}",
            })

            self._trim_history()

        # Exhausted all rounds
        self.conversation_history.append({
            "role": "assistant",
            "content": "I've completed the tool operations. Let me know if you need anything else.",
        })
        yield AgentEvent(type="done", content="", done=True)

    def _build_messages(self) -> list[dict]:
        """Build the full message list for the LLM."""
        tool_descriptions = self.mcp.get_tool_definitions_for_llm()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + tool_descriptions},
        ]

        # Add conversation history (last 16 messages to stay within context)
        for msg in self.conversation_history[-16:]:
            messages.append(msg)

        return messages

    def _extract_tool_call(self, text: str) -> dict | None:
        """
        Extract a tool call JSON from the LLM response text.

        Looks for patterns like:
          {"tool": "read_file", "arguments": {"path": "/tmp/test.txt"}}

        Uses bracket-counting to handle nested JSON in arguments.
        Returns None if no tool call is found.
        """
        # Find the "tool" key
        idx = text.find('"tool"')
        if idx == -1:
            return None

        # Find the opening brace before "tool"
        brace_start = text.rfind('{', 0, idx)
        if brace_start == -1:
            return None

        # Count braces to find the matching closing brace
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

    def _trim_history(self):
        """Keep conversation history manageable."""
        if len(self.conversation_history) > 30:
            self.conversation_history = self.conversation_history[-20:]

    def reset(self):
        """Clear conversation history."""
        self.conversation_history = []
        logger.info("Agent conversation reset")
