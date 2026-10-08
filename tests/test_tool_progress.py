"""The tool_execution middleware that keeps the sticker tools out of Hermes' tool progress lines.

Synthetic stand-ins only: a recording ctx instead of Hermes' PluginContext, and a few small fake Hermes modules
(written to a temporary folder and loaded under Hermes' module names, since the middleware reads who started the
chain from the call stack) instead of Hermes itself. CI's Hermes job runs the same middleware through Hermes' own
loader, chain, tool loop step, pre_tool_call hooks and argument coercion.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456:TEST-TOKEN"
# Every keyword Hermes passes a tool_execution middleware (hermes_cli/middleware.py, agent/tool_executor.py).
HERMES_CONTEXT = {"original_args": {}, "telemetry_schema_version": "hermes.observer.v1",
                  "middleware_schema_version": "hermes.middleware.v1", "task_id": "t1", "session_id": "s1",
                  "tool_call_id": "call_1", "turn_id": "turn_1", "api_request_id": "req_1"}
HOOK_IDS = {key: HERMES_CONTEXT[key] for key in ("task_id", "session_id", "tool_call_id", "turn_id",
                                                  "api_request_id")}
TELEGRAM = {"platform": "telegram", "chat_id": "111", "thread_id": "", "message_id": "5", "chat_type": "dm"}

# Small fake Hermes modules. Each is loaded under the module name in the key, as Hermes' own module would be.
FAKE_HERMES = {
    "hermes_cli.middleware": '''
"""Hermes' tool_execution chain, cut down: the registered callbacks in order, each reaching the next through
next_call, and Hermes' own step after the last one."""
CALLBACKS = []


def run_tool_execution_middleware(tool_name, args, next_call, **context):
    def call_at(index, payload):
        if index >= len(CALLBACKS):
            return next_call(payload)

        def nxt(new_payload=None):
            return call_at(index + 1, payload if new_payload is None else new_payload)

        return CALLBACKS[index](tool_name=tool_name, args=payload, next_call=nxt, **context)

    return call_at(0, args)
''',
    "agent.tool_executor": '''
"""The agent's tool loop: it starts the chain with Hermes' own step (pre-call checks, then the progress line)."""


def dispatch(chain, tool_name, args, hermes_step, **context):
    return chain(tool_name, args, hermes_step, **context)
''',
    "model_tools": '''
"""handle_function_call (execute_code, a plugin's dispatch_tool): pre_tool_call has run before the chain, and
Hermes posts no progress line after it. coerce_tool_args turns "true"/"false" and digit strings into the types the
schema asks for, like Hermes' coercion."""
SCHEMAS = {}


def handle_function_call(chain, tool_name, args, hermes_step, **context):
    return chain(tool_name, args, hermes_step, **context)


def coerce_tool_args(tool_name, args):
    props = (SCHEMAS.get(tool_name) or {}).get("parameters", {}).get("properties", {})
    for key, value in list(args.items()):
        kind = (props.get(key) or {}).get("type")
        if isinstance(value, str) and kind == "boolean" and value.lower() in ("true", "false"):
            args[key] = value.lower() == "true"
        elif isinstance(value, str) and kind == "integer" and value.lstrip("-").isdigit():
            args[key] = int(value)
    return args
''',
    "hermes_cli.plugins": '''
"""Hermes' pre_tool_call dispatch: records each call and answers (block message, modified args)."""
CALLS = []
ANSWER = {"block": None, "modify": None, "raise": None}


def _dispatch_pre_tool_call_hooks(tool_name, args, task_id="", session_id="", tool_call_id="", turn_id="",
                                  api_request_id="", middleware_trace=None):
    CALLS.append({"tool": tool_name, "args": dict(args), "ids": [task_id, session_id, tool_call_id, turn_id,
                                                                 api_request_id], "trace": middleware_trace})
    if ANSWER["raise"] is not None:
        raise ANSWER["raise"]
    modified = None if ANSWER["modify"] is None else {**args, **ANSWER["modify"]}
    return ANSWER["block"], modified
