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


@pytest.mark.asyncio
async def test_stream_private_tags_split_across_packets():
    pieces=['<th','ink>private',' reasoning</thi','nk>公开','回答']
    wire=''.join('data: '+json.dumps({'choices':[{'delta':{'content':p}}]})+'\n\n' for p in pieces)+'data: [DONE]\n\n'
    llm=OpenAICompatibleLLM(Settings('fixture',model='fixture'),httpx.MockTransport(lambda r:httpx.Response(200,text=wire)))
    visible=[]
    async def emit(t):visible.append(t)
    await llm.complete_stream([],[],emit)
    assert ''.join(visible)=='公开回答'


@pytest.mark.asyncio
async def test_stream_tool_arguments_assembled_before_execution():
    chunks=[{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','function':{'name':'calculator','arguments':'{"expression":'}}]}}]},
            {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"1+2"}'}}]}}]}]
    wire=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
    llm=OpenAICompatibleLLM(Settings('fixture',model='fixture'),httpx.MockTransport(lambda r:httpx.Response(200,text=wire)))
    async def emit(t):pass
    raw=await llm.complete_stream([],[],emit)
    assert json.loads(raw['tool_calls'][0]['function']['arguments'])=={'expression':'1+2'}


@pytest.mark.asyncio
async def test_stream_disconnect_not_reported_as_success():
    from minagent.llm import LLMError
    wire='data: '+json.dumps({'choices':[{'delta':{'content':'partial'}}]})+'\n\n'
    llm=OpenAICompatibleLLM(Settings('fixture',model='fixture'),httpx.MockTransport(lambda r:httpx.Response(200,text=wire)))
    async def emit(t):pass
    with pytest.raises(LLMError,match='incomplete'):await llm.complete_stream([],[],emit)
