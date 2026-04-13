"""Shared utilities for vLLM-based backends."""
import json
import sys


def parse_options(options_list):
    """Parse Options[] list of 'key:value' strings into a dict.

    Supports type inference for common cases (bool, int, float).
    Used by LoadModel to extract backend-specific options.
    """
    opts = {}
    for opt in options_list:
        if ":" not in opt:
            continue
        key, value = opt.split(":", 1)
        key = key.strip()
        value = value.strip()
        # Try type conversion
        if value.lower() in ("true", "false"):
            opts[key] = value.lower() == "true"
        else:
            try:
                opts[key] = int(value)
            except ValueError:
                try:
                    opts[key] = float(value)
                except ValueError:
                    opts[key] = value
    return opts


def messages_to_dicts(proto_messages):
    """Convert proto Message objects to list of dicts for apply_chat_template().

    Handles: role, content, name, tool_call_id, reasoning_content, tool_calls (JSON string -> list).
    """
    result = []
    for msg in proto_messages:
        d = {"role": msg.role, "content": msg.content or ""}
        if msg.name:
            d["name"] = msg.name
        if msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id
        if msg.reasoning_content:
            d["reasoning_content"] = msg.reasoning_content
        if msg.tool_calls:
            try:
                d["tool_calls"] = json.loads(msg.tool_calls)
            except json.JSONDecodeError:
                pass
        result.append(d)
    return result


def setup_parsers(opts):
    """Return (tool_parser_cls, reasoning_parser_cls) tuple from opts dict.

    Uses vLLM's native ToolParserManager and ReasoningParserManager.
    Returns (None, None) if vLLM is not installed or parsers not available.
    """
    tool_parser_cls = None
    reasoning_parser_cls = None

    tool_parser_name = opts.get("tool_parser")
    reasoning_parser_name = opts.get("reasoning_parser")

    if tool_parser_name:
        try:
            from vllm.tool_parsers import ToolParserManager
            tool_parser_cls = ToolParserManager.get_tool_parser(tool_parser_name)
            print(f"[vllm_utils] Loaded tool_parser: {tool_parser_name}", file=sys.stderr)
        except Exception as e:
            print(f"[vllm_utils] Failed to load tool_parser {tool_parser_name}: {e}", file=sys.stderr)

    if reasoning_parser_name:
        try:
            from vllm.reasoning import ReasoningParserManager
            reasoning_parser_cls = ReasoningParserManager.get_reasoning_parser(reasoning_parser_name)
            print(f"[vllm_utils] Loaded reasoning_parser: {reasoning_parser_name}", file=sys.stderr)
        except Exception as e:
            print(f"[vllm_utils] Failed to load reasoning_parser {reasoning_parser_name}: {e}", file=sys.stderr)

    return tool_parser_cls, reasoning_parser_cls
