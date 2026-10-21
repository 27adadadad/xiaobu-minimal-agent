"""SQLite authority. One connection per short transaction; no secret-bearing logs."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS turns (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
              request_id TEXT NOT NULL, input TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
              messages TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL,
              UNIQUE(user_id,session_id,request_id));
            CREATE TABLE IF NOT EXISTS todos (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, text TEXT NOT NULL,
              done INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memories (
              user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated TEXT NOT NULL,
              PRIMARY KEY(user_id,key));
            CREATE TABLE IF NOT EXISTS receipts (
              user_id TEXT NOT NULL, session_id TEXT NOT NULL, request_id TEXT NOT NULL,
              call_id TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL,
              PRIMARY KEY(user_id,session_id,request_id,call_id));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def turn(self, user, session, request):
        with self.connect() as db:
            row = db.execute("SELECT * FROM turns WHERE user_id=? AND session_id=? AND request_id=?", (user, session, request)).fetchone()
        return dict(row) if row else None

    def start(self, user, session, request, text):
        with self.connect() as db:
            db.execute("INSERT INTO turns(user_id,session_id,request_id,input,status,created) VALUES(?,?,?,?,?,?)", (user,session,request,text,"running",now()))

    def finish(self, user, session, request, result, messages):
        with self.connect() as db:
            db.execute("UPDATE turns SET status=?,result=?,messages=? WHERE user_id=? AND session_id=? AND request_id=?", (result["status"],dump(result),dump(messages),user,session,request))

    def recover(self):
        # Called once at single-process startup, never once per request.
        with self.connect() as db:
            rows = db.execute("SELECT * FROM turns WHERE status='running'").fetchall()
            for r in rows:
                result = {"status":"interrupted","answer":"上次执行因进程退出而中断。副作用请检查待办；不会自动重放。","request_id":r["request_id"]}
                messages = [{"role":"user","content":r["input"]},{"role":"assistant","content":result["answer"]}]
                db.execute("UPDATE turns SET status=?,result=?,messages=? WHERE seq=?", ("interrupted",dump(result),dump(messages),r["seq"]))

    def history(self, user, session, limit=12, budget=24000):
        with self.connect() as db:
            rows = db.execute("SELECT messages FROM turns WHERE user_id=? AND session_id=? AND status!='running' ORDER BY seq DESC LIMIT ?", (user,session,limit)).fetchall()
        selected, used = [], 0
        for row in rows:
            if used + len(row[0]) > budget:
                break
            selected.append(json.loads(row[0]))
            used += len(row[0])
        return [m for group in reversed(selected) for m in group]

    def sessions(self, user):
        with self.connect() as db:
            rows = db.execute("SELECT t.session_id,(SELECT f.input FROM turns f WHERE f.user_id=t.user_id AND f.session_id=t.session_id ORDER BY f.seq LIMIT 1) AS title,MAX(t.created) AS updated,COUNT(*) AS turns FROM turns t WHERE t.user_id=? GROUP BY t.session_id ORDER BY updated DESC LIMIT 100", (user,)).fetchall()
        return [dict(r) for r in rows]

    def recall(self, user):
        with self.connect() as db:
            rows = db.execute("SELECT key,value,updated FROM memories WHERE user_id=? ORDER BY updated DESC LIMIT 8", (user,)).fetchall()
        return [dict(r) for r in rows]

    def todos(self, user):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,text,done,created FROM todos WHERE user_id=? ORDER BY id DESC LIMIT 200", (user,)).fetchall()]

    def mutate(self, name, args, ctx):
        fingerprint = dump([name,args])
        key = (ctx.user,ctx.session,ctx.request,ctx.call_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT fingerprint,result FROM receipts WHERE user_id=? AND session_id=? AND request_id=? AND call_id=?",key).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    return {"ok":False,"error":{"code":"call_id_conflict"}}
                return json.loads(existing["result"])
            if name == "todo":
                action = args["action"]
                if action == "list":
                    result = {"ok":True,"items":self.todos(ctx.user)}
                elif action == "add":
                    text = args.get("text", "").strip()
                    if not text:
                        return {"ok":False,"error":{"code":"text_required"}}
                    cursor = db.execute("INSERT INTO todos(user_id,text,created) VALUES(?,?,?)",(ctx.user,text,now()))
                    result = {"ok":True,"item":dict(db.execute("SELECT id,text,done,created FROM todos WHERE id=?",(cursor.lastrowid,)).fetchone())}
                else:
                    row = db.execute("SELECT id,text,done,created FROM todos WHERE user_id=? AND id=?",(ctx.user,args.get("id"))).fetchone()
                    if not row:
                        return {"ok":False,"error":{"code":"todo_not_found"}}
                    if action == "done":
                        db.execute("UPDATE todos SET done=1 WHERE user_id=? AND id=?",(ctx.user,args["id"]))
                        result = {"ok":True,"item":{**dict(row),"done":1}}
                    else:
                        db.execute("DELETE FROM todos WHERE user_id=? AND id=?",(ctx.user,args["id"]))
                        result = {"ok":True,"deleted":args["id"]}
            else:
                action, key_name = args["action"], args.get("key")
                if action == "list":
                    result = {"ok":True,"items":self.recall(ctx.user)}
                elif not key_name:
                    return {"ok":False,"error":{"code":"key_required"}}
                elif action == "remember":
                    if not args.get("value", "").strip():
                        return {"ok":False,"error":{"code":"value_required"}}
                    db.execute("INSERT INTO memories(user_id,key,value,updated) VALUES(?,?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value,updated=excluded.updated",(ctx.user,key_name,args["value"],now()))
                    result = {"ok":True,"remembered":key_name}
                else:
                    db.execute("DELETE FROM memories WHERE user_id=? AND key=?",(ctx.user,key_name))
                    result = {"ok":True,"forgotten":key_name}
            db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?)", (*key, fingerprint,dump(result)))
            return result
