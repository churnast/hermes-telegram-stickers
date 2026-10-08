"""CI: the sticker tools still get no tool progress line on this Hermes, and Hermes' own pre-call steps still run.

Run from the plugin checkout with the Python of a Hermes install and an empty HERMES_HOME. It checks two things:

1. Hermes' own plugin loader registers the plugin's tool_execution middleware. Then Hermes' own tool loop step
   (agent/tool_executor.py, with a stand-in agent object that records the progress events) runs each call, in a
   Telegram turn unless stated otherwise:
   - every sticker tool runs without Hermes' own step after the chain, so no "tool.started" event (the progress
     line); Hermes' pre_tool_call hooks still run for it (a probe plugin records them), a "block" from a hook
     stops it, a "modify" reaches it, and Hermes' coercion turns "false" and "5" into false and 5;
   - another tool (web_search) and a sticker tool in a non-Telegram turn go through Hermes' own step, with the
     event; model_tools.handle_function_call runs pre_tool_call once for a sticker tool, not twice.
2. In agent/tool_executor.py, the one emitter of "tool.started" is reached only from the step at the end of the
   tool_execution chain. If Hermes moves it before the chain, the line comes back for the sticker tools, and this
   fails.
"""

from __future__ import annotations

import ast
import builtins
import json
import os
import shutil
import sys
from pathlib import Path
from unittest import mock

PLUGIN = Path.cwd()
PROGRESS_EVENT = "tool.started"
EMITTER = "_begin_tool_execution"
CHAIN = "run_tool_execution_middleware"
PROBE = "ci-hook-probe"
BLOCK_QUERY, MODIFY_QUERY = "blocked-by-probe", "modify-me"


