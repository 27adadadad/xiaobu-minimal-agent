import asyncio
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from minagent.config import Settings
from minagent.llm import OpenAICompatibleLLM, LLMError
from minagent.store import Store
from minagent.tools import build_registry
from minagent.runtime import Runtime
from minagent.web import create_app
from tests.test_agent import ScriptedLLM


@pytest.mark.asyncio
async def test_real_adapter_request_contract():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fixture-not-a-key"
        body = json.loads(request.content)
        assert body["tool_choice"] == "auto" and body["tools"][0]["type"] == "function"
        return httpx.Response(200,json={"choices":[{"message":{"content":"ok"}}]})
    model = OpenAICompatibleLLM(Settings("fixture-not-a-key",model="fixture"),httpx.MockTransport(handler))
    assert (await model.complete([{"role":"user","content":"hi"}],[{"type":"function"}]))["content"] == "ok"


@pytest.mark.asyncio
async def test_adapter_retry_500_and_redact_401():
    attempts = []
    def handler(req):
        attempts.append(1)
        return httpx.Response(500 if len(attempts)==1 else 200,json={"choices":[{"message":{"content":"ok"}}]})
    s = Settings("fixture-not-a-key",model="fixture")
    assert (await OpenAICompatibleLLM(s,httpx.MockTransport(handler)).complete([],[]))["content"] == "ok"
    assert len(attempts)==2
    bad = OpenAICompatibleLLM(s,httpx.MockTransport(lambda r:httpx.Response(401,text="secret-provider-body")))
    with pytest.raises(LLMError,match="llm_http_401") as exc:
        await bad.complete([],[])
    assert "secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_adapter_requires_explicit_credentials():
    with pytest.raises(LLMError,match="not_configured"):
        await OpenAICompatibleLLM(Settings("")).complete([],[])


def app_for(tmp_path, configured=True):
    s = Settings("fixture-not-a-key" if configured else "",model="fixture" if configured else "",db=str(tmp_path/"web.db"))
    store = Store(s.db)
    rt = Runtime(store,build_registry(store),ScriptedLLM({"content":"你好"},{"content":"second"}))
    return create_app(s,rt)


def test_web_chat_history_and_isolation(tmp_path):
    with TestClient(app_for(tmp_path)) as c:
        assert c.get("/").status_code == 200
        body = {"user_id":"A","session_id":"1","request_id":"r","message":"hi"}
        assert c.post("/api/chat",json=body).json()["answer"] == "你好"
        assert c.get("/api/history",params={"user_id":"A","session_id":"1"}).json()["messages"]
        assert c.get("/api/history",params={"user_id":"B","session_id":"1"}).json()["messages"] == []
        assert c.get("/api/sessions",params={"user_id":"A"}).json()["sessions"][0]["session_id"] == "1"
        assert c.get("/api/health").json()["configured"]
        assert "fixture-not-a-key" not in c.get("/api/health").text


def test_web_unconfigured_validation_and_origin(tmp_path):
    with TestClient(app_for(tmp_path,False)) as c:
        body = {"user_id":"A","session_id":"1","request_id":"r","message":"hi"}
        assert c.post("/api/chat",json=body).status_code == 503
        assert c.post("/api/chat",json={**body,"message":" "}).status_code == 422
        assert c.post("/api/chat",json={**body,"user_id":"../B"}).status_code == 422
        assert c.post("/api/chat",json=body,headers={"Origin":"https://evil.example"}).status_code == 403
        assert c.get("/",headers={"Host":"evil.example"}).status_code == 400


def test_sse_finishes_and_replay(tmp_path):
    with TestClient(app_for(tmp_path)) as c:
        body = {"user_id":"A","session_id":"1","request_id":"r","message":"hi"}
        with c.stream("POST","/api/chat/stream",json=body) as response:
            assert response.status_code==200
            data = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
        assert data[0]["type"] == "queued"
        assert data[-1]["type"] == "final" and data[-1]["answer"] == "你好"
        assert c.post("/api/chat",json=body).json()["answer"] == "你好"


def test_recover_interrupted_and_context_budget(tmp_path):
    s = Store(tmp_path/"recover.db")
    s.start("A","1","r","question")
    s.recover()
    assert s.turn("A","1","r")["status"] == "interrupted"
    assert s.history("A","1")[-1]["role"] == "assistant"
    assert s.history("A","1",budget=1) == []
