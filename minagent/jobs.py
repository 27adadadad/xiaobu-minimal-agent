"""Durable single-tool jobs: atomic claim, heartbeat, retry and fenced completion."""
import asyncio
import json
import time
import uuid
from contextlib import suppress
from .tools import ToolContext
from .store import dump

ALLOWED={"calculator","search","weather","todo","memory"}


class JobQueue:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY,user_id TEXT NOT NULL,session_id TEXT NOT NULL,dedupe TEXT NOT NULL,
              tool TEXT NOT NULL,arguments TEXT NOT NULL,state TEXT NOT NULL,run_at REAL NOT NULL,
              created REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,owner TEXT,token TEXT,
              lease_until REAL,result TEXT,notified INTEGER NOT NULL DEFAULT 0,
              UNIQUE(user_id,session_id,dedupe));
            CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state,run_at,lease_until);
            """)

    def submit(self,user,session,dedupe,tool,arguments,delay=0):
        if tool not in ALLOWED or not isinstance(arguments,dict) or not 0<=delay<=604800:
            raise ValueError("invalid_job")
        encoded=dump(arguments)
        if len(encoded)>16000: raise ValueError("job_too_large")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM jobs WHERE user_id=? AND session_id=? AND dedupe=?",(user,session,dedupe)).fetchone()
            if row:
                if row["tool"]!=tool or row["arguments"]!=encoded: raise ValueError("job_id_conflict")
                return self.public(row)
            ident=uuid.uuid4().hex;t=time.time()
            db.execute("INSERT INTO jobs(id,user_id,session_id,dedupe,tool,arguments,state,run_at,created) VALUES(?,?,?,?,?,?,?,?,?)",(ident,user,session,dedupe,tool,encoded,"queued",t+delay,t))
            return self.public(db.execute("SELECT * FROM jobs WHERE id=?",(ident,)).fetchone())

    @staticmethod
    def public(row):
        if row is None: return None
        return {"id":row["id"],"session_id":row["session_id"],"tool":row["tool"],"arguments":json.loads(row["arguments"]),"state":row["state"],"attempts":row["attempts"],"run_at":row["run_at"],"result":json.loads(row["result"]) if row["result"] else None}

    def get(self,user,ident):
        with self.store.connect() as db:
            return self.public(db.execute("SELECT * FROM jobs WHERE user_id=? AND id=?",(user,ident)).fetchone())

    def list(self,user):
        with self.store.connect() as db:
            return [self.public(r) for r in db.execute("SELECT * FROM jobs WHERE user_id=? ORDER BY created DESC LIMIT 100",(user,))]

    def claim(self,owner,lease_seconds=30):
        t=time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE jobs SET state='failed',result=? WHERE state='running' AND lease_until<? AND attempts>=3",(dump({"ok":False,"error":{"code":"worker_lost_retry_exhausted"}}),t))
            row=db.execute("SELECT * FROM jobs WHERE attempts<3 AND ((state='queued' AND run_at<=?) OR (state='running' AND lease_until<?)) ORDER BY created LIMIT 1",(t,t)).fetchone()
            if not row:return None
            token=uuid.uuid4().hex
            db.execute("UPDATE jobs SET state='running',owner=?,token=?,lease_until=?,attempts=attempts+1 WHERE id=?",(owner,token,t+lease_seconds,row["id"]))
            return dict(db.execute("SELECT * FROM jobs WHERE id=?",(row["id"],)).fetchone())

    def heartbeat(self,job,lease_seconds=30):
        with self.store.connect() as db:
            return db.execute("UPDATE jobs SET lease_until=? WHERE id=? AND token=? AND state='running'",(time.time()+lease_seconds,job["id"],job["token"])).rowcount==1

    def finish(self,job,result):
        with self.store.connect() as db:
            return db.execute("UPDATE jobs SET state=?,result=?,lease_until=NULL WHERE id=? AND token=? AND state='running'",("succeeded" if result.get("ok") else "failed",dump(result),job["id"],job["token"])).rowcount==1

    def cancel(self,user,ident):
        with self.store.connect() as db:
            return db.execute("UPDATE jobs SET state='cancelled',lease_until=NULL WHERE user_id=? AND id=? AND state IN ('queued','running')",(user,ident)).rowcount==1

    def notifications(self,user):
        with self.store.connect() as db:
            return [self.public(r) for r in db.execute("SELECT * FROM jobs WHERE user_id=? AND state IN ('succeeded','failed','cancelled') AND notified=0 ORDER BY created LIMIT 100",(user,))]

    def ack(self,user,ident):
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET notified=1 WHERE user_id=? AND id=?",(user,ident))


class Worker:
    def __init__(self,queue,registry,lease_seconds=30):
        self.queue,self.registry,self.lease_seconds=queue,registry,lease_seconds
        self.owner=uuid.uuid4().hex

    async def tick(self):
        job=self.queue.claim(self.owner,self.lease_seconds)
        if not job:return False
        execution=asyncio.create_task(self.registry.execute(job["tool"],json.loads(job["arguments"]),ToolContext(job["user_id"],job["session_id"],"job-"+job["id"],"fixed-call")))
        async def heartbeat():
            while True:
                await asyncio.sleep(self.lease_seconds/3)
                if not self.queue.heartbeat(job,self.lease_seconds):
                    execution.cancel();return
        pulse=asyncio.create_task(heartbeat())
        try:
            result=await asyncio.wait_for(execution,60)
            self.queue.finish(job,result)
        except TimeoutError:
            self.queue.finish(job,{"ok":False,"error":{"code":"job_timeout"}})
        except asyncio.CancelledError:
            execution.cancel()
            # Leave an interrupted claim to expire, so another worker may take over.
            if asyncio.current_task().cancelling():raise
        finally:
            pulse.cancel()
            with suppress(asyncio.CancelledError):await pulse
            with suppress(asyncio.CancelledError):await execution
        return True

    async def serve(self):
        while True:
            if not await self.tick():await asyncio.sleep(.2)
