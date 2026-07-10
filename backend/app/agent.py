"""Agent Orchestrator — ReAct execution loop with MCP tool interception."""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import AsyncIterator
from dataclasses import dataclass, field

from .model_loader import ModelLoader, get_model_tool_capability, build_tool_call_grammar
from .mcp_client import MCPClientManager
from .database import db

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful desktop AI assistant running locally on the user's device.
You have access to tools for file system operations, note management, and browser automation.
Always prioritize user privacy — all processing happens locally.
Be concise, helpful, and proactive. Use tools when they would help accomplish the task.
Attempt a tool call even with partial information — the system will ask the user for anything missing.

CRITICAL: You do NOT have internet access yourself. If the user asks about current events, news,
trending topics, or anything requiring up-to-date information, you MUST call search_and_fetch(query)
or search_web(query). You cannot answer these from your training data.

CRITICAL: Use final_answer ONLY when you can answer from your own knowledge. Do NOT use final_answer
to say you cannot do something — instead, call the appropriate tool. If the user asks you to do
something, try to do it with tools. Do not give up.

IMPORTANT: Only act on the user's most recent message. Do not continue a previous task unless the user explicitly references it.
Never fabricate user data like email addresses, names, or file contents.
If a tool requires user-provided values (like an email address or subject), use the placeholder
"ask_user" and the system will request the information from the user.

Before asking the user for information, check if it's already available via list_notes, search_notes, or list_drafts.
For example: if the user asks to plan their day, first call list_notes to see what's already scheduled,
then ask only about what's missing.

When asked to research, summarize, or look up a topic, use search_and_fetch(query) — it searches the web and returns the first result's content. Only ask for a URL if the user has a specific page in mind (use fetch_page for that)."""

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
    type: str  # token | clear | tool_call | tool_result | thinking | error | done | clarify | confirm | compose_form | reminder_form | permission_request | status | perf_tip
    content: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    done: bool = False


