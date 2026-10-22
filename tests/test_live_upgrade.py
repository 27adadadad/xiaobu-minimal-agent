import os
import pytest
from minagent.config import Settings
from minagent.llm import OpenAICompatibleLLM
from minagent.store import Store
from minagent.tools import build_registry,weather,ToolContext
from minagent.runtime import Runtime
from minagent.context import ContextManager
from minagent.jobs import JobQueue,Worker

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv("RUN_LIVE")!="1",reason="explicit real API opt-in required")]

async def test_live_semantic_compaction_and_recall(tmp_path):
    store=Store(tmp_path/'summary.db');llm=OpenAICompatibleLLM(Settings.from_env())
    for i in range(16):
        text='目标：优化保温杯主图，必须补杯底直径，不要自动降价。' if i==0 else f'第{i}次核对：杯底直径为7厘米，店铺在上海。'
        store.start('live','1',str(i),text)
        store.finish('live','1',str(i),{'status':'completed','answer':'已记录'},[{'role':'user','content':text},{'role':'assistant','content':'已记录'}])
    c=ContextManager(store,llm)
    messages=await c.prepare('live','1','主图需要补什么尺寸信息？')
    assert c.saved('live','1') is not None and c.last_error is None
    assert '不要自动降价' in str(messages)
    rt=Runtime(store,build_registry(store),llm)
    result=await rt.run('live','1','recall','根据历史，主图要补哪项尺寸？只回答该尺寸。')
    assert result['status']=='completed' and '杯底' in result['answer']

async def test_live_background_decision_and_execution(tmp_path):
    store=Store(tmp_path/'jobs.db');registry=build_registry(store)
    runtime=Runtime(store,registry,OpenAICompatibleLLM(Settings.from_env()))
    result=await runtime.run('live','1','submit','请用 background 在后台计算 (128+256)*3/8，只提交一个任务并告知任务编号，不要在前台调用 calculator。')
    assert result['status']=='completed'
    queue=JobQueue(store);jobs=queue.list('live')
    assert len(jobs)==1 and jobs[0]['tool']=='calculator'
    assert await Worker(queue,registry).tick()
    assert queue.get('live',jobs[0]['id'])['result']['result']==144
    assert len(queue.notifications('live'))==1

async def test_live_weather_forecast():
    result=await weather({'city':'Shanghai','country_code':'CN','days':3},ToolContext('probe','1','r','c'))
    if not result.get('ok') and result.get('candidates'):
        selected=result['candidates'][0]['id']
        result=await weather({'city':'Shanghai','country_code':'CN','location_id':selected,'days':3},ToolContext('probe','1','r','c'))
    assert result['ok'] and len(result['daily']['time'])==3
