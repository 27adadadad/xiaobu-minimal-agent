import argparse
import asyncio
import uvicorn
from .web import create_app
from .config import Settings
from .store import Store
from .tools import build_registry
from .jobs import JobQueue,Worker


def main():
    parser=argparse.ArgumentParser(description="Local hand-built Agent")
    parser.add_argument("--port",type=int,default=8000)
    parser.add_argument("--workers",type=int,choices=range(1,9),default=1)
    parser.add_argument("--worker-only",action="store_true")
    args=parser.parse_args()
    if args.worker_only:
        store=Store(Settings.from_env().db)
        asyncio.run(Worker(JobQueue(store),build_registry(store)).serve())
    elif args.workers>1:
        uvicorn.run("minagent.web:create_app",factory=True,host="127.0.0.1",port=args.port,workers=args.workers)
    else:
        uvicorn.run(create_app(),host="127.0.0.1",port=args.port)


if __name__=="__main__":main()
