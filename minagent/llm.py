"""Real HTTP adapter; tests inject a transport instead of changing production routing."""
import asyncio
import httpx
from .config import Settings


class LLMError(RuntimeError):
    pass


class OpenAICompatibleLLM:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport

    async def complete(self, messages, tools):
        s = self.settings
        if not s.configured:
            raise LLMError("llm_not_configured")
        body = {"model": s.model, "messages": messages, "tools": tools, "tool_choice": "auto", "stream": False}
        if not tools:
            body.pop("tools",None); body.pop("tool_choice",None)
        async with httpx.AsyncClient(timeout=s.timeout, transport=self.transport) as client:
            for attempt in range(3):
                try:
                    response = await client.post(s.base_url + "/chat/completions", headers={"Authorization": "Bearer " + s.api_key}, json=body)
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt == 2:
                        raise LLMError("llm_network_error") from exc
                    await asyncio.sleep(0.2 * 2 ** attempt)
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        await asyncio.sleep(0.2 * 2 ** attempt)
                        continue
                if response.is_error:
                    # Do not propagate provider bodies: they can contain credentials or user data.
                    raise LLMError("llm_http_" + str(response.status_code))
                try:
                    return response.json()["choices"][0]["message"]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise LLMError("llm_invalid_response") from exc
        raise LLMError("llm_unavailable")


    async def complete_stream(self, messages, tools, emit):
        import json
        s = self.settings
        if not s.configured:
            raise LLMError("llm_not_configured")
        content, calls, pending, hidden = "", {}, "", False
        body = {"model":s.model,"messages":messages,"tools":tools,"tool_choice":"auto","stream":True}
        done = False
        try:
            async with httpx.AsyncClient(timeout=s.timeout,transport=self.transport) as client:
                async with client.stream("POST",s.base_url+"/chat/completions",headers={"Authorization":"Bearer "+s.api_key},json=body) as response:
                    if response.is_error:
                        raise LLMError("llm_http_"+str(response.status_code))
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data=line[5:].strip()
                        if data=="[DONE]":
                            done=True
                            break
                        payload=json.loads(data)
                        if payload.get("error"):
                            raise LLMError("llm_stream_error")
                        for choice in payload.get("choices",[]):
                            if choice.get("index",0)!=0:
                                continue
                            delta=choice.get("delta",{})
                            chunk=delta.get("content") or ""
                            content+=chunk
                            # Keep partial tags buffered so split <think> blocks cannot leak.
                            pending+=chunk
                            visible=""
                            while pending:
                                tag="</think>" if hidden else "<think>"
                                pos=pending.lower().find(tag)
                                if pos>=0:
                                    if not hidden: visible+=pending[:pos]
                                    pending=pending[pos+len(tag):]
                                    hidden=not hidden
                                else:
                                    keep=0
                                    for n in range(1,min(len(tag),len(pending))+1):
                                        if tag.startswith(pending[-n:].lower()): keep=n
                                    flush=pending[:-keep] if keep else pending
                                    if not hidden: visible+=flush
                                    pending=pending[-keep:] if keep else ""
                                    break
                            if visible: await emit(visible)
                            for tool in delta.get("tool_calls",[]):
                                index=tool["index"]
                                if index not in calls:
                                    if len(calls)>=8: raise LLMError("excessive_tool_calls")
                                    calls[index]={"id":"","type":"function","function":{"name":"","arguments":""}}
                                target=calls[index]
                                target["id"]+=tool.get("id","")
                                for key in ("name","arguments"):
                                    target["function"][key]+=tool.get("function",{}).get(key,"")
            if not done: raise LLMError("llm_stream_incomplete")
            if pending and not hidden and not "<think>".startswith(pending.lower()): await emit(pending)
            return {"role":"assistant","content":content or None,**({"tool_calls":[calls[i] for i in sorted(calls)]} if calls else {})}
        except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
            raise LLMError("llm_stream_error") from exc
