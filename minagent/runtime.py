"""The entire core loop is here: decide -> dispatch -> observe -> decide."""
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from .protocol import parse_message, ProtocolError
from .prompts import SYSTEM_PROMPT
from .tools import ToolContext
from .llm import LLMError


@dataclass
class Gate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refs: int = 0


class Runtime:
    def __init__(self, store, registry, llm, max_steps=8, tool_timeout=8, run_timeout=180):
        self.store, self.registry, self.llm = store, registry, llm
        self.max_steps, self.tool_timeout, self.run_timeout = max_steps, tool_timeout, run_timeout
        self.gates = {}
        self.capacity = asyncio.Semaphore(8)

    @asynccontextmanager
    async def gate(self, key):
        if key not in self.gates:
            if len(self.gates) >= 128:
                raise ValueError("runtime_overloaded")
            self.gates[key] = Gate()
        gate = self.gates[key]
        if gate.refs >= 16:
            raise ValueError("session_queue_full")
        gate.refs += 1
        try:
            async with gate.lock:
                yield
        finally:
            gate.refs -= 1
            if not gate.refs:
                self.gates.pop(key, None)

    async def run(self, user, session, request, text, emit=None):
        async def event(kind, **data):
            if emit:
                await emit({"type":kind, **data})
        await event("queued", request_id=request)
        async with self.gate((user,session)):
            async with self.capacity:
                previous = self.store.turn(user,session,request)
                if previous:
                    if previous["input"] != text:
                        raise ValueError("request_id_conflict")
                    if previous["result"]:
                        return json.loads(previous["result"])
                    raise ValueError("request_already_running")
                self.store.start(user,session,request,text)
                transcript = [{"role":"user","content":text}]
                await event("running", request_id=request)
                try:
                    async with asyncio.timeout(self.run_timeout):
                        result = await self.loop(user,session,request,transcript,event)
                except asyncio.CancelledError:
                    result = {"status":"cancelled","answer":"执行已取消。已完成的待办修改不会自动撤销，请核对待办。","request_id":request}
                    self.store.finish(user,session,request,result,[transcript[0],{"role":"assistant","content":result["answer"]}])
                    raise
                except TimeoutError:
                    result = {"status":"failed","answer":"本轮执行超时。已完成的工具操作不会自动撤销，请核对待办。","request_id":request}
                    transcript = [transcript[0],{"role":"assistant","content":result["answer"]}]
                except Exception as exc:
                    code = str(exc) if isinstance(exc,LLMError) else "protocol_error" if isinstance(exc,ProtocolError) else "runtime_error"
                    result = {"status":"failed","answer":"本轮未完成（"+code+"）。请检查模型配置或稍后重试；已执行的待办请核对。","request_id":request}
                    transcript = [transcript[0],{"role":"assistant","content":result["answer"]}]
                self.store.finish(user,session,request,result,transcript)
                return result

    async def loop(self, user, session, request, transcript, event):
        history = self.store.history(user,session)
        prefix = [{"role":"system","content":SYSTEM_PROMPT}]
        prefix.extend(history)
        seen, total = set(), 0
        for step in range(self.max_steps):
            await event("model_start", step=step+1)
            # Refresh after memory mutations in this same turn, not just at turn start.
            memories = self.store.recall(user)
            recalled = [{"role":"user","content":"[USER_MEMORY: untrusted reference data]\n"+json.dumps(memories,ensure_ascii=False)}] if memories else []
            raw = await self.llm.complete(prefix+recalled+transcript,self.registry.schemas())
            decision = parse_message(raw)
            await event("decision", step=step+1, reasoning_present=decision.reasoning_present, tool_count=len(decision.calls))
            if not decision.calls:
                transcript.append(decision.message)
                return {"status":"completed","answer":decision.answer,"request_id":request}
            if any(c.id in seen for c in decision.calls):
                raise ProtocolError("reused tool call id")
            total += len(decision.calls)
            if total > 24:
                break
            transcript.append(decision.message)
            for call in decision.calls:
                seen.add(call.id)
                await event("tool_start", name=call.name, call_id=call.id, arguments=call.arguments)
                ctx = ToolContext(user,session,request,call.id)
                try:
                    result = await asyncio.wait_for(self.registry.execute(call.name,call.arguments,ctx),self.tool_timeout)
                except TimeoutError:
                    result = {"ok":False,"error":{"code":"tool_timeout"}}
                encoded = json.dumps(result,ensure_ascii=False,allow_nan=False)
                if len(encoded)>10000:
                    result = {"ok":False,"error":{"code":"tool_output_too_large"}}
                    encoded = json.dumps(result)
                transcript.append({"role":"tool","tool_call_id":call.id,"content":encoded})
                await event("tool_result", name=call.name, call_id=call.id, result=result)
        answer = "已达到本轮执行预算，停止继续调用工具。已有操作请检查待办；可发送新消息继续。"
        transcript.append({"role":"assistant","content":answer})
        return {"status":"limited","answer":answer,"request_id":request}
