"""Explicit offline browser fixture. Not imported by the production entry point."""
import json
import uvicorn
from minagent.config import Settings
from minagent.store import Store
from minagent.tools import build_registry
from minagent.runtime import Runtime
from minagent.web import create_app


class FixtureLLM:
    async def complete(self,messages,tools):
        last = messages[-1]
        if last["role"] == "tool":
            data=json.loads(last["content"])
            return {"content":"离线 UI 验证："+str(data.get("result",data))+"（不是实际模型回复）"}
        text=last["content"]
        name="calculator" if "计算" in text else "todo"
        args={"expression":"(128+256)*3/8"} if name=="calculator" else {"action":"add","text":"离线 UI 验证待办"}
        return {"content":None,"tool_calls":[{"id":"fixture-call","type":"function","function":{"name":name,"arguments":json.dumps(args)}}]}


if __name__ == "__main__":
    settings=Settings("fixture-not-a-key",model="OFFLINE UI FIXTURE — NOT REAL LLM",db="artifacts/ui-fixture.sqlite3")
    store=Store(settings.db)
    uvicorn.run(create_app(settings,Runtime(store,build_registry(store),FixtureLLM())),host="127.0.0.1",port=8766)
