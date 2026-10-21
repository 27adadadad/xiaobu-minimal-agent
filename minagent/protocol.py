"""Normalize native function calling; private reasoning is never exposed or stored."""
import json
import re
from dataclasses import dataclass


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class Decision:
    calls: list[ToolCall]
    answer: str
    reasoning_present: bool
    message: dict


def parse_message(raw: dict) -> Decision:
    if not isinstance(raw, dict):
        raise ProtocolError("assistant message must be an object")
    text = raw.get("content") or ""
    if not isinstance(text, str):
        raise ProtocolError("content must be text")
    reasoning = bool(raw.get("reasoning_content") or raw.get("reasoning") or "<think>" in text)
    # Only compatibility parsing, not an attempt to request/reconstruct chain of thought.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    text = re.sub(r"<think>.*", "", text, flags=re.S | re.I).strip()
    native = raw.get("tool_calls") or []
    if not isinstance(native, list) or len(native) > 8:
        raise ProtocolError("invalid or excessive tool calls")
    calls, normalized, ids = [], [], set()
    for item in native:
        try:
            ident, fn = item["id"], item["function"]
            name, encoded = fn["name"], fn["arguments"]
            if item.get("type") != "function" or not isinstance(ident, str) or not ident or ident in ids or len(ident) > 128:
                raise ValueError("invalid call id")
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", name):
                raise ValueError("invalid function name")
            if not isinstance(encoded, str) or len(encoded) > 16000:
                raise ValueError("invalid arguments")
            args = json.loads(encoded)
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except (KeyError, TypeError, ValueError, RecursionError) as exc:
            raise ProtocolError("malformed tool call") from exc
        ids.add(ident)
        calls.append(ToolCall(ident, name, args))
        normalized.append({"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}})
    if not calls and not text:
        raise ProtocolError("empty assistant response")
    message = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = normalized
    return Decision(calls, text, reasoning, message)