''',
}


class Ctx:
    """Records what register() registers, like Hermes' PluginContext."""

    def __init__(self, settings, refuse_middleware=False):
        self.settings = settings
        self.refuse_middleware = refuse_middleware
        self.tools, self.schemas, self.middleware, self.hooks = {}, {}, [], {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = handler
        self.schemas[name] = schema

    def register_command(self, name, handler, **kw):
        pass

    def register_skill(self, name, path, **kw):
        pass

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_telegram_handler(self, factory):
        pass

    def register_middleware(self, kind, callback):
        if self.refuse_middleware:
            raise RuntimeError("middleware refused")
        self.middleware.append((kind, callback))


class Hermes:
    """The fake Hermes modules, loaded for one test; runs a call the way the agent's tool loop or
    handle_function_call would."""

    def __init__(self, tmp_path, monkeypatch, ctx=None, present=tuple(FAKE_HERMES)):
        self.modules = {}
        folder = tmp_path / "fake_hermes"
        folder.mkdir(exist_ok=True)
        for package in ("hermes_cli", "agent"):
            monkeypatch.setitem(sys.modules, package, types.ModuleType(package))
        for name, source in FAKE_HERMES.items():
            if name not in present:
                monkeypatch.setitem(sys.modules, name, None)  # import fails, as on a Hermes without it
                continue
            path = folder / f"{name}.py"
            path.write_text(source, encoding="utf-8")
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            monkeypatch.setitem(sys.modules, name, module)
            spec.loader.exec_module(module)
            self.modules[name] = module
        self.chain = self.modules["hermes_cli.middleware"]
        self.hooks = self.modules.get("hermes_cli.plugins")
        if ctx is not None:
            self.use(ctx)

    def use(self, ctx):
        self.chain.CALLBACKS[:] = [callback for _, callback in ctx.middleware]
        if "model_tools" in self.modules:
            self.modules["model_tools"].SCHEMAS.update(ctx.schemas)
        return self

    def run(self, tool_name, args, step=None, via="agent.tool_executor", **context):
        """One call; returns (result, what Hermes' own step received, or None when it was not reached)."""
        reached = []

        def hermes_step(payload):
            reached.append(payload)
            return "ran through Hermes"

        caller = self.modules[via]
        start = caller.dispatch if via == "agent.tool_executor" else caller.handle_function_call
        result = start(self.chain.run_tool_execution_middleware, tool_name, args, step or hermes_step,
                       **{**HERMES_CONTEXT, **context})
        return result, (reached[0] if reached else None)


def load(tmp_path, monkeypatch, settings=None, before_register=None, session=TELEGRAM, **ctx_kwargs):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    spec = importlib.util.spec_from_file_location(
        "telegram_stickers_mw_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "telegram_stickers_mw_test", module)
    spec.loader.exec_module(module)
    if before_register is not None:
        before_register(module)
    ctx = Ctx({"packs": ["cats"], **(settings or {})}, **ctx_kwargs)
    module.register(ctx)
    monkeypatch.setattr(module._service, "_session", lambda: dict(session))
    return module, ctx


def middleware_of(ctx):
    (kind, callback), = ctx.middleware
    assert kind == "tool_execution"
    return callback


def fake_service(module, monkeypatch):
    """The sticker service answers without the network; the real tool handlers still run."""
    seen = []
    for method in ("find", "send"):
        monkeypatch.setattr(module._service, method,
                            lambda args, method=method: seen.append((method, args)) or {"success": True, "by": method})
    monkeypatch.setattr(module._service, "mute_current_chat",
                        lambda: seen.append(("mute", None)) or {"success": True, "by": "mute"})
    return seen


def setup(tmp_path, monkeypatch, settings=None, session=TELEGRAM, **kwargs):
    module, ctx = load(tmp_path, monkeypatch, settings, session=session, **kwargs)
    seen = fake_service(module, monkeypatch)
    return module, ctx, seen, Hermes(tmp_path, monkeypatch, ctx)


def args_for(name):
    return {"sticker": "😂"} if name.endswith("send") else {}


def test_register_declares_and_registers_the_middleware(tmp_path, monkeypatch):
    module, ctx = load(tmp_path, monkeypatch)
    assert middleware_of(ctx) is module._tool_execution_middleware
    assert set(module._tool_handlers) == set(ctx.tools)
    manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert "provides_middleware:\n  - tool_execution\n" in manifest
    assert "  hide_tool_progress:\n    type: bool\n    default: true\n" in manifest
    assert "hide_tool_progress: true" in (ROOT / "config.example.yaml").read_text(encoding="utf-8")


def test_in_the_tool_loop_of_a_telegram_turn_every_tool_of_the_plugin_skips_hermes_step(tmp_path, monkeypatch):
    module, ctx, seen, hermes = setup(tmp_path, monkeypatch)
    assert len(ctx.tools) >= 3
    for name, handler in ctx.tools.items():
        result, reached = hermes.run(name, args_for(name))
        assert reached is None, name  # Hermes' own step never runs, so no "tool.started" and no line
        assert json.loads(result)["success"] is True and result == handler(args_for(name)), name
    assert ("send", {"sticker": "😂"}) in seen


def test_hermes_pre_tool_call_step_runs_once_for_each_sticker_call_with_the_ids(tmp_path, monkeypatch):
    _, ctx, _, hermes = setup(tmp_path, monkeypatch)
    for name in ctx.tools:
        hermes.run(name, args_for(name))
    assert [call["tool"] for call in hermes.hooks.CALLS] == list(ctx.tools)
    assert hermes.hooks.CALLS[0]["ids"] == list(HOOK_IDS.values())
    assert all(call["trace"] == [] for call in hermes.hooks.CALLS)


def test_a_pre_tool_call_block_or_denied_approval_stops_the_sticker(tmp_path, monkeypatch):
    # Hermes' dispatch turns a plugin "block", a shell hook's exit code 2 and a denied or failed "approve" into a
    # block message; Hermes' tool loop then returns {"error": message} instead of running the tool.
    _, _, seen, hermes = setup(tmp_path, monkeypatch)
    hermes.hooks.ANSWER["block"] = f"BLOCKED: plugin approval required for telegram_sticker_send ({TOKEN})"
    result, reached = hermes.run("telegram_sticker_send", {"sticker": "😂"})
    assert reached is None and seen == []
    assert json.loads(result) == {"error": "BLOCKED: plugin approval required for telegram_sticker_send (<token>)"}


def test_pre_tool_call_modify_reaches_the_handler(tmp_path, monkeypatch):
    _, _, seen, hermes = setup(tmp_path, monkeypatch)
    hermes.hooks.ANSWER["modify"] = {"sticker": "🙂"}
    hermes.run("telegram_sticker_send", {"sticker": "😂", "reply": False})
    assert seen == [("send", {"sticker": "🙂", "reply": False})]


def test_a_failing_pre_tool_call_dispatch_hands_the_call_to_hermes(tmp_path, monkeypatch):
    # Never run a sticker tool without Hermes' pre-call checks: if the dispatch itself fails, Hermes' own step
    # runs the call (and its checks), at the cost of the progress line for that one call.
    _, _, seen, hermes = setup(tmp_path, monkeypatch)
    hermes.hooks.ANSWER["raise"] = RuntimeError("hook dispatch broke")
    _, reached = hermes.run("telegram_sticker_find", {"query": "cat"})
    assert reached == {"query": "cat"} and seen == []


def test_string_booleans_and_numbers_are_coerced_like_hermes_does(tmp_path, monkeypatch):
    # Models often send "false" or "5"; Hermes coerces them by the schema after the chain, so the middleware does
    # it too: refresh "false" must not reload every pack, reply "false" must not send a reply.
    _, _, seen, hermes = setup(tmp_path, monkeypatch)
    hermes.run("telegram_sticker_find", {"query": "cat", "refresh": "false", "limit": "5"})
    hermes.run("telegram_sticker_send", {"sticker": "cat", "reply": "false", "thread_id": "7"})
    assert seen == [("find", {"query": "cat", "refresh": False, "limit": 5}),
                    ("send", {"sticker": "cat", "reply": False, "thread_id": 7})]
    assert hermes.hooks.CALLS[0]["args"]["refresh"] == "false"  # pre_tool_call sees the model's own arguments


def test_a_failing_coercion_keeps_the_arguments(tmp_path, monkeypatch):
    _, _, seen, hermes = setup(tmp_path, monkeypatch)

    def broken(tool_name, args):
        raise ValueError("schema unreadable")

    monkeypatch.setattr(hermes.modules["model_tools"], "coerce_tool_args", broken)
    hermes.run("telegram_sticker_find", {"query": "cat", "refresh": True})
    assert seen == [("find", {"query": "cat", "refresh": True})]


@pytest.mark.parametrize("missing", ["hermes_cli.plugins", "model_tools"])
def test_without_hermes_pre_call_steps_the_sticker_tools_go_through_hermes(tmp_path, monkeypatch, missing):
    module, ctx = load(tmp_path, monkeypatch)
    seen = fake_service(module, monkeypatch)
    present = tuple(name for name in FAKE_HERMES if name != missing)
    hermes = Hermes(tmp_path, monkeypatch, ctx, present=present)
    result, reached = hermes.run("telegram_sticker_send", {"sticker": "x"})
    assert result == "ran through Hermes" and reached == {"sticker": "x"} and seen == []


def test_other_tools_pass_through_once_untouched(tmp_path, monkeypatch):
    _, _, seen, hermes = setup(tmp_path, monkeypatch)
    args = {"query": "x", "refresh": "false"}
    result, reached = hermes.run("web_search", args)
    assert result == "ran through Hermes" and reached is args and seen == [] and hermes.hooks.CALLS == []


@pytest.mark.parametrize("name", ["tool_call", "tool_describe", "tool_search", "", None, "TELEGRAM_STICKER_SEND"])
def test_bridge_calls_hermes_did_not_unwrap_pass_through(tmp_path, monkeypatch, name):
    # Hermes unwraps tool_call before the chain; a bridge name that still reaches it is Hermes' own business (a
    # refused call, or a batch on Hermes 0.21.5 and older), so the plugin leaves it to Hermes.
    _, _, seen, hermes = setup(tmp_path, monkeypatch)
    args = {"calls": [{"name": "telegram_sticker_send", "arguments": {"sticker": "😂"}}]}
    result, reached = hermes.run(name, args)
    assert result == "ran through Hermes" and reached == args and seen == []


@pytest.mark.parametrize("platform", ["", "cli", "api_server", "discord", "cron"])
def test_outside_a_telegram_turn_the_sticker_tools_go_through_hermes(tmp_path, monkeypatch, platform):
    # No progress line to hide there (the CLI, the API server, other platforms), so Hermes runs every step.
    _, ctx, seen, hermes = setup(tmp_path, monkeypatch, session={**TELEGRAM, "platform": platform})
    for name in ctx.tools:
        result, reached = hermes.run(name, args_for(name))
        assert result == "ran through Hermes" and reached == args_for(name), name
    assert seen == [] and hermes.hooks.CALLS == []


def test_handle_function_call_chains_pass_through(tmp_path, monkeypatch):
    # model_tools.handle_function_call (execute_code, a plugin's dispatch_tool) has run pre_tool_call already and
    # posts no line: running it again there would ask for an approval twice.
    _, ctx, seen, hermes = setup(tmp_path, monkeypatch)
    for name in ctx.tools:
        result, reached = hermes.run(name, args_for(name), via="model_tools")
        assert result == "ran through Hermes" and reached == args_for(name), name
    assert seen == [] and hermes.hooks.CALLS == []


def test_a_direct_call_outside_any_hermes_chain_passes_through(tmp_path, monkeypatch):
    _, ctx, seen, _ = setup(tmp_path, monkeypatch)
    calls = []
    result = middleware_of(ctx)(tool_name="telegram_sticker_send", args={"sticker": "x"},
                                next_call=lambda payload: calls.append(payload) or "next", **HERMES_CONTEXT)
    assert result == "next" and calls == [{"sticker": "x"}] and seen == []


def test_middleware_after_this_one_does_not_see_the_sticker_calls(tmp_path, monkeypatch):
    _, ctx, _, hermes = setup(tmp_path, monkeypatch)
    later = []

    def other_plugin(*, tool_name, args, next_call, **context):
        later.append(tool_name)
        return next_call(args)

    hermes.chain.CALLBACKS.append(other_plugin)
    hermes.run("telegram_sticker_send", {"sticker": "x"})
    hermes.run("web_search", {"query": "x"})
    assert later == ["web_search"]


def test_the_tools_come_from_what_register_registers_not_from_names(tmp_path, monkeypatch):
    def rename_mute(module):
        monkeypatch.setattr(module.schemas, "MUTE", {**module.schemas.MUTE, "name": "telegram_sticker_extra"})

    module, ctx = load(tmp_path, monkeypatch, before_register=rename_mute)
    fake_service(module, monkeypatch)
    hermes = Hermes(tmp_path, monkeypatch, ctx)
    result, reached = hermes.run("telegram_sticker_extra", {})
    assert json.loads(result)["by"] == "mute" and reached is None
    assert hermes.run("telegram_sticker_mute", {}) == ("ran through Hermes", {})


@pytest.mark.parametrize("value", [False, "off", "false", 0])
def test_hide_tool_progress_off_passes_the_sticker_tools_through(tmp_path, monkeypatch, value):
    _, ctx, seen, hermes = setup(tmp_path, monkeypatch, {"hide_tool_progress": value})
    for name in ctx.tools:
        assert hermes.run(name, {"sticker": "x"}) == ("ran through Hermes", {"sticker": "x"})
    assert seen == [] and hermes.hooks.CALLS == []


def test_the_setting_is_read_at_each_call(tmp_path, monkeypatch):
    _, ctx, _, hermes = setup(tmp_path, monkeypatch)
    assert json.loads(hermes.run("telegram_sticker_find", {})[0])["by"] == "find"
    ctx.settings["hide_tool_progress"] = False
    assert hermes.run("telegram_sticker_find", {})[0] == "ran through Hermes"


def test_a_failure_while_deciding_falls_back_to_next_call(tmp_path, monkeypatch):
    module, _, seen, hermes = setup(tmp_path, monkeypatch)

    def broken():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(module._service, "hide_tool_progress_enabled", broken)
    assert hermes.run("telegram_sticker_send", {"sticker": "x"}) == ("ran through Hermes", {"sticker": "x"})
    monkeypatch.setattr(module._service, "in_telegram_turn", broken)
    monkeypatch.setattr(module._service, "hide_tool_progress_enabled", lambda: True)
    assert hermes.run("telegram_sticker_send", {"sticker": "x"}) == ("ran through Hermes", {"sticker": "x"})
    monkeypatch.setattr(module, "_service", None)  # not registered yet, or torn down
    assert hermes.run("telegram_sticker_send", {}) == ("ran through Hermes", {})
    assert seen == [] and hermes.hooks.CALLS == []


def test_a_failing_handler_returns_the_tools_error_shape_without_the_token(tmp_path, monkeypatch):
    module, _, _, hermes = setup(tmp_path, monkeypatch)

    def boom(args):
        raise ValueError(f"bad url https://api.telegram.org/bot{TOKEN}/sendSticker")

    monkeypatch.setitem(module._tool_handlers, "telegram_sticker_send", boom)
    result, reached = hermes.run("telegram_sticker_send", {})
    result = json.loads(result)
    assert reached is None
    assert set(result) == {"error"} and result["error"].startswith("telegram_sticker_send failed: ValueError")
    assert "TEST-TOKEN" not in result["error"] and "bot<token>/sendSticker" in result["error"]


def test_the_real_handlers_keep_their_own_errors(tmp_path, monkeypatch):
    module, ctx = load(tmp_path, monkeypatch, session={**TELEGRAM, "chat_id": ""})
    hermes = Hermes(tmp_path, monkeypatch, ctx)
    result, reached = hermes.run("telegram_sticker_mute", {})
    assert reached is None and json.loads(result) == {"error": "This turn is not in a Telegram chat."}


def test_without_a_token_the_sticker_tools_refuse_like_check_fn(tmp_path, monkeypatch):
    module, ctx, seen, hermes = setup(tmp_path, monkeypatch)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    for name in ctx.tools:
        result, reached = hermes.run(name, {"sticker": "x"})
        assert reached is None and json.loads(result) == {"error": module.NO_TOKEN}
    assert seen == [] and hermes.hooks.CALLS == []


def test_a_downstream_failure_of_another_tool_is_not_swallowed_or_retried(tmp_path, monkeypatch):
    _, _, _, hermes = setup(tmp_path, monkeypatch)
    calls = []

    def timing_out(payload):
        calls.append(payload)
        raise TimeoutError("tool timed out")

    with pytest.raises(TimeoutError, match="tool timed out"):
        hermes.run("web_search", {"q": "x"}, step=timing_out)
    assert len(calls) == 1


def test_args_that_are_not_an_object_reach_the_handler_as_an_empty_one(tmp_path, monkeypatch):
    _, _, seen, hermes = setup(tmp_path, monkeypatch)
    hermes.run("telegram_sticker_find", None)
    assert seen == [("find", {})]


def test_no_middleware_in_a_plugin_host(tmp_path, monkeypatch):
    # plugins.isolation: host hands the middleware next_call as a placeholder it cannot call (checked against
    # Hermes main), so other tools could not pass through: the plugin does not register it there.
    monkeypatch.setenv("HERMES_PLUGIN_HOST_PROCESS", "1")
    _, ctx = load(tmp_path, monkeypatch)
    assert ctx.middleware == [] and len(ctx.tools) >= 3


def test_the_plugin_still_loads_when_middleware_is_refused(tmp_path, monkeypatch):
    module, ctx = load(tmp_path, monkeypatch, refuse_middleware=True)
    assert ctx.middleware == [] and set(module._tool_handlers) == set(ctx.tools)
    assert "pre_llm_call" in ctx.hooks


def test_in_telegram_turn_reads_the_turn_platform(tmp_path, monkeypatch):
    module, _ = load(tmp_path, monkeypatch)
    assert module._service.in_telegram_turn() is True
    monkeypatch.setattr(module._service, "_session", lambda: {"platform": "cli"})
    assert module._service.in_telegram_turn() is False
