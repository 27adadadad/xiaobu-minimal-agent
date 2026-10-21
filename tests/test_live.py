"""Opt-in acceptance using an actual paid model. Never uses the scripted test LLM."""
import os
import pytest
from minagent.config import Settings
from minagent.llm import OpenAICompatibleLLM
from minagent.runtime import Runtime
from minagent.store import Store
from minagent.tools import build_registry


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1",reason="set RUN_LIVE=1 and configure project .env to call a real LLM")
async def test_real_llm_calculator_todo_memory_and_session(tmp_path):
    settings = Settings.from_env()
    assert settings.configured, "Configure LLM_API_KEY and LLM_MODEL locally; do not paste credentials into chat."
    store = Store(tmp_path/"live.db")
    runtime = Runtime(store,build_registry(store),OpenAICompatibleLLM(settings))
    events = []
    async def emit(event):
        events.append(event)
    result = await runtime.run("live-A","window1","calc","必须用 calculator 计算 (128+256)*3/8。",emit)
    assert result["status"] == "completed"
    assert any(e.get("name")=="calculator" and e.get("result",{}).get("result")==144 for e in events)
    await runtime.run("live-A","window1","todo","请添加待办：面试前检查 Agent 测试。",emit)
    assert any("检查 Agent 测试" in t["text"] for t in store.todos("live-A"))
    assert not store.todos("live-B")
    await runtime.run("live-A","window1","memory","请长期记住：我的常住城市是上海。",emit)
    assert "上海" in str(store.recall("live-A"))
    assert not store.recall("live-B")
    result = await runtime.run("live-A","window2","recall","我常住哪个城市？")
    assert result["status"] == "completed" and "上海" in result["answer"]
    assert "检查 Agent 测试" not in str(store.history("live-A","window2"))