def install_plugins(home: Path) -> None:
    """The plugin as a user has it: in HERMES_HOME/plugins, enabled, with settings that never call Telegram; and a
    probe plugin whose pre_tool_call hook records every call, blocks one query and rewrites another."""
    dest = home / "plugins" / "telegram-stickers"
    shutil.copytree(PLUGIN, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", "docs"))
    probe = home / "plugins" / PROBE
    probe.mkdir(parents=True)
    (probe / "plugin.yaml").write_text(
        f"name: {PROBE}\nversion: 0.0.1\nmanifest_version: 1\ndescription: records pre_tool_call\n"
        "provides_hooks:\n  - pre_tool_call\n", encoding="utf-8")
    (probe / "__init__.py").write_text(f"""
import json

LOG = {str(home / "pre_tool_call.jsonl")!r}


def pre_tool_call(tool_name="", args=None, **kwargs):
    args = args if isinstance(args, dict) else {{}}
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({{"tool": tool_name, "args": args}}) + "\\n")
    if args.get("query") == {BLOCK_QUERY!r}:
        return {{"action": "block", "message": "blocked by the CI probe"}}
    if args.get("query") == {MODIFY_QUERY!r}:
        return {{"action": "modify", "args": {{"query": "modified by the CI probe"}}}}
    return None


def register(ctx):
    ctx.register_hook("pre_tool_call", pre_tool_call)
""", encoding="utf-8")
    (home / "config.yaml").write_text(
        f"plugins:\n  enabled:\n    - telegram-stickers\n    - {PROBE}\n  entries:\n    telegram-stickers:\n"
        "      settings:\n        default_packs: false\n        learn_packs: false\n", encoding="utf-8")


def sticker_tool_names() -> list[str]:
    sys.path.insert(0, str(PLUGIN))
    import schemas

    return sorted(value["name"] for value in vars(schemas).values()
                  if isinstance(value, dict) and str(value.get("name", "")).startswith("telegram_sticker"))


def pre_tool_call_log(home: Path) -> list[dict]:
    path = home / "pre_tool_call.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def fake_agent(events: list) -> mock.MagicMock:
    """What Hermes' tool loop step reads from its agent; tool_progress_callback records the progress events."""
    agent = mock.MagicMock()
    agent._interrupt_requested = False
    agent.verbose_logging = False
    agent.quiet_mode = True
    agent.session_id = "ci-session"
    agent._current_turn_id = "ci-turn"
    agent._current_api_request_id = "ci-request"
    agent._checkpoint_mgr.enabled = False
    agent._tool_guardrails.before_call.return_value = mock.Mock(allows_execution=True)
    agent.tool_progress_callback = lambda event, *args, **kwargs: events.append(event)
    agent.tool_start_callback = None
    return agent


def check_chain(home: Path) -> None:
    from agent import tool_executor
    from hermes_cli import plugins

    plugins.discover_plugins(force=True)
    callbacks = plugins.get_plugin_manager()._middleware.get("tool_execution", [])
    ours = [cb for cb in callbacks if getattr(cb, "__name__", "") == "_tool_execution_middleware"]
    assert ours, f"middleware not registered by Hermes' loader: {[getattr(cb, '__name__', '') for cb in callbacks]}"
    plugin_module = sys.modules[ours[0].__module__]
    seen = []
    # Every tool handler that takes the call's arguments (the settings tool too, where the plugin has it).
    for method in [m for m in ("find", "send", "settings") if hasattr(plugin_module._service, m)]:
        setattr(plugin_module._service, method,
                lambda args, method=method: seen.append((method, dict(args))) or {"success": True, "by": method})
    plugin_module._service.mute_current_chat = lambda: seen.append(("mute", {})) or {"success": True, "by": "mute"}

    def run(name: str, args: dict, platform: str = "telegram"):
        os.environ["HERMES_SESSION_PLATFORM"] = platform
        events, reached = [], []
        managed = tool_executor._run_agent_tool_execution_middleware(
            fake_agent(events), function_name=name, function_args=dict(args), effective_task_id="ci-task",
            tool_call_id=f"ci-{name}", execute=lambda final: reached.append(final) or '{"hermes": "ran it"}',
            display_index=1)
        return managed.result, events, reached

    tools = sticker_tool_names()
    assert len(tools) >= 3, tools
    for name in tools:
        before = len(pre_tool_call_log(home))
        result, events, reached = run(name, {"query": "cat"})
        assert PROGRESS_EVENT not in events and not reached, f"{name} reached Hermes' own step: {events}"
        assert json.loads(result).get("success") is True, result
        hooks = [e["tool"] for e in pre_tool_call_log(home)[before:]]
        assert hooks == [name], f"pre_tool_call did not run once for {name}: {hooks}"

    seen.clear()
    result, events, reached = run("telegram_sticker_find", {"query": BLOCK_QUERY})
    assert json.loads(result) == {"error": "blocked by the CI probe"} and not seen and not events, result
    run("telegram_sticker_find", {"query": MODIFY_QUERY, "refresh": "false", "limit": "5"})
    assert seen == [("find", {"query": "modified by the CI probe", "refresh": False, "limit": 5})], seen

    result, events, reached = run("web_search", {"query": "x"})
    assert PROGRESS_EVENT in events and reached == [{"query": "x"}], (events, reached)
    result, events, reached = run("telegram_sticker_send", {"sticker": "x"}, platform="cli")
    assert PROGRESS_EVENT in events and reached == [{"sticker": "x"}], (events, reached)

    import model_tools

    os.environ["HERMES_SESSION_PLATFORM"] = "telegram"
    seen.clear()
    before = len(pre_tool_call_log(home))
    model_tools.handle_function_call("telegram_sticker_find", {"query": "cat"}, "ci-task", tool_call_id="ci-hfc")
    assert len(pre_tool_call_log(home)) - before == 1 and seen == [("find", {"query": "cat"})], seen
    print(f"chain OK: {', '.join(tools)} run by the plugin after Hermes' pre_tool_call and coercion, with no "
          f"{PROGRESS_EVENT!r}; web_search, a CLI turn and handle_function_call go through Hermes")


class Functions(ast.NodeVisitor):
    """For each call of interest, the names of the functions (and lambdas) it sits in, outermost first."""

    def __init__(self) -> None:
        self.stack: list[str] = []
        self.emitters: set[str] = set()
        self.emitter_calls: list[list[str]] = []
        self.calls_by_function: dict[str, list[list[str]]] = {}
        self.chain_steps: set[str] = set()
        self.emitter_names = 0

    def _scope(self, node, name: str) -> None:
        self.stack.append(name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._scope(node, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self._scope(node, "<lambda>")

    def visit_Name(self, node):
        self.emitter_names += node.id == EMITTER

    def visit_Call(self, node):
        callee = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if any(isinstance(arg, ast.Constant) and arg.value == PROGRESS_EVENT for arg in node.args):
            self.emitters.add(self.stack[-1] if self.stack else "<module>")
        if callee == EMITTER:
            self.emitter_calls.append(list(self.stack))
        if callee:
            self.calls_by_function.setdefault(callee, []).append(list(self.stack))
        if callee == CHAIN and len(node.args) >= 3:
            # The chain's last step: the functions the third argument (the next_call target) calls.
            self.chain_steps |= {n.func.id for n in ast.walk(node.args[2]) if isinstance(n, ast.Call)
                                 and isinstance(n.func, ast.Name) and not hasattr(builtins, n.func.id)}
        self.generic_visit(node)


def check_executor() -> None:
    import agent.tool_executor as executor

    tree = ast.parse(Path(executor.__file__).read_text(encoding="utf-8"))
    found = Functions()
    found.visit(tree)
    assert found.emitters == {EMITTER}, f"{PROGRESS_EVENT!r} is emitted from {sorted(found.emitters)}"
    assert found.chain_steps, f"no {CHAIN}(name, args, next_call) call found"
    assert found.emitter_calls, f"{EMITTER} is never called"
    assert found.emitter_names == len(found.emitter_calls), f"{EMITTER} is passed around, not only called"

    def inside_chain_step(stack: list[str], depth: int = 0) -> bool:
        if any(name in found.chain_steps for name in stack):
            return True
        outer = stack[0] if stack else ""
        callers = found.calls_by_function.get(outer, [])
        return depth < 3 and bool(callers) and all(inside_chain_step(s, depth + 1) for s in callers)

    for stack in found.emitter_calls:
        assert inside_chain_step(stack), f"{EMITTER} is called outside the tool_execution chain: {stack}"
    print(f"executor OK: {PROGRESS_EVENT!r} only from {EMITTER}, reached only through "
          f"{', '.join(sorted(found.chain_steps))}")


if __name__ == "__main__":
    home = Path(os.environ["HERMES_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    assert not any(home.iterdir()), f"HERMES_HOME {home} must be empty"
    os.environ["TELEGRAM_BOT_TOKEN"] = "123456:CI-SYNTHETIC-TOKEN"
    install_plugins(home)
    check_chain(home)
    check_executor()
