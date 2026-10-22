"""Local demo transport. user_id is a test identity, NOT production authentication."""
import asyncio
import json
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import Settings
from .store import Store
from .tools import build_registry
from .llm import OpenAICompatibleLLM
from .runtime import Runtime
from .jobs import JobQueue,Worker

ID = Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class ChatInput(BaseModel):
    user_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    message: str = Field(min_length=1,max_length=4000)

    @field_validator("message")
    @classmethod
    def not_blank(cls,value):
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()


def create_app(settings=None, runtime=None):
    settings = settings or Settings.from_env()
    if runtime is None:
        store = Store(settings.db)
        runtime = Runtime(store,build_registry(store),OpenAICompatibleLLM(settings))
    @asynccontextmanager
    async def lifespan(app):
        worker=asyncio.create_task(Worker(JobQueue(runtime.store),runtime.registry).serve())
        try:yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):await worker
    app = FastAPI(title="Handbuilt Agent",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.runtime = runtime
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["localhost","127.0.0.1","testserver"])

    @app.middleware("http")
    async def local_security(request, call_next):
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail":"cross_origin_rejected"},status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    root = Path(__file__).resolve().parent.parent/"web"
    app.mount("/static",StaticFiles(directory=root),name="static")

    @app.get("/")
    async def index():
        return FileResponse(root/"index.html")

    @app.get("/api/health")
    async def health():
        return {"ok":True,"configured":settings.configured,"model":settings.model or "未配置","mode":"real-llm","authentication":"local-demo-only"}

    @app.get("/api/sessions")
    async def sessions(user_id: ID):
        return {"sessions":runtime.store.sessions(user_id)}

    @app.get("/api/history")
    async def history(user_id: ID, session_id: ID):
        return {"messages":runtime.store.history(user_id,session_id,limit=100,budget=200000)}

    @app.get("/api/todos")
    async def todos(user_id: ID):
        return {"items":runtime.store.todos(user_id)}

    jobs=JobQueue(runtime.store)

    @app.get("/api/jobs")
    async def job_list(user_id: ID):
        return {"jobs":jobs.list(user_id),"notifications":jobs.notifications(user_id)}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str,user_id: ID):
        return {"ok":jobs.cancel(user_id,job_id)}

    @app.post("/api/jobs/{job_id}/ack")
    async def ack_job(job_id: str,user_id: ID):
        jobs.ack(user_id,job_id)
        return {"ok":True}

    def require_model():
        if not settings.configured:
            raise HTTPException(503,"请在本地 .env 配置 LLM_API_KEY 和 LLM_MODEL，服务从不自动切换为假模型。")

    @app.post("/api/chat")
    async def chat(body: ChatInput):
        require_model()
        try:
            return await runtime.run(body.user_id,body.session_id,body.request_id,body.message)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc

    @app.post("/api/chat/stream")
    async def stream(body: ChatInput):
        require_model()
        async def generate():
            queue = asyncio.Queue(maxsize=128)
            async def worker():
                try:
                    result = await runtime.run(body.user_id,body.session_id,body.request_id,body.message,queue.put)
                    await queue.put({"type":"final",**result})
                except ValueError as exc:
                    await queue.put({"type":"error","message":str(exc)})
                except Exception:
                    await queue.put({"type":"error","message":"stream_error"})
                finally:
                    await queue.put(None)
            task = asyncio.create_task(worker())
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(),12)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if item is None:
                        break
                    yield "data: " + json.dumps(item,ensure_ascii=False) + "\n\n"
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        return StreamingResponse(generate(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
    return app
