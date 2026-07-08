"""Agent Orchestrator — ReAct execution loop with MCP tool interception."""

import json
import logging
import re
from typing import AsyncIterator
from dataclasses import dataclass, field

from .model_loader import ModelLoader, get_model_tool_capability
from .mcp_client import MCPClientManager
from .database import db

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful desktop AI assistant running locally on the user's device.
You have access to tools for file system operations, note management, and browser automation.
Always prioritize user privacy — all processing happens locally.
Be concise, helpful, and proactive. Use tools when they would help accomplish the task.
Attempt a tool call even with partial information — the system will ask the user for anything missing.

IMPORTANT: Only act on the user's most recent message. Do not continue a previous task unless the user explicitly references it.
Never fabricate user data like email addresses, names, or file contents.
If a tool requires user-provided values (like an email address or subject), use the placeholder
"ask_user" and the system will request the information from the user."""

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

_CONFIRMATION_TOOLS = {"delete_file", "move_file", "execute_organize", "draft_email", "open_email_client"}


@dataclass
class AgentEvent:
    """Events streamed from the agent to the client."""
    type: str  # token | clear | tool_call | tool_result | thinking | error | done | clarify | confirm | compose_form
    content: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    done: bool = False


class AgentOrchestrator:
    """
    ReAct agent with buffered streaming and interactive clarification.

    Supports three response paths:
    - Tool call with complete args → execute immediately (or confirm if risky)
    - Tool call with missing args → ask user for each missing field one at a time
    - Text response → stream to client

    Pending state tracks whether we're waiting for a clarification answer
    or a confirmation decision, so the next user reply routes correctly.
    """

    def __init__(self, model_loader: ModelLoader, mcp_manager: MCPClientManager):
        self.model = model_loader
        self.mcp = mcp_manager
        self.conversation_history: list[dict] = []
        self._tool_call_count = 0
        self._pending_state: dict | None = None
        self._organize_plans: dict[str, dict] = {}  # plan_id → {moves, path, summary}
        self._conversation_id: int | None = None

    async def initialize(self):
        await self.mcp.connect_all()
        tools = self.mcp.get_tool_schemas()
        logger.info("Agent initialized with %d tools: %s", len(tools), [t["name"] for t in tools])

    async def chat(self, user_message: str, user_response: str | None = None) -> AsyncIterator[AgentEvent]:
        # --- Resume from pending state ---
        if self._pending_state and user_response is not None:
            pending = self._pending_state
            self._pending_state = None

            if pending["type"] == "clarify":
                async for event in self._handle_clarify_resume(pending, user_message, user_response):
                    yield event
                return

            elif pending["type"] == "confirm":
                async for event in self._handle_confirm_resume(pending, user_response):
                    yield event
                return

        # --- Escape hatch: if user typed something unrelated to pending, clear it ---
        if self._pending_state and user_response is None:
            logger.info("Clearing stale pending state — user sent unrelated message")
            self._pending_state = None
            self._organize_plans.clear()

        # --- Compose intent bypass: short-circuit LLM for email drafting ---
        compose_slots = self._is_compose_intent(user_message)
        if compose_slots is not None:
            logger.info("Compose intent detected: %s", compose_slots)
            if self._conversation_id is None:
                self._conversation_id = db.create_conversation(title=user_message[:80])
            self.conversation_history.append({"role": "user", "content": user_message})
            db.add_message(self._conversation_id, "user", user_message)
            yield AgentEvent(
                type="compose_form",
                content=json.dumps(compose_slots),
                tool_name="draft_email",
                tool_args=compose_slots,
            )
            return

        # --- Organize intent bypass: short-circuit LLM for file organization ---
        organize_path = self._is_organize_intent(user_message)
        if organize_path is not None:
            logger.info("Organize intent detected for path: %s", organize_path)
            if self._conversation_id is None:
                self._conversation_id = db.create_conversation(title=user_message[:80])
            self.conversation_history.append({"role": "user", "content": user_message})
            db.add_message(self._conversation_id, "user", user_message)
            async for event in self._handle_organize_intent(organize_path):
                yield event
            return

        # --- Normal flow ---
        # Create a conversation if we don't have one yet
        if self._conversation_id is None:
            self._conversation_id = db.create_conversation(title=user_message[:80])

        self.conversation_history.append({"role": "user", "content": user_message})
        db.add_message(self._conversation_id, "user", user_message)

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

            yield AgentEvent(type="thinking", content="Processing...")

            full_response = ""
            token_buffer = []

            async for token in self.model.generate(messages=messages, max_tokens=512):
                if token.startswith("{") and "finish_reason" in token:
                    continue
                full_response += token
                token_buffer.append(token)

            logger.info("LLM response (round %d): %s", round_num + 1, full_response[:200])

            # --- Check for tool call ---
            tool_call = None
            if '"tool"' in full_response and '{' in full_response:
                tool_call = self._extract_tool_call(full_response)

            if tool_call is not None and not self._is_prompt_echo(full_response):
                tool_name = tool_call["tool"]
                tool_args = tool_call["arguments"]

                # --- Validate arguments ---
                missing = self._validate_tool_args(tool_name, tool_args)
                if missing:
                    # Ask for first missing field one at a time
                    field_name = missing[0]
                    remaining = missing[1:]
                    self._pending_state = {
                        "type": "clarify",
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "missing_field": field_name,
                        "remaining_fields": remaining,
                    }
                    yield AgentEvent(
                        type="clarify",
                        content=f"I need the **{field_name}** to use `{tool_name}`. What should I use?",
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                    return

                # --- Confirmation for risky tools ---
                if tool_name in _CONFIRMATION_TOOLS:
                    summary = self._format_confirm_summary(tool_name, tool_args)
                    self._pending_state = {
                        "type": "confirm",
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                    }
                    yield AgentEvent(
                        type="confirm",
                        content=summary,
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                    return

                # --- Execute tool ---
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

                # Store summaries, not full content — keeps context lean for next round
                summary_response = full_response[:200] + "..." if len(full_response) > 200 else full_response
                summary_result = tool_result[:300] + "..." if len(tool_result) > 300 else tool_result
                self.conversation_history.append({"role": "assistant", "content": summary_response})
                self.conversation_history.append({
                    "role": "tool",
                    "content": f"Tool '{tool_name}' result:\n{summary_result}",
                })
                self._trim_history()

                # Persist to database
                if self._conversation_id:
                    db.add_message(self._conversation_id, "assistant", summary_response)
                    db.add_message(self._conversation_id, "tool", summary_result)
                    # Log activity with scope based on tool
                    scope = self._tool_scope(tool_name)
                    db.log_activity(scope, tool_name, self._tool_ack_message(tool_name, tool_args, tool_result)[:100])

                # If tool succeeded, emit direct ack — no next LLM round
                if '"error"' not in tool_result:
                    ack = self._tool_ack_message(tool_name, tool_args, tool_result)
                    yield AgentEvent(type="token", content=ack)
                    self.conversation_history.append({"role": "assistant", "content": ack})
                    self._trim_history()
                    yield AgentEvent(type="done", content="", done=True)
                    return
                continue

            else:
                # Normal text response
                if self._is_valid_response(full_response):
                    for buffered_token in token_buffer:
                        yield AgentEvent(type="token", content=buffered_token)
                    self.conversation_history.append({"role": "assistant", "content": full_response})
                    self._trim_history()
                    if self._conversation_id:
                        db.add_message(self._conversation_id, "assistant", full_response)
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

    # --- Resume handlers ---

    async def _handle_clarify_resume(
        self, pending: dict, user_message: str, user_response: str
    ) -> AsyncIterator[AgentEvent]:
        """Handle user's answer to a clarification question."""
        tool_name = pending["tool_name"]
        tool_args = dict(pending["tool_args"])
        field_name = pending["missing_field"]
        remaining = list(pending.get("remaining_fields", []))

        # Fill the answered field
        tool_args[field_name] = user_response.strip()

        # Add exchange to history so LLM sees the clarification
        self.conversation_history.append({"role": "user", "content": user_message})
        if self._conversation_id:
            db.add_message(self._conversation_id, "user", user_message)

        # Check if more fields are missing
        still_missing = self._validate_tool_args(tool_name, tool_args)
        if still_missing:
            next_field = still_missing[0]
            remaining = still_missing[1:]
            self._pending_state = {
                "type": "clarify",
                "tool_name": tool_name,
                "tool_args": tool_args,
                "missing_field": next_field,
                "remaining_fields": remaining,
            }
            yield AgentEvent(
                type="clarify",
                content=f"I also need the **{next_field}**. What should I use?",
                tool_name=tool_name,
                tool_args=tool_args,
            )
            return

        # All args present — check if confirmation needed
        if tool_name in _CONFIRMATION_TOOLS:
            summary = self._format_confirm_summary(tool_name, tool_args)
            self._pending_state = {
                "type": "confirm",
                "tool_name": tool_name,
                "tool_args": tool_args,
            }
            yield AgentEvent(
                type="confirm",
                content=summary,
                tool_name=tool_name,
                tool_args=tool_args,
            )
            return

        # Execute
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

        summary_result = tool_result[:300] + "..." if len(tool_result) > 300 else tool_result
        self.conversation_history.append({
            "role": "tool",
            "content": f"Tool '{tool_name}' result:\n{summary_result}",
        })
        self._trim_history()

        # Persist
        if self._conversation_id:
            db.add_message(self._conversation_id, "tool", summary_result)
            scope = self._tool_scope(tool_name)
            db.log_activity(scope, tool_name, self._tool_ack_message(tool_name, tool_args, tool_result)[:100])

        # If tool failed, let LLM explain. If succeeded, emit a direct
        # acknowledgment — no LLM round, no duplication risk.
        if '"error"' in tool_result:
            async for event in self._final_response_round():
                yield event
        else:
            ack = self._tool_ack_message(tool_name, tool_args, tool_result)
            yield AgentEvent(type="token", content=ack)
            self.conversation_history.append({"role": "assistant", "content": ack})
            self._trim_history()
            yield AgentEvent(type="done", content="", done=True)

    async def _handle_confirm_resume(
        self, pending: dict, user_response: str
    ) -> AsyncIterator[AgentEvent]:
        """Handle user's yes/no confirmation."""
        tool_name = pending["tool_name"]
        tool_args = pending["tool_args"]

        answer = user_response.strip().lower()
        if answer not in ("yes", "y", "confirm", "ok", "sure", "do it", "go"):
            yield AgentEvent(
                type="token",
                content="Okay, cancelled.",
            )
            yield AgentEvent(type="done", content="", done=True)
            return

        # Execute
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

        summary_result = tool_result[:300] + "..." if len(tool_result) > 300 else tool_result
        self.conversation_history.append({
            "role": "tool",
            "content": f"Tool '{tool_name}' result:\n{summary_result}",
        })
        self._trim_history()

        # Persist
        if self._conversation_id:
            db.add_message(self._conversation_id, "tool", summary_result)
            scope = self._tool_scope(tool_name)
            db.log_activity(scope, tool_name, self._tool_ack_message(tool_name, tool_args, tool_result)[:100])

        if '"error"' in tool_result:
            async for event in self._final_response_round():
                yield event
        else:
            ack = self._tool_ack_message(tool_name, tool_args, tool_result)
            yield AgentEvent(type="token", content=ack)
            self.conversation_history.append({"role": "assistant", "content": ack})
            self._trim_history()
            yield AgentEvent(type="done", content="", done=True)

    async def _final_response_round(self) -> AsyncIterator[AgentEvent]:
        """One more LLM round to generate a natural language response after tool execution."""
        messages = self._build_messages()

        yield AgentEvent(type="thinking", content="Processing...")

        full_response = ""
        token_buffer = []

        async for token in self.model.generate(messages=messages, max_tokens=512):
            if token.startswith("{") and "finish_reason" in token:
                continue
            full_response += token
            token_buffer.append(token)

        if self._is_valid_response(full_response):
            for buffered_token in token_buffer:
                yield AgentEvent(type="token", content=buffered_token)
            self.conversation_history.append({"role": "assistant", "content": full_response})
            self._trim_history()
        else:
            yield AgentEvent(type="token", content="Done!")
            self.conversation_history.append({"role": "assistant", "content": "Done!"})

        yield AgentEvent(type="done", content="", done=True)

    # --- Tool argument validation ---

    def _validate_tool_args(self, tool_name: str, arguments: dict) -> list[str]:
        """Return list of missing required argument names (empty if all present)."""
        schema = self.mcp.get_tool_schema(tool_name)
        if not schema:
            return []

        required = schema.get("inputSchema", {}).get("required", [])
        missing = []
        for r in required:
            val = arguments.get(r)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing.append(r)
        return missing

    def _format_confirm_summary(self, tool_name: str, tool_args: dict) -> str:
        """Format a human-readable confirmation prompt."""
        if tool_name == "delete_file":
            return f"⚠️ Delete file `{tool_args.get('path', '?')}`? This cannot be undone."
        elif tool_name == "move_file":
            return f"Move `{tool_args.get('source', '?')}` → `{tool_args.get('destination', '?')}`?"
        elif tool_name == "execute_organize":
            plan_id = tool_args.get("plan_id", "")
            plan = self._organize_plans.get(plan_id)
            if plan:
                return f"Organize `{plan['path']}`: {plan['summary']}?"
            return f"Execute organize plan `{plan_id}`?"
        elif tool_name == "draft_email":
            to = tool_args.get("to", "?")
            subject = tool_args.get("subject", "(no subject)")
            return f'Save email draft to `{to}` with subject "{subject}"?'
        elif tool_name == "open_email_client":
            return "Open your email client with the draft?"
        return f"Confirm: `{tool_name}` with {json.dumps(tool_args)}?"

    def _tool_ack_message(self, tool_name: str, tool_args: dict, tool_result: str) -> str:
        """Generate a brief, tool-specific acknowledgment after successful execution."""
        if tool_name == "draft_email":
            to = tool_args.get("to", "")
            subject = tool_args.get("subject", "")
            parts = ["Draft saved"]
            if to:
                parts.append(f"for {to}")
            if subject:
                parts.append(f'with subject "{subject}"')
            return " ".join(parts) + "."
        elif tool_name == "open_email_client":
            return "Opened your email client."
        elif tool_name == "delete_file":
            return f"Deleted `{tool_args.get('path', 'file')}`."
        elif tool_name == "move_file":
            return f"Moved `{tool_args.get('source', '?')}` → `{tool_args.get('destination', '?')}`."
        elif tool_name == "create_note":
            return f"Note saved as `{tool_args.get('filename', 'note')}`."
        elif tool_name == "write_file":
            return f"Wrote to `{tool_args.get('path', 'file')}`."
        elif tool_name == "read_file":
            return f"Read `{tool_args.get('path', 'file')}`."
        elif tool_name == "list_directory":
            return f"Listed `{tool_args.get('path', 'directory')}`."
        elif tool_name == "open_browser":
            return f"Opened browser."
        elif tool_name == "search_web":
            return f"Searched for `{tool_args.get('query', '?')}`."
        else:
            return f"Done — {tool_name} completed."

    # --- Streaming without tools (weak models) ---

    async def _stream_without_tools(self) -> AsyncIterator[AgentEvent]:
        messages = self._build_messages()
        async for token in self.model.generate(messages=messages, max_tokens=512):
            if token.startswith("{") and "finish_reason" in token:
                continue
            yield AgentEvent(type="token", content=token)
        yield AgentEvent(type="done", content="", done=True)

    # --- Message building ---

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

    # --- Tool call extraction ---

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

    # --- Compose intent detection ---

    _COMPOSE_VERBS = re.compile(
        r'\b(draft|compose|write|send|create)\b', re.IGNORECASE
    )
    _COMPOSE_NOUN = re.compile(
        r'\b(e[\-]?mail|mail)\b', re.IGNORECASE
    )
    _TO_PATTERNS = [
        re.compile(r'\bto\s+([a-zA-Z0-9._%+\-@ ]+)', re.IGNORECASE),
        re.compile(r'\bemail\s+([a-zA-Z0-9._%+\-@ ]+)', re.IGNORECASE),
        re.compile(r'\bmail\s+to\s+([a-zA-Z0-9._%+\-@ ]+)', re.IGNORECASE),
    ]
    _SUBJECT_PATTERNS = [
        re.compile(r'\bsubject\s*:?\s*(.+?)(?:\s+body|\s+and\s+body|\s*$)', re.IGNORECASE),
        re.compile(r'\babout\s+(.+?)(?:\s+body|\s+and\s+body|\s*$)', re.IGNORECASE),
    ]
    _BODY_PATTERNS = [
        re.compile(r'\bbody\s*:?\s*(.+)', re.IGNORECASE),
        re.compile(r'\bsaying\s+(.+)', re.IGNORECASE),
        re.compile(r'\bthat\s+(.+)', re.IGNORECASE),
        re.compile(r'\bcontent\s*:?\s*(.+)', re.IGNORECASE),
    ]

    def _is_compose_intent(self, message: str) -> dict | None:
        """
        Detect if the user wants to compose/send an email.
        Returns extracted slots dict if detected, None otherwise.
        Fires before model.generate() — zero LLM involvement.
        """
        # Must match verb + "email/mail" (e.g. "write an email", "send mail to X")
        has_verb = self._COMPOSE_VERBS.search(message)
        has_noun = self._COMPOSE_NOUN.search(message)

        # Also match bare "email to X" / "mail to X" patterns
        has_email_to = re.search(r'\bemail\s+to\s+', message, re.IGNORECASE)
        has_mail_to = re.search(r'\bmail\s+to\s+', message, re.IGNORECASE)

        if not (has_verb and has_noun) and not has_email_to and not has_mail_to:
            return None

        slots = {}

        # Extract "to"
        for pattern in self._TO_PATTERNS:
            m = pattern.search(message)
            if m:
                to_val = m.group(1).strip().rstrip('.')
                if '@' in to_val or '.' in to_val:
                    slots["to"] = to_val
                break

        # Extract "subject"
        for pattern in self._SUBJECT_PATTERNS:
            m = pattern.search(message)
            if m:
                slots["subject"] = m.group(1).strip().rstrip('.')
                break

        # Extract "body"
        for pattern in self._BODY_PATTERNS:
            m = pattern.search(message)
            if m:
                slots["body"] = m.group(1).strip().rstrip('.')
                break

        # Return slots — even empty slots trigger the compose form
        # The form lets the user fill in what they didn't specify
        logger.info("Compose slots extracted: %s", slots)
        return slots

    # --- Organize intent detection ---

    _ORGANIZE_VERBS = re.compile(
        r'\b(organize|clean\s*up|tidy|sort|arrange| declutter)\b', re.IGNORECASE
    )
    _ORGANIZE_NOUNS = re.compile(
        r'\b(folder|directory|downloads?|desktop|documents?|pictures?|files?)\b',
        re.IGNORECASE,
    )
    _PATH_KEYWORDS = {
        "downloads": "downloads",
        "download": "downloads",
        "desktop": "desktop",
        "documents": "documents",
        "document": "documents",
        "pictures": "pictures",
        "picture": "pictures",
        "photos": "pictures",
        "music": "music",
        "videos": "videos",
        "home": "home",
    }

    def _is_organize_intent(self, message: str) -> str | None:
        """
        Detect if the user wants to organize/clean up a folder.
        Returns the resolved path if detected, None otherwise.
        """
        has_verb = self._ORGANIZE_VERBS.search(message)
        has_noun = self._ORGANIZE_NOUNS.search(message)

        if not has_verb and not has_noun:
            return None

        # Must have at least a verb OR a noun that strongly implies organize
        # "downloads looks messy" → noun alone is enough if it's a known folder
        if not has_verb:
            # Only trigger on noun if the message also implies disorder
            disorder_words = re.compile(
                r'\b(messy|cluttered|disorganized|chaotic|full of|clean|tidy|sort)\b',
                re.IGNORECASE,
            )
            if not disorder_words.search(message):
                return None

        # Try to extract a specific folder reference
        for keyword, alias in self._PATH_KEYWORDS.items():
            if re.search(r'\b' + keyword + r'\b', message, re.IGNORECASE):
                from pathlib import Path
                path = Path.home() / alias
                if path.exists():
                    return str(path)

        # Default to Downloads if verb matches but no specific path
        if has_verb:
            from pathlib import Path
            default = Path.home() / "Downloads"
            if default.exists():
                return str(default)

        return None

    async def _handle_organize_intent(self, path: str) -> AsyncIterator[AgentEvent]:
        """Handle organize intent — call preview_organize directly, skip LLM."""
        yield AgentEvent(type="thinking", content="Scanning folder...")

        try:
            tool_result = await self.mcp.call_tool("preview_organize", {"path": path})
        except Exception as e:
            yield AgentEvent(type="token", content=f"Error scanning folder: {e}")
            yield AgentEvent(type="done", content="", done=True)
            return

        self._tool_call_count += 1

        yield AgentEvent(
            type="tool_call",
            content="Scanning folder structure...",
            tool_name="preview_organize",
            tool_args={"path": path},
        )
        yield AgentEvent(
            type="tool_result",
            content=tool_result,
            tool_name="preview_organize",
            tool_result=tool_result,
        )

        # Parse the result to find plan_id and summary
        import json as json_mod
        try:
            result_data = json_mod.loads(tool_result) if isinstance(tool_result, str) else tool_result
        except (json_mod.JSONDecodeError, TypeError):
            result_data = {}

        plan_id = result_data.get("plan_id", "")
        summary = result_data.get("summary", "organize files by type")
        planned_moves = result_data.get("planned_moves", [])

        if not planned_moves:
            yield AgentEvent(type="token", content="No files to organize — the folder is already tidy.")
            yield AgentEvent(type="done", content="", done=True)
            return

        # Store the plan for later execution
        self._organize_plans[plan_id] = {
            "path": path,
            "summary": summary,
            "moves": planned_moves,
        }

        # Ask for confirmation
        summary_text = f"Organize `{path}`: {summary} ({len(planned_moves)} files to move)"
        self._pending_state = {
            "type": "confirm",
            "tool_name": "execute_organize",
            "tool_args": {"plan_id": plan_id},
        }
        yield AgentEvent(
            type="confirm",
            content=summary_text,
            tool_name="execute_organize",
            tool_args={"plan_id": plan_id},
        )

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
        before = len(self.conversation_history)
        if before > 20:
            self.conversation_history = self.conversation_history[-10:]
            logger.info("History trimmed: %d → %d messages", before, len(self.conversation_history))

    _TOOL_SCOPES = {
        "read_file": "files", "write_file": "files", "list_directory": "files",
        "move_file": "files", "copy_file": "files", "delete_file": "files",
        "glob_search": "files", "mkdir": "files",
        "preview_organize": "files", "execute_organize": "files",
        "create_note": "notes", "list_notes": "notes",
        "search_notes": "notes", "delete_note": "notes",
        "open_browser": "browser", "search_web": "browser",
        "draft_email": "email", "open_email_client": "email", "list_drafts": "email",
    }

    def _tool_scope(self, tool_name: str) -> str:
        return self._TOOL_SCOPES.get(tool_name, "other")

    def new_conversation(self):
        """Start a fresh conversation — saves the old one, resets state."""
        self.conversation_history = []
        self._tool_call_count = 0
        self._pending_state = None
        self._organize_plans.clear()
        self._conversation_id = None
        logger.info("Agent conversation reset")

    def reset(self):
        self.new_conversation()
