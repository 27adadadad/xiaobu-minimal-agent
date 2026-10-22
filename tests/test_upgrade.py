import asyncio
import json
import time
import pytest
from minagent.store import Store
from minagent.jobs import JobQueue, Worker
from minagent.vectors import VectorIndex, LocalEmbedding
from minagent.context import ContextManager
from minagent.coordination import session_lock
from minagent.tools import build_registry, ToolContext

@pytest.fixture
def store(tmp_path): return Store(tmp_path/"s.db")

@pytest.mark.asyncio
async def test_vector_persistence_scope_and_deletion(store):
    index=VectorIndex(store,LocalEmbedding())
    await index.sync("A","1",{"one":"上海车载保温杯价格","two":"北京降雨预报"})
    assert (await index.search("A","1","保温杯价格"))[0]["id"]=="one"
    assert await index.search("B","1","保温杯价格")==[]
    assert await index.search("A","2","保温杯价格")==[]
    await index.sync("A","1",{"two":"北京降雨预报"})
    assert all(r["id"]!="one" for r in await VectorIndex(store,LocalEmbedding()).search("A","1","保温杯"))


def test_job_idempotency_scope_and_restart(store):
    q=JobQueue(store)
    a=q.submit("A","1","r","calculator",{"expression":"1+1"})
    assert q.submit("A","1","r","calculator",{"expression":"1+1"})==a
    with pytest.raises(ValueError): q.submit("A","1","r","calculator",{"expression":"2+2"})
    assert JobQueue(Store(store.path)).get("A",a["id"])["state"]=="queued"
    assert q.get("B",a["id"]) is None
    assert not q.cancel("B",a["id"])


def test_job_lease_takeover_and_fenced_completion(store,monkeypatch):
    import minagent.jobs as jobs_module
    clock=[100.0]
    monkeypatch.setattr(jobs_module.time,"time",lambda:clock[0])
    q=JobQueue(store);j=q.submit("A","1","r","calculator",{"expression":"1"})
    a=q.claim("worker1",lease_seconds=10); assert a
    assert q.claim("worker2") is None
    clock[0]+=11
    b=q.claim("worker2"); assert b["id"]==a["id"] and b["token"]!=a["token"]
    assert not q.finish(a,{"ok":True})
    assert q.finish(b,{"ok":True})
    assert len(q.notifications("A"))==1
    assert q.notifications("B")==[]
    q.ack("A",j["id"]);assert q.notifications("A")==[]


@pytest.mark.asyncio
async def test_background_worker_todo_exact_receipt(store):
    q=JobQueue(store);j=q.submit("A","1","r","todo",{"action":"add","text":"恢复后不重复"})
    worker=Worker(q,build_registry(store))
    assert await worker.tick()
    assert q.get("A",j["id"])["state"]=="succeeded"
    assert len(store.todos("A"))==1
    assert not await worker.tick()


@pytest.mark.asyncio
async def test_job_cancel_and_validation(store):
    q=JobQueue(store);j=q.submit("A","1","r","search",{"query":"x"},delay=100)
    assert q.claim("w") is None
    assert q.cancel("A",j["id"])
    assert q.get("A",j["id"])["state"]=="cancelled"
    registry=build_registry(store)
    result=await registry.execute("background",{"action":"submit","tool":"calculator","arguments":{"expression":"1+1"}},ToolContext("A","1","r2","c"))
    assert result["ok"] and result["job"]["state"]=="queued"
    assert not (await registry.execute("background",{"action":"submit","tool":"background","arguments":{}},ToolContext("A","1","r3","c")))["ok"]


class SummaryLLM:
    async def complete(self,messages,tools):
        source=json.loads(messages[-1]["content"])
        return {"content":json.dumps({"objective":"完成任务","constraints":[],"facts":["用户在上海"],"decisions":[],"open_items":[],"references":[r["seq"] for r in source["turns"]]})}


def populate(store,n=16):
    for i in range(n):
        text="必须使用中文，不要自动降价" if i==0 else f"第{i}轮 上海保温杯"
        store.start("A","1",str(i),text)
        store.finish("A","1",str(i),{"status":"completed","answer":"好"},[{"role":"user","content":text},{"role":"assistant","content":"好"}])


@pytest.mark.asyncio
async def test_semantic_summary_persistent_and_anchored(store):
    populate(store)
    context=ContextManager(store,SummaryLLM())
    result=await context.prepare("A","1","保温杯")
    assert any("SESSION_SUMMARY" in (m.get("content") or "") for m in result)
    assert "必须使用中文，不要自动降价" in json.dumps(result,ensure_ascii=False)
    assert context.saved("A","1")["through_seq"]>0
    assert ContextManager(Store(store.path),SummaryLLM()).saved("A","1")
    assert context.saved("B","1") is None


@pytest.mark.asyncio
async def test_bad_summary_keeps_previous_snapshot(store):
    populate(store)
    c=ContextManager(store,SummaryLLM());await c.prepare("A","1","x"); before=c.saved("A","1")
    for i in range(16,25):
        store.start("A","1",str(i),"new")
        store.finish("A","1",str(i),{"status":"completed"},[{"role":"user","content":"new"},{"role":"assistant","content":"ok"}])
    class Bad:
        async def complete(self,*a): return {"content":"not-json"}
    await ContextManager(store,Bad()).prepare("A","1","x")
    assert c.saved("A","1")==before