class AgentOrchestrator:
    """
    ReAct agent with buffered streaming and interactive clarification.

    Supports three response paths:
    - Tool call with complete args -> execute immediately (or confirm if risky)
    - Tool call with missing args -> ask user for each missing field one at a time
    - Text response -> stream to client

    Pending state tracks whether we're waiting for a clarification answer
    or a confirmation decision, so the next user reply routes correctly.
    """

    # Tools that execute an action and DO NOT require the LLM to synthesize an answer.
    # Read-only/Information tools (like search, read_file) are excluded so the ReAct loop continues.
    _DIRECT_ACK_TOOLS = {
        "draft_email", "open_email_client",
        "delete_file", "move_file",
        "create_note", "write_file", "open_browser",
        "create_reminder", "delete_reminder", "execute_organize",
    }

    def __init__(self, model_loader: ModelLoader, mcp_manager: MCPClientManager):
        """ReAct agent with buffered streaming and interactive clarification.

        Supports three response paths:
        - Tool call with complete args -> execute immediately (or confirm if risky)
        - Tool call with missing args -> ask user for each missing field one at a time
        - Text response -> stream to client

        Pending state tracks whether we're waiting for a clarification answer
        or a confirmation decision, so the next user reply routes correctly.
        """

    def __init__(self, model_loader: ModelLoader, mcp_manager: MCPClientManager):
        self.model = model_loader
        self.mcp = mcp_manager
        self.conversation_history: list[dict] = []
        self._tool_call_count = 0
        self._pending_state: dict | None = None
        self._organize_plans: dict[str, dict] = {}  # plan_id -> {moves, path, summary}
        self._conversation_id: int | None = None
        self._tool_grammar = None  # GBNF grammar for structured tool calls
        self._perf_tip_shown = False  # One-time slowness tip per session
        self._last_tool_call: tuple | None = None  # Duplicate tool call guard

    def record_turn(self, role: str, content: str, tool_name: str = "", conversation_id: int | None = None) -> int:
        """Shared persistence helper — creates conversation if needed, saves message.
        Returns the conversation_id."""
        cid = conversation_id or self._conversation_id
        if cid is None:
            title = content[:80] if role == "user" else content[:80]
            cid = db.create_conversation(title=title)
            self._conversation_id = cid
        db.add_message(cid, role, content[:2000], tool_name=tool_name or None)
        return cid

    async def initialize(self):
        await self.mcp.connect_all()
        tools = self.mcp.get_tool_schemas()
        logger.info("Agent initialized with %d tools: %s", len(tools), [t["name"] for t in tools])

        # Build GBNF grammar for structured tool calls (if model supports tools)
        from .model_loader import get_model_tool_capability
        tool_cap = get_model_tool_capability(self.model.info.name)
        if tool_cap == "good":
            self._tool_grammar = build_tool_call_grammar(tools)
            if self._tool_grammar:
                tool_names = [t["name"] for t in tools]
                logger.info("GBNF grammar active — tools: %s", tool_names)

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
        self._last_tool_call = None  # Reset duplicate guard per user message

        # --- Compose intent bypass: short-circuit LLM for email drafting ---
        compose_slots = self._is_compose_intent(user_message)
        if compose_slots is not None:
            logger.info("Compose intent detected: %s", compose_slots)
            self.record_turn("user", user_message)
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
            self.record_turn("user", user_message)
            async for event in self._handle_organize_intent(organize_path):
                yield event
            return

        # --- Reminder intent bypass: short-circuit LLM for reminders ---
        reminder_intent = self._is_reminder_intent(user_message)
        if reminder_intent is not None:
            action = reminder_intent.get("action", "create")
            logger.info("Reminder intent detected: %s", reminder_intent)
            self.record_turn("user", user_message)

            if action == "list":
                # List reminders — execute directly, no form needed
                date = reminder_intent.get("date")
                tool_args = {}
                if date:
                    tool_args["date"] = date
                try:
                    tool_result = await self.mcp.call_tool("list_reminders", tool_args)
                except Exception as e:
                    tool_result = json.dumps({"error": str(e)})
                    logger.exception("list_reminders failed")

                # Format and return as normal response
                try:
                    reminders = json.loads(tool_result)
                    if isinstance(reminders, list) and reminders:
                        lines = []
                        for r in reminders:
                            t = r.get("title", "?")
                            d = r.get("due_date", "?")
                            tm = r.get("due_time", "")
                            time_str = f" at {tm}" if tm else ""
                            lines.append(f"- **{t}** on {d}{time_str}")
                        content = "Your reminders:\n" + "\n".join(lines)
                    elif isinstance(reminders, list):
                        content = "No reminders found."
                    else:
                        content = str(reminders)
                except (json.JSONDecodeError, TypeError):
                    content = tool_result

                self.conversation_history.append({"role": "assistant", "content": content})
                self._trim_history()
                self.record_turn("assistant", content)
                yield AgentEvent(type="token", content=content)
                yield AgentEvent(type="done", content="", done=True)
                return

            elif action == "create":
                # Create reminder — emit form for user to fill
                yield AgentEvent(
                    type="reminder_form",
                    content=json.dumps(reminder_intent),
                    tool_name="create_reminder",
                    tool_args=reminder_intent,
                )
                return

            # delete or unknown — fall through to LLM

        # --- Memory intent bypass: save user preferences directly ---
        memory_content = self._is_memory_intent(user_message)
        if memory_content is not None:
            logger.info("Memory intent detected: %s", memory_content)
            self.record_turn("user", user_message)
            db.add_memory("fact", memory_content)
            db.log_activity("memory", "remember", memory_content[:100])
            ack = f"Got it — I'll remember that."
            yield AgentEvent(type="token", content=ack)
            self.record_turn("assistant", ack)
            yield AgentEvent(type="done", content="", done=True)
            return

        # --- Web search intent bypass: skip LLM tool-decision, call search_and_fetch directly ---
        web_query = self._is_web_search_intent(user_message)
        if web_query is not None:
            logger.info("Web search intent detected: %s", web_query)
            self.record_turn("user", user_message)
            self.conversation_history.append({"role": "user", "content": user_message})

            # Auto-grant browser permission if needed
            if not db.get_permission("browser"):
                db.set_permission("browser", True)
                logger.info("Auto-granted browser permission for web search intent")

            yield AgentEvent(type="status", content="Searching the web...")

            # Call search_and_fetch directly
            tool_result = await self.mcp.call_tool("search_and_fetch", {"query": web_query})
            logger.info("Web search result: %d chars", len(tool_result))

            # Clean the content — strip navigation, ads, "More", "Follow" etc.
            clean_result = self._clean_web_content(tool_result)
            logger.info("Cleaned content: %d chars", len(clean_result))

            # Add cleaned content to history for LLM summarization
            self.conversation_history.append({
                "role": "tool",
                "content": f"Web search results for '{web_query}':\n{clean_result}",
            })
            self._trim_history()
            self.record_turn("tool", clean_result)
            db.log_activity("browser", "search_and_fetch", web_query[:100])

            # Let LLM synthesize the result — use focused summarization prompt
            yield AgentEvent(type="status", content="Summarizing results...")
            messages = self._summarize_web_content(web_query, clean_result)

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
                self.record_turn("assistant", full_response)
            else:
                # Fallback: return cleaned content directly
                yield AgentEvent(type="token", content=clean_result)
                self.conversation_history.append({"role": "assistant", "content": clean_result})
                self.record_turn("assistant", clean_result)

            yield AgentEvent(type="done", content="", done=True)
            return

        # --- Normal flow ---
        self.record_turn("user", user_message)
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

            yield AgentEvent(type="status", content="Thinking..." if round_num == 0 else "Reviewing results...")

            full_response = ""
            token_buffer = []

            # Pass GBNF grammar on tool-capable rounds (enforces valid JSON output)
            grammar = self._tool_grammar if self._tool_grammar else None
            import time as _time
            round_start = _time.monotonic()
            async for token in self.model.generate(messages=messages, max_tokens=512, grammar=grammar):
                if token.startswith("{") and "finish_reason" in token:
                    continue
                full_response += token
                token_buffer.append(token)
            elapsed = _time.monotonic() - round_start

            logger.info("LLM response (round %d, %.1fs): %s", round_num + 1, elapsed, full_response[:200])

            # Slowness detection: if CPU-only and round took >15s, show one-time tip
            if not self._perf_tip_shown and elapsed > 15:
                from .model_loader import _get_total_vram_mb
                vram = _get_total_vram_mb()
                if vram == 0:
                    self._perf_tip_shown = True
                    yield AgentEvent(
                        type="perf_tip",
                        content="Running on CPU only — replies may be faster with a smaller model in Settings.",
                    )

            # --- Check for final_answer (grammar-constrained: model wants to stop tool calling) ---
            final_answer = None
            if '"final_answer"' in full_response and '{' in full_response:
                try:
                    parsed = json.loads(full_response)
                    final_answer = parsed.get("final_answer")
                except json.JSONDecodeError:
                    pass

            if final_answer is not None:
                # Don't yield raw JSON tokens — extract the answer string and send it as prose
                yield AgentEvent(type="token", content=final_answer)
                self.conversation_history.append({"role": "assistant", "content": final_answer})
                self._trim_history()
                self.record_turn("assistant", final_answer)
                yield AgentEvent(type="done", content="", done=True)
                return

            # --- Check for tool call ---
            tool_call = None
            if '"tool"' in full_response and '{' in full_response:
                tool_call = self._extract_tool_call(full_response)

            if tool_call is not None and not self._is_prompt_echo(full_response):
                tool_name = tool_call["tool"]
                # GBNF schema may nest args as {tool_name: {...}} — unwrap if so
                raw_args = tool_call["arguments"]
                tool_args = raw_args.get(tool_name, raw_args)

                logger.info("Tool call parsed: %s args=%s", tool_name, json.dumps(tool_args)[:200])

                # --- Duplicate tool call guard: break loop if same tool called twice in a row ---
                if (self._last_tool_call and
                    self._last_tool_call[0] == tool_name and
                    self._last_tool_call[1] == tool_args):
                    logger.warning("Duplicate tool call detected: %s — forcing final response", tool_name)
                    async for event in self._final_response_round():
                        yield event
                    return
                self._last_tool_call = (tool_name, tool_args)

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

                # --- Permission gate: check scope before executing ---
                scope = self._tool_scope(tool_name)
                if scope != "other" and not db.get_permission(scope):
                    self._pending_state = {
                        "type": "confirm",
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "permission_scope": scope,
                    }
                    yield AgentEvent(
                        type="permission_request",
                        content=f"Luna wants to access your **{scope}**. Allow?",
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                    yield AgentEvent(type="done", content="", done=True)
                    return

                # --- Execute tool ---
                # Redirect search_web -> search_and_fetch (model keeps choosing wrong tool)
                if tool_name == "search_web":
                    logger.info("Redirecting search_web -> search_and_fetch for query: %s", tool_args.get("query", ""))
                    tool_name = "search_and_fetch"

                _tool_status = {
                    "search_and_fetch": "Searching the web...",
                    "search_web": "Opening browser...",
                    "fetch_page": "Fetching web page...",
                    "read_file": "Reading file...",
                    "read_pdf": "Reading PDF...",
                    "write_file": "Writing file...",
                    "list_directory": "Listing files...",
                    "create_reminder": "Setting reminder...",
                    "list_reminders": "Checking reminders...",
                    "draft_email": "Drafting email...",
                    "create_note": "Saving note...",
                    "glob_search": "Searching files...",
                }
                status_msg = _tool_status.get(tool_name, f"Using {tool_name}...")
                logger.info("Emitting status: %s", status_msg)
                yield AgentEvent(type="status", content=status_msg)
                yield AgentEvent(
                    type="tool_call",
                    content=f"Using {tool_name}...",
                    tool_name=tool_name,
                    tool_args=tool_args,
                )

                try:
                    logger.info("Calling mcp.call_tool(%s, %s)", tool_name, tool_args)
                    tool_result = await self.mcp.call_tool(tool_name, tool_args)
                    logger.info("Tool result received: %s chars", len(tool_result))
                except Exception as e:
                    tool_result = json.dumps({"error": str(e)})
                    logger.exception("Tool dispatch failed for %s", tool_name)

                self._tool_call_count += 1

                yield AgentEvent(
                    type="tool_result",
                    content=tool_result,
                    tool_name=tool_name,
                    tool_result=tool_result,
                )

                # Store tool result in history — enough context for LLM to synthesize
                summary_response = full_response
                limit = 2500 if tool_name in {"search_and_fetch", "fetch_page", "read_file", "read_pdf"} else 400
                summary_result = tool_result[:limit] + "..." if len(tool_result) > limit else tool_result
                self.conversation_history.append({"role": "assistant", "content": summary_response})
                self.conversation_history.append({
                    "role": "tool",
                    "content": f"Tool '{tool_name}' result:\n{summary_result}",
                })
                self._trim_history()

                # Persist to database
                self.record_turn("assistant", summary_response)
                self.record_turn("tool", summary_result)
                scope = self._tool_scope(tool_name)
                db.log_activity(scope, tool_name, self._tool_ack_message(tool_name, tool_args, tool_result)[:100])

                # If it's a direct action tool (like saving a file), emit ack and stop.
                # If it's a data gathering tool (like search_and_fetch), continue the loop
                # so the LLM synthesizes an answer from the content.
                if '"error"' not in tool_result and tool_name in self._DIRECT_ACK_TOOLS:
                    ack = self._tool_ack_message(tool_name, tool_args, tool_result)
                    yield AgentEvent(type="token", content="\n\n" + ack)
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
                    self.record_turn("assistant", full_response)
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
        tool_args = dict(pending.get("tool_args", {}))

        # Special case: reminder clarify — user provides full title, re-parse
        if tool_name == "create_reminder":
            self.conversation_history.append({"role": "user", "content": user_message})
            self.record_turn("user", user_message)
            # Re-run reminder detection on the combined context
            reminder_msg = user_response.strip()
            intent = self._is_reminder_intent(f"remind me to {reminder_msg}")
            if intent and intent.get("action") == "create":
                async for event in self._handle_reminder_intent(intent):
                    yield event
                return
            # If still can't parse, just create with what they gave us
            title = user_response.strip().rstrip(".,")
            due_date = datetime.now().date().isoformat()
            tool_args = {"title": title, "due_date": due_date}
            yield AgentEvent(type="thinking", content="Creating reminder...")
            try:
                tool_result = await self.mcp.call_tool("create_reminder", tool_args)
            except Exception as e:
                yield AgentEvent(type="token", content=f"Error creating reminder: {e}")
                yield AgentEvent(type="done", content="", done=True)
                return
            self._tool_call_count += 1
            summary = f"Reminder: {title} on {due_date}"
            self.record_turn("assistant", summary)
            scope = self._tool_scope("create_reminder")
            db.log_activity(scope, "create_reminder", summary[:100])
            yield AgentEvent(type="tool_call", content=f"Creating reminder: {title}", tool_name="create_reminder", tool_args=tool_args)
            yield AgentEvent(type="tool_result", content=tool_result, tool_name="create_reminder", tool_result=tool_result)
            ack = f"Reminder set: **{title}** on {due_date}."
            yield AgentEvent(type="token", content=ack)
            self.conversation_history.append({"role": "assistant", "content": ack})
            self._trim_history()
            yield AgentEvent(type="done", content="", done=True)
            return

        field_name = pending.get("missing_field")
        remaining = list(pending.get("remaining_fields", []))

        # Fill the answered field
        tool_args[field_name] = user_response.strip()

        # Add exchange to history so LLM sees the clarification
        self.conversation_history.append({"role": "user", "content": user_message})
        self.record_turn("user", user_message)

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

        # Permission gate
        scope = self._tool_scope(tool_name)
        if scope != "other" and not db.get_permission(scope):
            self._pending_state = {
                "type": "confirm",
                "tool_name": tool_name,
                "tool_args": tool_args,
                "permission_scope": scope,
            }
            yield AgentEvent(
                type="permission_request",
                content=f"Luna wants to access your **{scope}**. Allow?",
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
        self.record_turn("tool", summary_result)
        scope = self._tool_scope(tool_name)
        db.log_activity(scope, tool_name, self._tool_ack_message(tool_name, tool_args, tool_result)[:100])

        # Data tools: let LLM synthesize. Direct ack tools: emit ack and stop.
        if '"error"' not in tool_result and tool_name in self._DIRECT_ACK_TOOLS:
            ack = self._tool_ack_message(tool_name, tool_args, tool_result)
            yield AgentEvent(type="token", content="\n\n" + ack)
            self.conversation_history.append({"role": "assistant", "content": ack})
            self._trim_history()
            yield AgentEvent(type="done", content="", done=True)
        else:
            async for event in self._final_response_round():
                yield event

    async def _handle_confirm_resume(
        self, pending: dict, user_response: str
    ) -> AsyncIterator[AgentEvent]:
        """Handle user's yes/no confirmation or permission grant."""
        tool_name = pending["tool_name"]
        tool_args = pending["tool_args"]
        permission_scope = pending.get("permission_scope")

        answer = user_response.strip().lower()
        if answer not in ("yes", "y", "confirm", "ok", "sure", "do it", "go"):
            yield AgentEvent(
                type="token",
                content="Okay, cancelled.",
            )
            yield AgentEvent(type="done", content="", done=True)
            return

        # If this was a permission request, persist the grant
        if permission_scope:
            db.set_permission(permission_scope, True)
            logger.info("Permission granted: %s", permission_scope)
            db.log_activity(permission_scope, "permission", f"User granted {permission_scope} access")

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
        self.record_turn("tool", summary_result)
        scope = self._tool_scope(tool_name)
        db.log_activity(scope, tool_name, self._tool_ack_message(tool_name, tool_args, tool_result)[:100])

        # Data tools: let LLM synthesize. Direct ack tools: emit ack and stop.
        if '"error"' not in tool_result and tool_name in self._DIRECT_ACK_TOOLS:
            ack = self._tool_ack_message(tool_name, tool_args, tool_result)
            yield AgentEvent(type="token", content="\n\n" + ack)
            self.conversation_history.append({"role": "assistant", "content": ack})
            self._trim_history()
            yield AgentEvent(type="done", content="", done=True)
        else:
            async for event in self._final_response_round():
                yield event

    async def _final_response_round(self) -> AsyncIterator[AgentEvent]:
        """One more LLM round to generate a natural language response after tool execution.
        Runs WITHOUT grammar constraints so the LLM can write prose.
        Streams tokens and sentence-chunks for TTS audio generation."""
        messages = self._build_messages()

        yield AgentEvent(type="thinking", content="Formulating answer...")

        full_response = ""
        sentence_buffer = []

        async for token in self.model.generate(messages=messages, max_tokens=768):
            if token.startswith("{") and "finish_reason" in token:
                continue
            full_response += token
            sentence_buffer.append(token)

            # Emit token immediately for text streaming
            yield AgentEvent(type="token", content=token)

            # Check for sentence boundary — generate TTS audio
            current_text = "".join(sentence_buffer)
            if current_text.rstrip() and current_text.rstrip()[-1] in ".!?":
                sentence = current_text.strip()
                sentence_buffer.clear()

                # Generate TTS audio in background (don't block token streaming)
                try:
                    from .voice import tts_manager
                    audio_path = await tts_manager.synthesize(sentence)
                    if audio_path:
                        yield AgentEvent(type="audio", content=audio_path)
                except Exception as e:
                    logger.debug("TTS generation skipped: %s", e)

        # Flush remaining text as final sentence
        remainder = "".join(sentence_buffer).strip()
        if remainder:
            try:
                from .voice import tts_manager
                audio_path = await tts_manager.synthesize(remainder)
                if audio_path:
                    yield AgentEvent(type="audio", content=audio_path)
            except Exception:
                pass

        if self._is_valid_response(full_response):
            self.conversation_history.append({"role": "assistant", "content": full_response})
            self._trim_history()
            self.record_turn("assistant", full_response)
        else:
            # Replace the streamed "Done!" — already emitted, just save
            self.conversation_history.append({"role": "assistant", "content": "Done!"})
            self.record_turn("assistant", "Done!")

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
            return f"Move `{tool_args.get('source', '?')}` -> `{tool_args.get('destination', '?')}`?"
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

    def _clean_web_content(self, text: str) -> str:
        """Strip navigation, ads, and noise from scraped web content.
        Returns clean article text suitable for LLM summarization."""
        lines = text.split('\n')
        cleaned = []
        skip_patterns = re.compile(
            r'^(More|Follow|Follow this topic|Share|See more|'
            r'chevron_right|Source:|Headlines|'
            r'By \w+ \w+|[\d]+ (hour|minute|day|week|month)s? ago|'
            r'[\w\s]+\.com|[\w\s]+\.in|[\w\s]+\.org|'
            r'^\s*$)',
            re.IGNORECASE
        )
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if skip_patterns.match(stripped):
                continue
            if len(stripped) < 5:
                continue
            cleaned.append(stripped)

        result = '\n'.join(cleaned)
        # Cap at 1200 chars for 1.5B model context
        if len(result) > 1200:
            result = result[:1200] + "\n[Content truncated]"
        return result

    def _summarize_web_content(self, query: str, content: str) -> str:
        """Directly summarize web content without full ReAct loop.
        Uses a focused prompt to force the LLM to summarize, not echo."""
        messages = [
            {"role": "system", "content": (
                "You are a helpful assistant. Summarize the web content below in 2-3 sentences. "
                "Focus on the key facts. Do NOT repeat the raw content. Do NOT say 'I found' or 'Here are'. "
                "Just state the facts directly."
            )},
            {"role": "user", "content": f"Summarize this for the query '{query}':\n\n{content}"},
        ]
        return messages

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
            return f"Moved `{tool_args.get('source', '?')}` -> `{tool_args.get('destination', '?')}`."
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
        elif tool_name == "create_reminder":
            title = tool_args.get("title", "reminder")
            due_date = tool_args.get("due_date", "")
            due_time = tool_args.get("due_time", "")
            time_str = f" at {due_time}" if due_time else ""
            return f"Reminder set: **{title}** on {due_date}{time_str}. Calendar event created."
        elif tool_name == "list_reminders":
            return "Listed reminders."
        elif tool_name == "delete_reminder":
            return "Reminder deleted."
        elif tool_name == "search_and_fetch":
            return f"Searched and fetched content."
        elif tool_name == "fetch_page":
            return f"Fetched page content."
        elif tool_name == "read_pdf":
            return f"Extracted text from PDF."
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

        # Inject recent memories for personal context
        memories = db.get_memories(limit=5)
        if memories:
            memory_lines = [f"- {m['content']}" for m in memories]
            system += "\n\nWhat you know about the user:\n" + "\n".join(memory_lines)

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

    # --- Memory intent detection ---

    _MEMORY_PATTERNS = [
        re.compile(r'\bremember\s+that\s+(.+)', re.IGNORECASE),
        re.compile(r'\bremember\s+(.+)', re.IGNORECASE),
        re.compile(r'\bmy\s+favorite\s+(.+?)\s+is\s+(.+)', re.IGNORECASE),
        re.compile(r'\b(i\s+prefer|i\s+like|i\s+use|i\s+want)\s+(.+)', re.IGNORECASE),
        re.compile(r'\bnote\s+that\s+(.+)', re.IGNORECASE),
        re.compile(r'\bdon.t\s+forget\s+that\s+(.+)', re.IGNORECASE),
    ]

    # --- Web search intent patterns ---
    _WEB_SEARCH_PATTERNS = [
        re.compile(r'\b(search|look\s*up|find|google|browse|check)\b.{0,30}\b(for|about|on)\b', re.IGNORECASE),
        re.compile(r'\b(what(\'s| is| are)|latest|recent|current|trending|news)\b.{0,40}\b(on|in|about|today|now)\b', re.IGNORECASE),
        re.compile(r'\b(tell me about|what do you know about|what\'s happening)\b', re.IGNORECASE),
        re.compile(r'\b(summarize|research|look into|dig into)\b', re.IGNORECASE),
    ]

    def _is_web_search_intent(self, message: str) -> str | None:
        """
        Detect if the user wants to search the web.
        Returns the search query if detected, None otherwise.
        Fires before model.generate() — zero LLM involvement.
        """
        for pattern in self._WEB_SEARCH_PATTERNS:
            if pattern.search(message):
                # Use the full message as the search query
                return message.strip()
        return None

    def _is_memory_intent(self, message: str) -> str | None:
        """
        Detect if the user wants to save a memory/preference.
        Returns the memory content if detected, None otherwise.
        Fires before model.generate() — zero LLM involvement.
        """
        for pattern in self._MEMORY_PATTERNS:
            m = pattern.search(message)
            if m:
                # For patterns with two groups (favorite X is Y), combine them
                if m.lastindex and m.lastindex >= 2:
                    return f"{m.group(1).strip()} is {m.group(2).strip()}"
                return m.group(1).strip().rstrip(".")
        return None

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
        # Match verb + "email/mail" OR bare "email to X" / "mail to X"
        has_verb = self._COMPOSE_VERBS.search(message)
        has_noun = self._COMPOSE_NOUN.search(message)
        has_email_to = re.search(r'\bemail\s+to\s+', message, re.IGNORECASE)
        has_mail_to = re.search(r'\bmail\s+to\s+', message, re.IGNORECASE)

        if not (has_verb and has_noun) and not has_email_to and not has_mail_to:
            return None

        # Try regex extraction first
        slots = {}
        for pattern in self._TO_PATTERNS:
            m = pattern.search(message)
            if m:
                to_val = m.group(1).strip().rstrip('.')
                if '@' in to_val or '.' in to_val:
                    slots["to"] = to_val
                break
        for pattern in self._SUBJECT_PATTERNS:
            m = pattern.search(message)
            if m:
                slots["subject"] = m.group(1).strip().rstrip('.')
                break
        for pattern in self._BODY_PATTERNS:
            m = pattern.search(message)
            if m:
                slots["body"] = m.group(1).strip().rstrip('.')
                break

        # If regex didn't extract enough, use LLM to fill gaps
        if not slots.get("to") or not slots.get("subject"):
            logger.info("Regex extraction incomplete (slots=%s), using LLM to extract", slots)
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're inside an async context — run LLM extraction synchronously
                    extraction_prompt = (
                        "Extract email fields from this message. "
                        "Return ONLY a JSON object with keys: to, subject, body. "
                        "Use empty string for missing fields. "
                        f"Message: {message}"
                    )
                    messages = [
                        {"role": "system", "content": "You extract email fields from user messages. Return only JSON."},
                        {"role": "user", "content": extraction_prompt},
                    ]
                    # Run LLM in a thread to avoid blocking
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        future = pool.submit(
                            lambda: list(self.model.generate(messages=messages, max_tokens=256))
                        )
                        tokens = future.result(timeout=30)
                    response = "".join(tokens)
                    # Parse JSON from response
                    import json as _json
                    # Find JSON in response
                    start = response.find('{')
                    end = response.rfind('}') + 1
                    if start >= 0 and end > start:
                        extracted = _json.loads(response[start:end])
                        if not slots.get("to") and extracted.get("to"):
                            slots["to"] = extracted["to"]
                        if not slots.get("subject") and extracted.get("subject"):
                            slots["subject"] = extracted["subject"]
                        if not slots.get("body") and extracted.get("body"):
                            slots["body"] = extracted["body"]
                        logger.info("LLM extracted slots: %s", slots)
            except Exception as e:
                logger.warning("LLM slot extraction failed: %s", e)

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
        # "downloads looks messy" -> noun alone is enough if it's a known folder
        if not has_verb:
            # Only trigger on noun if the message also implies disorder
            disorder_words = re.compile(
                r'\b(messy|cluttered|disorganized|chaotic|full of|clean|tidy|sort)\b',
                re.IGNORECASE,
            )
            if not disorder_words.search(message):
                return None

        # Try to extract a specific folder reference
        # First check for compound paths: "documents in downloads", "photos under desktop"
        # Also handles "documents folder in the downloads folder"
        compound_match = re.search(
            r'\b(\w+)\s+(?:folder|dir|directory)?\s*(?:in|under|inside|within|of)\s+(?:the\s+)?(\w+)\s*(?:folder|dir|directory)?\b',
            message, re.IGNORECASE,
        )
        if compound_match:
            subfolder = compound_match.group(1).lower()
            parent = compound_match.group(2).lower()
            from pathlib import Path
            parent_path = None
            child_path = None
            for keyword, alias in self._PATH_KEYWORDS.items():
                if keyword == parent:
                    parent_path = Path.home() / alias
                if keyword == subfolder:
                    child_path = alias
            if parent_path and child_path and parent_path.exists():
                full_path = parent_path / child_path
                if full_path.exists():
                    return str(full_path)

        # Single folder keywords
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

    # --- Reminder intent detection ---
    # Broad regex: matches any natural phrasing that implies "create/set/put a reminder"
    _REMINDER_VERBS = re.compile(
        r'\b(remind\s+me|'
        r'(?:create|set|put|add|make|schedule|build|note|write|establish|arrange|prepare|compose|send|fire|trigger|issue|generate|produce|design|construct|assemble|organize|prepare)\s+(?:a|an)?\s*(?:quick\s+|simple\s+|new\s+|short\s+|fast\s+|quick\s+)?(?:reminder|alarm|notification|alert|ping|memo|note|task)\b|'
        r'(?:a|an)\s*(?:quick\s+|simple\s+|new\s+|short\s+|fast\s+)?(?:reminder|alarm|notification|alert|ping|memo|note|task)\s+(?:for|about|regarding|concerning|on|at|in)\b|'
        r'don\'?t\s+forget\s+(?:to|about)|'
        r'remember\s+to|'
        r'i\s+need\s+(?:a\s+)?(?:to\s+)?(?:be\s+)?remind(?:ed)?|'
        r'can\s+you\s+(?:set|create|put|make|add)\s+(?:a|an)?\s*(?:reminder|alarm|alert)|'
        r'could\s+you\s+(?:set|create|put|make|add)\s+(?:a|an)?\s*(?:reminder|alarm|alert)|'
        r'(?:set|create|put|make|add)\s+me\s+(?:a|an)?\s*(?:reminder|alarm|alert))\b',
        re.IGNORECASE,
    )
    _REMINDER_LIST_PATTERNS = re.compile(
        r'\b(what|show|list|check|any)\b.*\b(reminders?|tasks?|todo|to-do|schedule)\b',
        re.IGNORECASE,
    )
    _REMINDER_DELETE_PATTERNS = re.compile(
        r'\b(delete|remove|cancel|clear)\b.*\b(reminder|task|todo)\b',
        re.IGNORECASE,
    )
    _DATE_PATTERNS = [
        re.compile(r'\b(today)\b', re.IGNORECASE),
        re.compile(r'\b(tomorrow)\b', re.IGNORECASE),
        re.compile(r'\b(next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b', re.IGNORECASE),
        re.compile(r'\b(\d{4}-\d{2}-\d{2})\b'),  # YYYY-MM-DD
        re.compile(r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b'),  # MM/DD/YYYY
        re.compile(r'\b(\d{1,2}-\d{1,2}-\d{2,4})\b'),  # MM-DD-YYYY
    ]
    _TIME_PATTERNS = [
        re.compile(r'\bat\s+(\d{1,2}:\d{2})\b', re.IGNORECASE),
        re.compile(r'\bat\s+(\d{1,2})\s*(am|pm)\b', re.IGNORECASE),
        re.compile(r'@(\d{1,2})\s*(am|pm)\b', re.IGNORECASE),
        re.compile(r'@(\d{1,2}:\d{2})\b', re.IGNORECASE),
        re.compile(r'\b(\d{1,2}:\d{2})\b'),
    ]

    _OFFSET_PATTERN = re.compile(
        r'\b(\d+)\s*(min(?:ute)?s?|hours?|hrs?)\s*(before|early|prior|ago)\b',
        re.IGNORECASE,
    )

    # Words that indicate the title extraction went wrong (captured offset/time fragments)
    _TITLE_GARBLED_RE = re.compile(
        r'\b(before|after|early|late|mins?|minutes?|hours?|hrs?|\d+\s*(am|pm)|@\d)\b',
        re.IGNORECASE,
    )

    def _parse_relative_date(self, date_str: str) -> str | None:
        """Convert relative date strings to YYYY-MM-DD."""
        today = datetime.now().date()
        lower = date_str.lower().strip()

        if lower == "today":
            return today.isoformat()
        elif lower == "tomorrow":
            return (today + timedelta(days=1)).isoformat()
        elif lower.startswith("next "):
            day_name = lower.replace("next ", "").strip()
            days = {
                "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6,
            }
            target = days.get(day_name)
            if target is not None:
                current = today.weekday()
                delta = (target - current) % 7
                if delta == 0:
                    delta = 7
                return (today + timedelta(days=delta)).isoformat()
        elif re.match(r'\d{4}-\d{2}-\d{2}$', date_str):
            return date_str  # Already YYYY-MM-DD
        elif re.match(r'\d{1,2}/\d{1,2}/\d{2,4}$', date_str):
            parts = date_str.split("/")
            month, day = int(parts[0]), int(parts[1])
            year = int(parts[2]) if len(parts) > 2 else today.year
            if year < 100:
                year += 2000
            try:
                return f"{year:04d}-{month:02d}-{day:02d}"
            except ValueError:
                return None
        elif re.match(r'\d{1,2}-\d{1,2}-\d{2,4}$', date_str):
            parts = date_str.split("-")
            month, day = int(parts[0]), int(parts[1])
            year = int(parts[2]) if len(parts) > 2 else today.year
            if year < 100:
                year += 2000
            try:
                return f"{year:04d}-{month:02d}-{day:02d}"
            except ValueError:
                return None

        return None

    def _parse_relative_time(self, time_str: str) -> str:
        """Convert 12h time strings to HH:MM (24h)."""
        time_str = time_str.strip().lower()
        m = re.match(r'(\d{1,2})\s*(am|pm)', time_str)
        if m:
            hour = int(m.group(1))
            period = m.group(2)
            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0
            return f"{hour:02d}:00"
        # Already HH:MM
        if re.match(r'\d{1,2}:\d{2}$', time_str):
            return time_str
        return ""

    def _is_reminder_intent(self, message: str) -> dict | None:
        """
        Detect if the user wants to create, list, or delete reminders.
        Returns action dict if detected, None otherwise.
        Fires before model.generate() — zero LLM involvement.
        """
        # List reminders
        if self._REMINDER_LIST_PATTERNS.search(message):
            # Check if they want reminders for a specific date
            for pattern in self._DATE_PATTERNS:
                m = pattern.search(message)
                if m:
                    date_str = self._parse_relative_date(m.group(1))
                    if date_str:
                        return {"action": "list", "date": date_str}
            return {"action": "list"}

        # Delete reminder — needs a reminder_id, fall through to LLM for now
        if self._REMINDER_DELETE_PATTERNS.search(message):
            # Can't extract ID from prose — let LLM handle via list + delete
            return None

        # Create reminder
        if self._REMINDER_VERBS.search(message):
            title = ""
            title_match = re.search(
                r'(?:remind\s+me\s+(?:to|about|for)?|create\s+a?\s*reminder\s+(?:to|for|about)?|set\s+a?\s*reminder\s+(?:to|for|about)?|add\s+a?\s*reminder\s+(?:to|for|about)?|schedule\s+a?\s*reminder\s+(?:to|for|about)?)\s*(.+?)(?:\s+(?:on|at|by|before|tomorrow|today|next|@\d|in\s+\d+|within\s+\d+)\b|$)',
                message, re.IGNORECASE,
            )
            if title_match:
                candidate = title_match.group(1).strip().rstrip(".,")
                if candidate and not self._TITLE_GARBLED_RE.search(candidate):
                    title = candidate

            if not title:
                fallback_match = re.search(
                    r'^(.+?)(?:\s+(?:before|after|at|on|by|tomorrow|today|next|@\d|\d+\s*(?:am|pm)|in\s+\d+|within\s+\d+)\b|$)',
                    message, re.IGNORECASE,
                )
                if fallback_match:
                    candidate = fallback_match.group(1).strip().rstrip(".,")
                    if candidate and len(candidate) > 2 and not self._TITLE_GARBLED_RE.search(candidate):
                        title = candidate

            # Extract date (confident only)
            due_date = None
            for pattern in self._DATE_PATTERNS:
                m = pattern.search(message)
                if m:
                    parsed = self._parse_relative_date(m.group(1))
                    if parsed:
                        due_date = parsed
                        break
            if not due_date:
                due_date = datetime.now().date().isoformat()

            # Extract time — check relative expressions first ("in 15 minutes", "within 30 mins")
            due_time = None
            relative_match = re.search(
                r'\b(?:in|within|of)\s+([\d]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty)\s+(minutes?|mins?|hours?|hrs?)\b',
                message, re.IGNORECASE,
            )
            if relative_match:
                raw_amount = relative_match.group(1)
                # Convert word numbers to int ("three" -> 3, "five" -> 5)
                _WORD_NUMS = {
                    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
                    "an": 1, "a": 1,
                }
                amount = _WORD_NUMS.get(raw_amount.lower()) or int(raw_amount)
                unit = relative_match.group(2).lower()
                if "hour" in unit or "hr" in unit:
                    target = datetime.now() + timedelta(hours=amount)
                else:
                    target = datetime.now() + timedelta(minutes=amount)
                due_time = target.strftime("%H:%M")
                # If the relative time crosses midnight, use tomorrow's date
                if target.date() > datetime.now().date():
                    due_date = target.date().isoformat()
                logger.info("Relative time detected: %d %s -> %s on %s", amount, unit, due_time, due_date)
            else:
                # Try explicit time patterns
                for pattern in self._TIME_PATTERNS:
                    m = pattern.search(message)
                    if m:
                        parsed = self._parse_relative_time(m.group(1))
                        if parsed:
                            due_time = parsed
                            break

            return {"action": "create", "title": title, "due_date": due_date, "due_time": due_time or ""}

        return None

    async def _handle_reminder_intent(self, intent: dict) -> AsyncIterator[AgentEvent]:
        """Handle reminder intent — call tools directly, skip LLM."""
        action = intent.get("action", "create")

        if action == "list":
            date = intent.get("date")
            tool_args = {}
            if date:
                tool_args["date"] = date

            yield AgentEvent(type="thinking", content="Looking up reminders...")

            try:
                tool_result = await self.mcp.call_tool("list_reminders", tool_args)
            except Exception as e:
                yield AgentEvent(type="token", content=f"Error listing reminders: {e}")
                yield AgentEvent(type="done", content="", done=True)
                return

            self._tool_call_count += 1
            self.record_turn("assistant", f"Listed reminders for {date or 'all dates'}")
            scope = self._tool_scope("list_reminders")
            db.log_activity(scope, "list_reminders", f"Listed reminders ({date or 'all'})")

            # Parse and format
            try:
                reminders = json.loads(tool_result) if isinstance(tool_result, str) else tool_result
            except (json.JSONDecodeError, TypeError):
                reminders = []

            if not reminders:
                yield AgentEvent(type="token", content="No upcoming reminders found.")
            else:
                lines = [f"**{len(reminders)} reminder(s):**\n"]
                for r in reminders:
                    time_str = f" at {r['due_time']}" if r.get("due_time") else ""
                    lines.append(f"- **{r['title']}** — {r['due_date']}{time_str}")
                yield AgentEvent(type="token", content="\n".join(lines))

            yield AgentEvent(type="done", content="", done=True)
            return

        elif action == "clarify":
            question = intent.get("question", "What should the reminder be about?")
            # Store context so we can resume when user answers
            self._pending_state = {
                "type": "clarify",
                "tool_name": "create_reminder",
                "remaining_fields": ["title", "due_date", "due_time"],
                "tool_args": {},
            }
            yield AgentEvent(type="clarify", content=question)
            return

        elif action == "create":
            title = intent["title"]
            due_date = intent["due_date"]
            due_time = intent.get("due_time")

            tool_args = {"title": title, "due_date": due_date}
            if due_time:
                tool_args["due_time"] = due_time

            yield AgentEvent(type="thinking", content="Creating reminder...")

            try:
                tool_result = await self.mcp.call_tool("create_reminder", tool_args)
            except Exception as e:
                yield AgentEvent(type="token", content=f"Error creating reminder: {e}")
                yield AgentEvent(type="done", content="", done=True)
                return

            self._tool_call_count += 1

            # Persist via record_turn
            time_str = f" at {due_time}" if due_time else ""
            summary = f"Reminder: {title} on {due_date}{time_str}"
            self.record_turn("assistant", summary)
            scope = self._tool_scope("create_reminder")
            db.log_activity(scope, "create_reminder", summary[:100])

            # Emit tool events
            yield AgentEvent(
                type="tool_call",
                content=f"Creating reminder: {title}",
                tool_name="create_reminder",
                tool_args=tool_args,
            )
            yield AgentEvent(
                type="tool_result",
                content=tool_result,
                tool_name="create_reminder",
                tool_result=tool_result,
            )

            # Ack
            ack = f"Reminder set: **{title}** on {due_date}{time_str}."
            yield AgentEvent(type="token", content=ack)
            self.conversation_history.append({"role": "assistant", "content": ack})
            self._trim_history()
            yield AgentEvent(type="done", content="", done=True)
            return

    def _trim_history(self):
        before = len(self.conversation_history)
        if before > 20:
            self.conversation_history = self.conversation_history[-10:]
            logger.info("History trimmed: %d -> %d messages", before, len(self.conversation_history))

    _TOOL_SCOPES = {
        "read_file": "files", "write_file": "files", "list_directory": "files",
        "move_file": "files", "copy_file": "files", "delete_file": "files",
        "glob_search": "files", "mkdir": "files", "read_pdf": "files",
        "preview_organize": "files", "execute_organize": "files",
        "create_note": "notes", "list_notes": "notes",
        "search_notes": "notes", "delete_note": "notes",
        "open_browser": "browser", "search_web": "browser",
        "fetch_page": "browser", "search_and_fetch": "browser",
        "draft_email": "email", "open_email_client": "email", "list_drafts": "email",
        "create_reminder": "reminders", "list_reminders": "reminders", "delete_reminder": "reminders",
        "voice_transcribe": "microphone",
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
