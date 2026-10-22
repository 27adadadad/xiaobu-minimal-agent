"""Deterministic multi-worker browser fixture. NEVER a production model."""
import asyncio
import json
import os
from minagent.config import Settings
from minagent.store import Store
from minagent.tools import build_registry
from minagent.runtime import Runtime
from minagent.web import create_app

class Fixture:
    async def complete(self,messages,tools):
        await asyncio.sleep(.15)
        last=messages[-1]
        if last['role']=='tool':return {'content':'离线多进程验证：'+last['content']}
        text=last.get('content','')
        if '后台' in text:
            return {'content':None,'tool_calls':[{'id':'fixture-bg','type':'function','function':{'name':'background','arguments':json.dumps({'action':'submit','tool':'calculator','arguments':{'expression':'128*256'},'delay_seconds':3})}}]}
        return {'content':'离线多进程验证：'+text}

def create_fixture_app():
    settings=Settings('fixture',model='OFFLINE MULTIPROCESS FIXTURE',db=os.environ.get('AGENT_DB','artifacts/full-ui.db'))
    store=Store(settings.db)
    app=create_app(settings,Runtime(store,build_registry(store),Fixture()))
    @app.middleware('http')
    async def pid(request,call_next):
        result=await call_next(request);result.headers['X-Test-Pid']=str(os.getpid());return result
    return app
