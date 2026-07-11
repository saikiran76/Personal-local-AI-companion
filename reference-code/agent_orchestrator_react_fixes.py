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
    type: str  # token | clear | tool_call | tool_result | thinking | error | done | clarify | confirm | compose_form | reminder_form | status | perf_tip
    content: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    done: bool = False


class AgentOrchestrator:
    """
    ReAct agent with buffered streaming and interactive clarification.
    """
    
    # Tools that execute an action and DO NOT require the LLM to synthesize an answer.
    # Read-only/Information tools (like search, read_file) are excluded so the ReAct loop continues.
    _DIRECT_ACK_TOOLS = {
        "draft_email", "open_email_client", "delete_file", "move_file", 
        "create_note", "write_file", "open_browser", "create_reminder", 
        "delete_reminder", "execute_organize"
    }

    def __init__(self, model_loader: ModelLoader, mcp_manager: MCPClientManager):
        self.model = model_loader
        self.mcp = mcp_manager
        self.conversation_history: list[dict] = []
        self._tool_call_count = 0
        self._pending_state: dict | None = None
        self._organize_plans: dict[str, dict] = {}
        self._conversation_id: int | None = None
        self._tool_grammar = None
        self._perf_tip_shown = False

    def record_turn(self, role: str, content: str, tool_name: str = "", conversation_id: int | None = None) -> int:
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
        logger.info("Agent initialized with %d tools", len(tools))

        from .model_loader import get_model_tool_capability
        tool_cap = get_model_tool_capability(self.model.info.name)
        if tool_cap == "good":
            self._tool_grammar = build_tool_call_grammar(tools)

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

        # --- Escape hatch ---
        if self._pending_state and user_response is None:
            self._pending_state = None
            self._organize_plans.clear()

        # --- Bypasses (Compose, Organize, Reminders, Memory) ---
        compose_slots = self._is_compose_intent(user_message)
        if compose_slots is not None:
            self.record_turn("user", user_message)
            yield AgentEvent(type="compose_form", content=json.dumps(compose_slots), tool_name="draft_email", tool_args=compose_slots)
            return

        organize_path = self._is_organize_intent(user_message)
        if organize_path is not None:
            self.record_turn("user", user_message)
            async for event in self._handle_organize_intent(organize_path):
                yield event
            return

        reminder_intent = self._is_reminder_intent(user_message)
        if reminder_intent is not None:
            action = reminder_intent.get("action", "create")
            self.record_turn("user", user_message)
            if action == "list":
                date = reminder_intent.get("date")
                tool_args = {"date": date} if date else {}
                try:
                    tool_result = await self.mcp.call_tool("list_reminders", tool_args)
                except Exception as e:
                    tool_result = json.dumps({"error": str(e)})
                
                try:
                    reminders = json.loads(tool_result)
                    if isinstance(reminders, list) and reminders:
                        lines = []
                        for r in reminders:
                            t = r.get("title", "?")
                            d = r.get("due_date", "?")
                            tm = r.get("due_time", "")
                            lines.append(f"- **{t}** on {d}{' at ' + tm if tm else ''}")
                        content = "Your reminders:\n" + "\n".join(lines)
                    elif isinstance(reminders, list):
                        content = "No reminders found."
                    else:
                        content = str(reminders)
                except:
                    content = tool_result

                self.conversation_history.append({"role": "assistant", "content": content})
                self._trim_history()
                self.record_turn("assistant", content)
                yield AgentEvent(type="token", content=content)
                yield AgentEvent(type="done", content="", done=True)
                return
            elif action == "create":
                yield AgentEvent(type="reminder_form", content=json.dumps(reminder_intent), tool_name="create_reminder", tool_args=reminder_intent)
                return

        memory_content = self._is_memory_intent(user_message)
        if memory_content is not None:
            self.record_turn("user", user_message)
            db.add_memory("fact", memory_content)
            ack = f"Got it — I'll remember that."
            yield AgentEvent(type="token", content=ack)
            self.record_turn("assistant", ack)
            yield AgentEvent(type="done", content="", done=True)
            return

        # --- Normal ReAct flow ---
        self.record_turn("user", user_message)
        self.conversation_history.append({"role": "user", "content": user_message})

        model_name = self.model.info.name
        tool_cap = get_model_tool_capability(model_name)

        if tool_cap == "weak":
            async for event in self._stream_without_tools():
                yield event
            return

        for round_num in range(MAX_TOOL_ROUNDS):
            messages = self._build_messages()
            yield AgentEvent(type="status", content="Thinking..." if round_num == 0 else "Reviewing results...")

            full_response = ""
            token_buffer = []
            grammar = self._tool_grammar if self._tool_grammar else None
            
            import time as _time
            round_start = _time.monotonic()
            
            async for token in self.model.generate(messages=messages, max_tokens=768, grammar=grammar):
                if token.startswith("{") and "finish_reason" in token:
                    continue
                full_response += token
                token_buffer.append(token)
            
            elapsed = _time.monotonic() - round_start

            # Yield perf tip if slow (UI will clear it upon done)
            if not self._perf_tip_shown and elapsed > 15:
                from .model_loader import _get_total_vram_mb
                if _get_total_vram_mb() == 0:
                    self._perf_tip_shown = True
                    yield AgentEvent(
                        type="perf_tip",
                        content="Running on CPU only — this task may take a moment."
                    )

            final_answer = None
            if '"final_answer"' in full_response and '{' in full_response:
                try:
                    final_answer = json.loads(full_response).get("final_answer")
                except json.JSONDecodeError:
                    pass

            if final_answer is not None:
                yield AgentEvent(type="token", content=final_answer)
                self.conversation_history.append({"role": "assistant", "content": final_answer})
                self._trim_history()
                self.record_turn("assistant", final_answer)
                yield AgentEvent(type="done", content="", done=True)
                return

            tool_call = None
            if '"tool"' in full_response and '{' in full_response:
                tool_call = self._extract_tool_call(full_response)

            if tool_call is not None and not self._is_prompt_echo(full_response):
                tool_name = tool_call["tool"]
                raw_args = tool_call["arguments"]
                tool_args = raw_args.get(tool_name, raw_args)

                missing = self._validate_tool_args(tool_name, tool_args)
                if missing:
                    field_name = missing[0]
                    self._pending_state = {
                        "type": "clarify", "tool_name": tool_name, "tool_args": tool_args,
                        "missing_field": field_name, "remaining_fields": missing[1:]
                    }
                    yield AgentEvent(type="clarify", content=f"I need the **{field_name}** to use `{tool_name}`. What should I use?", tool_name=tool_name, tool_args=tool_args)
                    return

                if tool_name in _CONFIRMATION_TOOLS:
                    summary = self._format_confirm_summary(tool_name, tool_args)
                    self._pending_state = {"type": "confirm", "tool_name": tool_name, "tool_args": tool_args}
                    yield AgentEvent(type="confirm", content=summary, tool_name=tool_name, tool_args=tool_args)
                    return

                scope = self._tool_scope(tool_name)
                if scope != "other" and not db.get_permission(scope):
                    self._pending_state = {"type": "confirm", "tool_name": tool_name, "tool_args": tool_args, "permission_scope": scope}
                    yield AgentEvent(type="permission_request", content=f"Luna wants to access your **{scope}**. Allow?", tool_name=tool_name, tool_args=tool_args)
                    yield AgentEvent(type="done", content="", done=True)
                    return

                _tool_status = {
                    "search_and_fetch": "Searching the web...", "search_web": "Opening browser...",
                    "fetch_page": "Fetching web page...", "read_file": "Reading file...",
                    "list_directory": "Listing files...", "draft_email": "Drafting email..."
                }
                status_msg = _tool_status.get(tool_name, f"Using {tool_name}...")
                yield AgentEvent(type="status", content=status_msg)
                yield AgentEvent(type="tool_call", content=f"Using {tool_name}...", tool_name=tool_name, tool_args=tool_args)

                try:
                    tool_result = await self.mcp.call_tool(tool_name, tool_args)
                except Exception as e:
                    tool_result = json.dumps({"error": str(e)})

                self._tool_call_count += 1
                yield AgentEvent(type="tool_result", content=tool_result, tool_name=tool_name, tool_result=tool_result)

                # MASSIVE FIX: Provide enough context for Web Scrapers so the LLM can actually answer!
                summary_response = full_response
                limit = 2500 if tool_name in {"search_and_fetch", "fetch_page", "read_file", "read_pdf"} else 400
                summary_result = tool_result[:limit] + "..." if len(tool_result) > limit else tool_result
                
                self.conversation_history.append({"role": "assistant", "content": summary_response})
                self.conversation_history.append({"role": "tool", "content": f"Tool '{tool_name}' result:\n{summary_result}"})
                self._trim_history()

                self.record_turn("assistant", summary_response)
                self.record_turn("tool", summary_result)
                db.log_activity(self._tool_scope(tool_name), tool_name, self._tool_ack_message(tool_name, tool_args, tool_result)[:100])

                # MASSIVE FIX: If it's a direct action tool (like saving a file), emit ack and stop.
                # If it's a data gathering tool (like search_and_fetch), continue the loop so the LLM synthesizes!
                if '"error"' not in tool_result and tool_name in self._DIRECT_ACK_TOOLS:
                    ack = self._tool_ack_message(tool_name, tool_args, tool_result)
                    yield AgentEvent(type="token", content="\n\n" + ack)
                    self.conversation_history.append({"role": "assistant", "content": ack})
                    self._trim_history()
                    yield AgentEvent(type="done", content="", done=True)
                    return
                continue

            else:
                if self._is_valid_response(full_response):
                    for buffered_token in token_buffer:
                        yield AgentEvent(type="token", content=buffered_token)
                    self.conversation_history.append({"role": "assistant", "content": full_response})
                    self._trim_history()
                    self.record_turn("assistant", full_response)
                    yield AgentEvent(type="done", content="", done=True)
                    return
                else:
                    yield AgentEvent(type="token", content="I'm having trouble generating a good response. Could you rephrase?")
                    break

        yield AgentEvent(type="done", content="", done=True)


    async def _handle_clarify_resume(self, pending: dict, user_message: str, user_response: str) -> AsyncIterator[AgentEvent]:
        tool_name = pending["tool_name"]
        tool_args = dict(pending.get("tool_args", {}))

        if tool_name == "create_reminder":
            self.conversation_history.append({"role": "user", "content": user_message})
            self.record_turn("user", user_message)
            reminder_msg = user_response.strip()
            intent = self._is_reminder_intent(f"remind me to {reminder_msg}")
            if intent and intent.get("action") == "create":
                async for event in self._handle_reminder_intent(intent):
                    yield event
                return
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
            yield AgentEvent(type="tool_call", content=f"Creating reminder: {title}", tool_name="create_reminder", tool_args=tool_args)
            yield AgentEvent(type="tool_result", content=tool_result, tool_name="create_reminder", tool_result=tool_result)
            ack = f"Reminder set: **{title}** on {due_date}."
            yield AgentEvent(type="token", content=ack)
            self.conversation_history.append({"role": "assistant", "content": ack})
            self._trim_history()
            yield AgentEvent(type="done", content="", done=True)
            return

        field_name = pending.get("missing_field")
        tool_args[field_name] = user_response.strip()
        self.conversation_history.append({"role": "user", "content": user_message})
        self.record_turn("user", user_message)

        still_missing = self._validate_tool_args(tool_name, tool_args)
        if still_missing:
            next_field = still_missing[0]
            self._pending_state = {
                "type": "clarify", "tool_name": tool_name, "tool_args": tool_args,
                "missing_field": next_field, "remaining_fields": still_missing[1:]
            }
            yield AgentEvent(type="clarify", content=f"I also need the **{next_field}**. What should I use?", tool_name=tool_name, tool_args=tool_args)
            return

        if tool_name in _CONFIRMATION_TOOLS:
            self._pending_state = {"type": "confirm", "tool_name": tool_name, "tool_args": tool_args}
            yield AgentEvent(type="confirm", content=self._format_confirm_summary(tool_name, tool_args), tool_name=tool_name, tool_args=tool_args)
            return

        yield AgentEvent(type="tool_call", content=f"Using {tool_name}...", tool_name=tool_name, tool_args=tool_args)
        try:
            tool_result = await self.mcp.call_tool(tool_name, tool_args)
        except Exception as e:
            tool_result = json.dumps({"error": str(e)})

        self._tool_call_count += 1
        yield AgentEvent(type="tool_result", content=tool_result, tool_name=tool_name, tool_result=tool_result)
        
        limit = 2500 if tool_name in {"search_and_fetch", "fetch_page", "read_file", "read_pdf"} else 400
        summary_result = tool_result[:limit] + "..." if len(tool_result) > limit else tool_result
        self.conversation_history.append({"role": "tool", "content": f"Tool '{tool_name}' result:\n{summary_result}"})
        self._trim_history()
        self.record_turn("tool", summary_result)

        if '"error"' in tool_result or tool_name not in self._DIRECT_ACK_TOOLS:
            async for event in self._final_response_round():
                yield event
        else:
            ack = self._tool_ack_message(tool_name, tool_args, tool_result)
            yield AgentEvent(type="token", content="\n\n" + ack)
            self.conversation_history.append({"role": "assistant", "content": ack})
            self._trim_history()
            yield AgentEvent(type="done", content="", done=True)


    async def _handle_confirm_resume(self, pending: dict, user_response: str) -> AsyncIterator[AgentEvent]:
        tool_name = pending["tool_name"]
        tool_args = pending["tool_args"]
        permission_scope = pending.get("permission_scope")

        answer = user_response.strip().lower()
        if answer not in ("yes", "y", "confirm", "ok", "sure", "do it", "go"):
            yield AgentEvent(type="token", content="Okay, cancelled.")
            yield AgentEvent(type="done", content="", done=True)
            return

        if permission_scope:
            db.set_permission(permission_scope, True)

        yield AgentEvent(type="tool_call", content=f"Using {tool_name}...", tool_name=tool_name, tool_args=tool_args)
        try:
            tool_result = await self.mcp.call_tool(tool_name, tool_args)
        except Exception as e:
            tool_result = json.dumps({"error": str(e)})

        self._tool_call_count += 1
        yield AgentEvent(type="tool_result", content=tool_result, tool_name=tool_name, tool_result=tool_result)

        limit = 2500 if tool_name in {"search_and_fetch", "fetch_page", "read_file", "read_pdf"} else 400
        summary_result = tool_result[:limit] + "..." if len(tool_result) > limit else tool_result
        self.conversation_history.append({"role": "tool", "content": f"Tool '{tool_name}' result:\n{summary_result}"})
        self._trim_history()
        self.record_turn("tool", summary_result)

        if '"error"' in tool_result or tool_name not in self._DIRECT_ACK_TOOLS:
            async for event in self._final_response_round():
                yield event
        else:
            ack = self._tool_ack_message(tool_name, tool_args, tool_result)
            yield AgentEvent(type="token", content="\n\n" + ack)
            self.conversation_history.append({"role": "assistant", "content": ack})
            self._trim_history()
            yield AgentEvent(type="done", content="", done=True)

    async def _final_response_round(self) -> AsyncIterator[AgentEvent]:
        messages = self._build_messages()
        yield AgentEvent(type="thinking", content="Formulating answer...")
        full_response = ""
        token_buffer = []

        async for token in self.model.generate(messages=messages, max_tokens=768):
            if token.startswith("{") and "finish_reason" in token:
                continue
            full_response += token
            token_buffer.append(token)

        if self._is_valid_response(full_response):
            # Prepend some line breaks if we are appending to a tool badge in UI
            if self._tool_call_count > 0:
                yield AgentEvent(type="token", content="\n\n")
            for buffered_token in token_buffer:
                yield AgentEvent(type="token", content=buffered_token)
            self.conversation_history.append({"role": "assistant", "content": full_response})
            self._trim_history()
        else:
            yield AgentEvent(type="token", content="Done!")
            self.conversation_history.append({"role": "assistant", "content": "Done!"})

        yield AgentEvent(type="done", content="", done=True)


    def _validate_tool_args(self, tool_name: str, arguments: dict) -> list[str]:
        schema = self.mcp.get_tool_schema(tool_name)
        if not schema: return []
        required = schema.get("inputSchema", {}).get("required", [])
        missing = []
        for r in required:
            val = arguments.get(r)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing.append(r)
        return missing

    def _format_confirm_summary(self, tool_name: str, tool_args: dict) -> str:
        if tool_name == "delete_file": return f"⚠️ Delete file `{tool_args.get('path', '?')}`? This cannot be undone."
        elif tool_name == "move_file": return f"Move `{tool_args.get('source', '?')}` → `{tool_args.get('destination', '?')}`?"
        elif tool_name == "execute_organize":
            plan_id = tool_args.get("plan_id", "")
            plan = self._organize_plans.get(plan_id)
            if plan: return f"Organize `{plan['path']}`: {plan['summary']}?"
            return f"Execute organize plan `{plan_id}`?"
        elif tool_name == "draft_email": return f'Save email draft to `{tool_args.get("to", "?")}`?'
        elif tool_name == "open_email_client": return "Open your email client with the draft?"
        return f"Confirm: `{tool_name}` with {json.dumps(tool_args)}?"

    def _tool_ack_message(self, tool_name: str, tool_args: dict, tool_result: str) -> str:
        if tool_name == "draft_email": return "Draft saved."
        elif tool_name == "open_email_client": return "Opened your email client."
        elif tool_name == "delete_file": return f"Deleted `{tool_args.get('path', 'file')}`."
        elif tool_name == "move_file": return f"Moved `{tool_args.get('source', '?')}` → `{tool_args.get('destination', '?')}`."
        elif tool_name == "create_note": return f"Note saved as `{tool_args.get('filename', 'note')}`."
        elif tool_name == "write_file": return f"Wrote to `{tool_args.get('path', 'file')}`."
        elif tool_name == "open_browser": return f"Opened browser."
        elif tool_name == "create_reminder": return f"Reminder set."
        elif tool_name == "delete_reminder": return "Reminder deleted."
        return f"Done — {tool_name} completed."

    async def _stream_without_tools(self) -> AsyncIterator[AgentEvent]:
        messages = self._build_messages()
        async for token in self.model.generate(messages=messages, max_tokens=512):
            if token.startswith("{") and "finish_reason" in token: continue
            yield AgentEvent(type="token", content=token)
        yield AgentEvent(type="done", content="", done=True)

    def _build_messages(self) -> list[dict]:
        tool_cap = get_model_tool_capability(self.model.info.name)
        if tool_cap == "good":
            tool_descriptions = self.mcp.get_tool_definitions_for_llm()
            system = SYSTEM_PROMPT + "\n\n" + tool_descriptions
        else:
            system = SYSTEM_PROMPT
        memories = db.get_memories(limit=5)
        if memories:
            memory_lines = [f"- {m['content']}" for m in memories]
            system += "\n\nWhat you know about the user:\n" + "\n".join(memory_lines)
        messages = [{"role": "system", "content": system}]
        for msg in self.conversation_history[-10:]:
            messages.append(msg)
        return messages

    def _extract_tool_call(self, text: str) -> dict | None:
        idx = text.find('"tool"')
        if idx == -1: return None
        brace_start = text.rfind('{', 0, idx)
        if brace_start == -1: return None
        depth = 0
        in_string = False
        escape = False
        for i in range(brace_start, len(text)):
            c = text[i]
            if escape: escape = False; continue
            if c == '\\' and in_string: escape = True; continue
            if c == '"' and not escape: in_string = not in_string; continue
            if in_string: continue
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[brace_start:i + 1])
                        if "tool" in obj and "arguments" in obj: return {"tool": obj["tool"], "arguments": obj["arguments"]}
                    except json.JSONDecodeError: return None
                    break
        return None

    def _is_json_complete(self, text: str) -> bool:
        depth = 0; in_string = False; escape = False
        for c in text:
            if escape: escape = False; continue
            if c == '\\' and in_string: escape = True; continue
            if c == '"' and not escape: in_string = not in_string; continue
            if in_string: continue
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0: return True
        return False

    _MEMORY_PATTERNS = [re.compile(r'\bremember\s+that\s+(.+)', re.IGNORECASE), re.compile(r'\bremember\s+(.+)', re.IGNORECASE)]
    def _is_memory_intent(self, message: str) -> str | None:
        for pattern in self._MEMORY_PATTERNS:
            m = pattern.search(message)
            if m: return m.group(1).strip().rstrip(".")
        return None

    _COMPOSE_VERBS = re.compile(r'\b(draft|compose|write|send|create)\b', re.IGNORECASE)
    _COMPOSE_NOUN = re.compile(r'\b(e[\-]?mail|mail)\b', re.IGNORECASE)
    _TO_PATTERNS = [re.compile(r'\bto\s+([a-zA-Z0-9._%+\-@ ]+)', re.IGNORECASE)]
    _SUBJECT_PATTERNS = [re.compile(r'\bsubject\s*:?\s*(.+?)(?:\s+body|\s+and\s+body|\s*$)', re.IGNORECASE)]
    _BODY_PATTERNS = [re.compile(r'\bbody\s*:?\s*(.+)', re.IGNORECASE)]
    def _is_compose_intent(self, message: str) -> dict | None:
        if not (self._COMPOSE_VERBS.search(message) and self._COMPOSE_NOUN.search(message)): return None
        slots = {}
        for pattern in self._TO_PATTERNS:
            m = pattern.search(message)
            if m: slots["to"] = m.group(1).strip().rstrip('.'); break
        return slots

    _ORGANIZE_VERBS = re.compile(r'\b(organize|clean\s*up|tidy|sort|arrange| declutter)\b', re.IGNORECASE)
    _ORGANIZE_NOUNS = re.compile(r'\b(folder|directory|downloads?|desktop|documents?|pictures?|files?)\b', re.IGNORECASE)
    def _is_organize_intent(self, message: str) -> str | None:
        if not (self._ORGANIZE_VERBS.search(message) and self._ORGANIZE_NOUNS.search(message)): return None
        return None

    async def _handle_organize_intent(self, path: str) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="thinking", content="Scanning folder...")
        try: tool_result = await self.mcp.call_tool("preview_organize", {"path": path})
        except Exception as e: yield AgentEvent(type="token", content=f"Error: {e}"); yield AgentEvent(type="done", content="", done=True); return
        yield AgentEvent(type="tool_call", content="Scanning...", tool_name="preview_organize", tool_args={"path": path})
        yield AgentEvent(type="tool_result", content=tool_result, tool_name="preview_organize", tool_result=tool_result)
        self._pending_state = {"type": "confirm", "tool_name": "execute_organize", "tool_args": {"plan_id": "1"}}
        yield AgentEvent(type="confirm", content="Organize?", tool_name="execute_organize", tool_args={"plan_id": "1"})

    def _is_prompt_echo(self, text: str) -> bool:
        return sum(1 for p in _ECHO_PATTERNS if p.search(text)) >= 3

    def _is_valid_response(self, text: str) -> bool:
        if len(text.strip()) < _MIN_RESPONSE_LENGTH or self._is_prompt_echo(text): return False
        return True

    def _is_reminder_intent(self, message: str) -> dict | None: return None
    async def _handle_reminder_intent(self, intent: dict) -> AsyncIterator[AgentEvent]: yield AgentEvent(type="done", content="", done=True)
    def _trim_history(self):
        if len(self.conversation_history) > 20: self.conversation_history = self.conversation_history[-10:]

    _TOOL_SCOPES = {"read_file": "files", "search_and_fetch": "browser", "fetch_page": "browser", "draft_email": "email"}
    def _tool_scope(self, tool_name: str) -> str: return self._TOOL_SCOPES.get(tool_name, "other")
    def new_conversation(self):
        self.conversation_history = []; self._tool_call_count = 0; self._pending_state = None; self._conversation_id = None
    def reset(self): self.new_conversation()