@pytest.mark.asyncio
async def test_worker_cancel_running_does_not_kill_worker(store):
    q=JobQueue(store)
    registry=build_registry(store)
    entered=asyncio.Event()
    async def slow(args,ctx):
        entered.set();await asyncio.sleep(10)
        return {"ok":True}
    d,s,_=registry.tools["search"];registry.tools["search"]=(d,s,slow)
    j=q.submit("A","1","first","search",{"query":"x"})
    worker=Worker(q,registry,lease_seconds=.09)
    task=asyncio.create_task(worker.tick())
    await entered.wait();q.cancel("A",j["id"])
    assert await asyncio.wait_for(task,1)
    q.submit("A","1","second","calculator",{"expression":"2+3"})
    assert await worker.tick()
    assert any(j["state"]=="succeeded" for j in q.list("A"))


@pytest.mark.asyncio
async def test_real_process_lock_release_after_kill(store,tmp_path):
    import subprocess,sys
    ready=tmp_path/"ready"
    code = """
import asyncio,sys
from pathlib import Path
from minagent.coordination import session_lock
from minagent.store import Store
async def main():
    store=Store(sys.argv[1])
    async with session_lock(store.path,'A','1'):
        store.start('A','1','crashed','do not replay')
        Path(sys.argv[2]).write_text('ready')
        await asyncio.sleep(60)
asyncio.run(main())
"""
    child=subprocess.Popen([sys.executable,"-c",code,store.path,str(ready)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(500):
            if ready.exists():break
            if child.poll() is not None:raise AssertionError(child.communicate())
            await asyncio.sleep(.02)
        assert ready.exists()
        acquired=asyncio.Event()
        async def contender():
            async with session_lock(store.path,"A","1",timeout=5):
                acquired.set();store.recover("A","1")
        task=asyncio.create_task(contender())
        await asyncio.sleep(.15);assert not acquired.is_set()
        child.kill();child.wait(timeout=5)
        await asyncio.wait_for(task,5)
        assert store.turn("A","1","crashed")["status"]=="interrupted"
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
        child.communicate()


@pytest.mark.asyncio
async def test_process_job_takeover_preserves_side_effect_receipt(store,tmp_path):
    import subprocess,sys
    q=JobQueue(store);job=q.submit("A","1","durable","todo",{"action":"add","text":"kill-safe"})
    ready=tmp_path/"claimed"
    code="""
import sys,time,json
from pathlib import Path
from minagent.store import Store
from minagent.jobs import JobQueue
from minagent.tools import ToolContext
s=Store(sys.argv[1]);q=JobQueue(s);j=q.claim('killed-worker',lease_seconds=.3)
s.mutate('todo',json.loads(j['arguments']),ToolContext(j['user_id'],j['session_id'],'job-'+j['id'],'fixed-call'))
Path(sys.argv[2]).write_text('committed')
time.sleep(60)
"""
    child=subprocess.Popen([sys.executable,"-c",code,store.path,str(ready)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(500):
            if ready.exists():break
            if child.poll() is not None:raise AssertionError(child.communicate())
            await asyncio.sleep(.02)
        assert ready.exists();child.kill();child.wait(timeout=5)
        await asyncio.sleep(.35)
        assert await Worker(JobQueue(Store(store.path)),build_registry(store)).tick()
        assert q.get("A",job["id"])["state"]=="succeeded"
        assert len(store.todos("A"))==1
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
        child.communicate()


@pytest.mark.asyncio
async def test_http_embeddings_contract(store):
    import httpx
    from minagent.config import Settings
    from minagent.vectors import HTTPEmbedding
    def handler(req):
        assert req.url.path.endswith('/embeddings')
        assert json.loads(req.content)['model']=='fixture-embed'
        return httpx.Response(200,json={"data":[{"index":0,"embedding":[3,4]}]})
    embedding=HTTPEmbedding(Settings('fixture',embedding_model='fixture-embed',embedding_key='fixture'),httpx.MockTransport(handler))
    assert await embedding.embed(['test'])==[[.6,.8]]


@pytest.mark.asyncio
async def test_forecast_and_ambiguous_city(monkeypatch,store):
    import httpx
    from minagent.tools import weather
    original=httpx.AsyncClient
    places=[{"id":1,"name":"Springfield","country":"US","country_code":"US","latitude":1,"longitude":1},{"id":2,"name":"Springfield","country":"CA","country_code":"CA","latitude":2,"longitude":2}]
    requests=[]
    def handler(req):
        requests.append(req)
        if 'geocoding' in str(req.url):return httpx.Response(200,json={"results":places})
        assert req.url.params['forecast_days']=='3'
        return httpx.Response(200,json={"current":{"temperature_2m":20},"daily":{"time":["2026-10-22","2026-10-23"],"temperature_2m_max":[21,22]}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(handler)))
    ctx=ToolContext('A','1','r','c')
    result=await weather({"city":"Springfield"},ctx)
    assert result['error']['code']=='city_ambiguous' and len(requests)==1
    result=await weather({"city":"Springfield","location_id":2,"days":3},ctx)
    assert result['ok'] and result['country']=='CA' and result['daily']['time'][1]=='2026-10-23'
