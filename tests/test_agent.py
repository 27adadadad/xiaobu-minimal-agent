import asyncio
import json
import pytest
from minagent.protocol import parse_message, ProtocolError
from minagent.store import Store
from minagent.tools import build_registry, ToolContext
from minagent.runtime import Runtime


def call(name, args, ident="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class ScriptedLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    async def complete(self, messages, tools):
        self.requests.append(json.loads(json.dumps(messages)))
        await asyncio.sleep(0)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.sqlite3")


@pytest.fixture
def registry(store):
    return build_registry(store)


def test_final_and_private_reasoning():
    d = parse_message({"content": "<think>private</think>你好", "reasoning_content": "secret"})
    assert d.answer == "你好" and d.reasoning_present
    assert "private" not in json.dumps(d.message)


def test_unclosed_reasoning_not_leaked():
    with pytest.raises(ProtocolError):
        parse_message({"content": "<think>secret"})


def test_native_calls_parsed():
    d = parse_message(call("calculator", {"expression": "2+3"}))
    assert d.calls[0].name == "calculator"
    assert d.calls[0].arguments == {"expression": "2+3"}


@pytest.mark.parametrize("message", [
    {"content": None},
    {"content": "", "tool_calls": [{"id": "a", "type": "function", "function": {"name": "calculator", "arguments": "bad"}}]},
    {"content": "", "tool_calls": [{"id": "a", "type": "function", "function": {"name": "calculator", "arguments": "[]"}}]},
    {"content": "", "tool_calls": [call("x", {})["tool_calls"][0]] * 2},
])
def test_protocol_rejects_malformed(message):
    with pytest.raises(ProtocolError):
        parse_message(message)


@pytest.mark.asyncio
async def test_direct_answer(store, registry):
    llm = ScriptedLLM({"content": "你好"})
    result = await Runtime(store, registry, llm).run("A", "1", "r1", "你好")
    assert result["answer"] == "你好"
    assert llm.requests[0][-1] == {"role": "user", "content": "你好"}


@pytest.mark.asyncio
async def test_tool_loop_and_idempotent_replay(store, registry):
    llm = ScriptedLLM(call("calculator", {"expression": "(2+3)*4"}), {"content": "20"})
    rt = Runtime(store, registry, llm)
    result = await rt.run("A", "1", "r1", "计算(2+3)*4")
    assert result["answer"] == "20"
    second = llm.requests[1]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1"
    assert json.loads(second[-1]["content"])["result"] == 20
    assert await rt.run("A", "1", "r1", "计算(2+3)*4") == result
    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_request_id_conflict(store, registry):
    rt = Runtime(store, registry, ScriptedLLM({"content": "ok"}))
    await rt.run("A", "1", "same", "one")
    with pytest.raises(ValueError, match="request_id"):
        await rt.run("A", "1", "same", "different")


@pytest.mark.asyncio
async def test_multi_tool_and_tool_error_recovery(store, registry):
    msg = call("calculator", {"expression": "1/0"}, "bad")
    msg["tool_calls"] += call("search", {"query": "agent"}, "good")["tool_calls"]
    llm = ScriptedLLM(msg, {"content": "计算失败，检索是模拟数据。"})
    result = await Runtime(store, registry, llm).run("A", "1", "r", "test")
    assert result["status"] == "completed"
    assert len([m for m in llm.requests[-1] if m["role"] == "tool"]) == 2
    assert not json.loads(llm.requests[-1][-2]["content"])["ok"]


@pytest.mark.asyncio
async def test_todo_user_scope_and_receipt(store, registry):
    ctx = ToolContext("A", "1", "r", "c1")
    a = await registry.execute("todo", {"action": "add", "text": "带伞"}, ctx)
    b = await registry.execute("todo", {"action": "add", "text": "带伞"}, ctx)
    assert a == b
    other_window = await registry.execute("todo", {"action": "list"}, ToolContext("A", "2", "r2", "c2"))
    other_user = await registry.execute("todo", {"action": "list"}, ToolContext("B", "1", "r3", "c3"))
    assert len(other_window["items"]) == 1 and other_user["items"] == []
    bad = await registry.execute("todo", {"action": "done", "id": a["item"]["id"]}, ToolContext("B", "1", "r4", "c4"))
    assert not bad["ok"]


@pytest.mark.asyncio
async def test_todo_done_delete(store, registry):
    ctx = lambda i: ToolContext("A", "1", "r", str(i))
    item = (await registry.execute("todo", {"action": "add", "text": "test"}, ctx(1)))["item"]
    assert (await registry.execute("todo", {"action": "done", "id": item["id"]}, ctx(2)))["item"]["done"]
    assert (await registry.execute("todo", {"action": "delete", "id": item["id"]}, ctx(3)))["ok"]
    assert (await registry.execute("todo", {"action": "list"}, ctx(4)))["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("expr", ["__import__('os').system('echo unsafe')", "2**10000", "[1]", "True+1", "9**9**9"])
async def test_calculator_rejects_unsafe(registry, expr):
    assert not (await registry.execute("calculator", {"expression": expr}, ToolContext("A", "1", "r", "c")))["ok"]


@pytest.mark.asyncio
async def test_schema_and_unknown_tool(registry):
    ctx = ToolContext("A", "1", "r", "c")
    assert not (await registry.execute("calculator", {"expression": "1", "evil": 1}, ctx))["ok"]
    assert not (await registry.execute("calculator", {"expression": 1}, ctx))["ok"]
    assert not (await registry.execute("missing", {}, ctx))["ok"]
    assert {t["function"]["name"] for t in registry.schemas()} >= {"todo", "calculator", "search", "weather", "memory"}


@pytest.mark.asyncio
async def test_history_isolated_and_persistent(tmp_path):
    path = tmp_path / "s.db"
    store = Store(path)
    llm = ScriptedLLM({"content": "first"}, {"content": "second"}, {"content": "third"})
    rt = Runtime(store, build_registry(store), llm)
    await rt.run("A", "1", "r1", "secret-A1")
    await rt.run("A", "2", "r2", "window2")
    await rt.run("B", "1", "r3", "B")
    assert "secret-A1" not in json.dumps(llm.requests[1])
    assert "secret-A1" not in json.dumps(llm.requests[2])
    assert "secret-A1" in json.dumps(Store(path).history("A", "1"))


@pytest.mark.asyncio
async def test_explicit_memory_recall_is_user_scoped(store, registry):
    await registry.execute("memory", {"action": "remember", "key": "city", "value": "上海"}, ToolContext("A", "1", "r", "c"))
    llm = ScriptedLLM({"content": "ok"}, {"content": "ok"})
    rt = Runtime(store, registry, llm)
    await rt.run("A", "2", "r1", "你好")
    await rt.run("B", "2", "r2", "你好")
    assert "上海" in json.dumps(llm.requests[0], ensure_ascii=False)
    assert "上海" not in json.dumps(llm.requests[1], ensure_ascii=False)


@pytest.mark.asyncio
async def test_loop_limit(store, registry):
    llm = ScriptedLLM(*[call("search", {"query": "x"}, str(i)) for i in range(2)])
    result = await Runtime(store, registry, llm, max_steps=2).run("A", "1", "r", "loop")
    assert result["status"] == "limited"
    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_model_error_does_not_poison_next_turn(store, registry):
    llm = ScriptedLLM(RuntimeError("hidden credential"), {"content": "recovered"})
    rt = Runtime(store, registry, llm)
    assert (await rt.run("A", "1", "r1", "first"))["status"] == "failed"
    assert (await rt.run("A", "1", "r2", "second"))["answer"] == "recovered"
    assert "hidden credential" not in json.dumps(store.history("A", "1"))


@pytest.mark.asyncio
async def test_same_session_serialized_other_sessions_parallel(store, registry):
    active = set()
    overlap = []
    class Slow:
        async def complete(self, messages, tools):
            key = messages[-1]["content"]
            active.add(key)
            overlap.append(set(active))
            await asyncio.sleep(0.03)
            active.remove(key)
            return {"content": key}
    rt = Runtime(store, registry, Slow())
    await asyncio.gather(rt.run("A", "1", "r1", "one"), rt.run("A", "1", "r2", "two"), rt.run("B", "1", "r3", "other"))
    assert not any({"one", "two"} <= s for s in overlap)
    assert any("other" in s and len(s) == 2 for s in overlap)
    assert not rt.gates


@pytest.mark.asyncio
async def test_tool_timeout(store, registry):
    async def slow(args, ctx):
        await asyncio.sleep(1)
        return {"ok": True}
    registry.register("slow", "slow fixture", {"type": "object", "properties": {}, "additionalProperties": False}, slow)
    llm = ScriptedLLM(call("slow", {}), {"content": "timed out"})
    rt = Runtime(store, registry, llm, tool_timeout=0.01)
    await rt.run("A", "1", "r", "go")
    assert json.loads(llm.requests[-1][-1]["content"])["error"]["code"] == "tool_timeout"


@pytest.mark.asyncio
async def test_cancellation_releases_session(store, registry):
    entered = asyncio.Event()
    class Slow:
        async def complete(self, messages, tools):
            entered.set()
            await asyncio.sleep(20)
    rt = Runtime(store, registry, Slow())
    task = asyncio.create_task(rt.run("A", "1", "r", "test"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not rt.gates
    assert store.turn("A", "1", "r")["status"] == "cancelled"


@pytest.mark.asyncio
async def test_weather_then_todo_dependency(store, registry):
    async def fixture_weather(args, ctx):
        return {"ok":True,"source":"OFFLINE FIXTURE","current":{"precipitation":1}}
    description,schema,_ = registry.tools["weather"]
    registry.tools["weather"] = (description,schema,fixture_weather)
    llm = ScriptedLLM(call("weather",{"city":"上海"},"w"),call("todo",{"action":"add","text":"带伞"},"t"),{"content":"已查天气并添加待办。"})
    result = await Runtime(store,registry,llm).run("A","1","r","查上海天气并记待办")
    assert result["status"] == "completed"
    assert json.loads(llm.requests[1][-1]["content"])["current"]["precipitation"] == 1
    assert store.todos("A")[0]["text"] == "带伞"


@pytest.mark.asyncio
async def test_memory_refreshes_between_model_steps(store, registry):
    llm = ScriptedLLM(call("memory",{"action":"remember","key":"city","value":"上海"}),{"content":"记住了"})
    await Runtime(store,registry,llm).run("A","1","r","记住我的城市是上海")
    assert any((m.get("content") or "").startswith("[USER_MEMORY") for m in llm.requests[1])


def test_session_title_is_first_message(store):
    for request,text in [("r1","z-first"),("r2","a-second")]:
        store.start("A","1",request,text)
        store.finish("A","1",request,{"status":"completed","answer":"ok"},[{"role":"user","content":text},{"role":"assistant","content":"ok"}])
    assert store.sessions("A")[0]["title"] == "z-first"
