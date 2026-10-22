import json
import httpx
import pytest
from minagent.llm import OpenAICompatibleLLM
from minagent.config import Settings

@pytest.mark.asyncio
async def test_stream_fragments():
    chunks=[{"choices":[{"delta":{"content":"你好"}}]}, {"choices":[{"delta":{"content":"世界"}}]}]
    def handler(req):
        assert json.loads(req.content)["stream"] is True
        return httpx.Response(200,text="".join("data: "+json.dumps(c)+"\n\n" for c in chunks)+"data: [DONE]\n\n")
    llm=OpenAICompatibleLLM(Settings("fixture",model="fixture"),httpx.MockTransport(handler))
    events=[]
    async def emit(text): events.append(text)
    raw=await llm.complete_stream([],[],emit)
    assert raw["content"]=="你好世界" and events==["你好","世界"]
