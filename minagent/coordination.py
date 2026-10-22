"""Same-host process exclusion. Kernel locks release on crash, no stale PID guessing."""
import asyncio
import hashlib
import os
from pathlib import Path
from contextlib import asynccontextmanager


@asynccontextmanager
async def session_lock(database, user, session, timeout=240):
    path=Path(database).resolve()
    directory=path.parent/(path.name+".locks")
    directory.mkdir(parents=True,exist_ok=True)
    name=hashlib.sha256((user+"\0"+session).encode()).hexdigest()
    handle=open(directory/(name+".lock"),"a+b")
    handle.seek(0,2)
    if handle.tell()==0:
        handle.write(b"0");handle.flush()
    acquired=False
    loop=asyncio.get_running_loop();deadline=loop.time()+timeout
    try:
        while not acquired:
            try:
                handle.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                acquired=True
            except (BlockingIOError,OSError):
                if loop.time()>=deadline: raise TimeoutError("session_lock_timeout")
                await asyncio.sleep(.05)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        handle.close()
