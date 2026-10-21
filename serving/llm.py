"""A small client for any OpenAI-compatible model server (vLLM, Ollama, LM Studio...).

Configure with environment variables:
    LLM_BASE_URL  default http://localhost:8001/v1
    LLM_MODEL     default: the first model the server lists
    LLM_API_KEY   default "local" (local servers ignore it)

Settings are read only from these LLM_* variables, never from OPENAI_*, so a
paid account set up elsewhere on the machine is never used by accident. The
key is passed straight to the client and never printed.
"""

import json
import os
import threading
from functools import lru_cache

from openai import OpenAI

TOOL_LOCK = threading.Lock()
TEMPERATURE = 0            # the same question should get the same answer
MAX_TOKENS = 8192


@lru_cache(maxsize=1)
def connect():
    """Return (client, model id). Asked once per process."""
    client = OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://localhost:8001/v1"),
                    api_key=os.environ.get("LLM_API_KEY", "local"), timeout=600)
    model = os.environ.get("LLM_MODEL") or client.models.list().data[0].id
    return client, model


def describe():
    """One line for the screen: which server and model are answering."""
    client, model = connect()
    return f"model {model} at {client.base_url}"


def chat_with_tools(system, user, tools, call_tool, max_turns=8, stats=None, finish_tool=None):
    """Let the model call tools until it writes a final message.

    call_tool(name, arguments) -> result (anything JSON can hold).
    stats: an optional dict; prompt and completion tokens and turns are added to it.
    finish_tool: an optional tool name that ends the conversation when called
        (the evaluation harness uses `submit_answer`).
    Returns (final text, [{"tool", "arguments", "result"}, ...]). The text is
    None, not "", when max_turns ran out before any final message: callers
    must say so rather than pass silence off as an answer.
    """
    client, model = connect()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    calls = []
    nudged = False
    stats = {} if stats is None else stats
    stats.setdefault("model", model)
    for _ in range(max_turns):
        response = client.chat.completions.create(
            model=model, messages=messages, tools=tools, max_tokens=MAX_TOKENS, temperature=TEMPERATURE)
        for key in ("prompt_tokens", "completion_tokens"):
            stats[key] = stats.get(key, 0) + (getattr(response.usage, key, 0) or 0)
        stats["turns"] = stats.get("turns", 0) + 1
        message = response.choices[0].message
        if not message.tool_calls:
            if finish_tool and not nudged:      # answered in prose: ask once for the form
                nudged = True
                messages += [{"role": "assistant", "content": message.content or ""},
                             {"role": "user", "content": f"Now call {finish_tool} with that answer."}]
                continue
            return (message.content or "").strip(), calls
        messages.append({"role": "assistant", "content": message.content or "",
                         "tool_calls": [call.model_dump() for call in message.tool_calls]})
        for call in message.tool_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            with TOOL_LOCK:                 # one tool at a time: mf locks the DuckDB file
                result = call_tool(call.function.name, arguments)
            calls.append({"tool": call.function.name, "arguments": arguments, "result": result})
            if call.function.name == finish_tool:
                return (message.content or "").strip(), calls
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result, default=str)})
    stats["no_answer"] = True
    return None, calls                      # out of turns: no answer, said plainly
