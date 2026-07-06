"""Agent Orchestrator — ReAct execution loop with MCP tool interception."""

import json
import logging
import re
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

# Pattern to detect tool calls in LLM output
TOOL_CALL_PATTERN = re.compile(
    r'\{"tool"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{[^}]*\})\s*\}',
    re.DOTALL,
)


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
    2. Send to LLM, stream response tokens
    3. After LLM finishes, check if response contains a tool call
    4. If tool call found → execute via MCP → append result → go to step 2
    5. If no tool call → stream final response to client

    The agent intercepts tool calls before they reach the user,
    executes them transparently, and feeds results back to the LLM.
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

        Streams AgentEvent objects:
        - type="token": streaming text token from LLM
        - type="thinking": agent is deciding (internal)
        - type="tool_call": agent is calling a tool
        - type="tool_result": tool returned a result
        - type="done": response complete
        """
        self.conversation_history.append({"role": "user", "content": user_message})

        # ReAct loop — allow multiple tool rounds
        for round_num in range(MAX_TOOL_ROUNDS):
            logger.info("ReAct round %d", round_num + 1)

            # Build messages for LLM
            messages = self._build_messages()

            # Collect full LLM response
            full_response = ""

            # Stream tokens from LLM
            async for token in self.model.generate(messages=messages, max_tokens=1024):
                # Check if this is a finish_reason signal
                if token.startswith("{") and "finish_reason" in token:
                    break
                full_response += token
                yield AgentEvent(type="token", content=token)

            logger.info("LLM response (round %d): %s", round_num + 1, full_response[:200])

            # Check for tool call in the response
            tool_call = self._extract_tool_call(full_response)

            if tool_call is None:
                # No tool call — this is the final response
                self.conversation_history.append({"role": "assistant", "content": full_response})
                self._trim_history()
                yield AgentEvent(type="done", content="", done=True)
                return

            # Tool call found — execute it
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

            # Add the assistant's tool-calling message and tool result to history
            self.conversation_history.append({"role": "assistant", "content": full_response})
            self.conversation_history.append({
                "role": "tool",
                "content": f"Tool '{tool_name}' result:\n{tool_result}",
            })

            self._trim_history()

            # Loop continues — LLM will see the tool result and generate next response

        # If we exhaust all rounds, add a final message
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

        Returns None if no tool call is found.
        """
        # Find the last JSON object in the response that has a "tool" key
        matches = TOOL_CALL_PATTERN.findall(text)
        if not matches:
            return None

        # Take the last match (in case there are multiple)
        tool_name, args_str = matches[-1]

        try:
            arguments = json.loads(args_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse tool arguments: %s", args_str)
            return None

        return {"tool": tool_name, "arguments": arguments}

    def _trim_history(self):
        """Keep conversation history manageable."""
        if len(self.conversation_history) > 30:
            # Keep the last 20 messages
            self.conversation_history = self.conversation_history[-20:]

    def reset(self):
        """Clear conversation history."""
        self.conversation_history = []
        logger.info("Agent conversation reset")
