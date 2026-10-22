"""Persistent cosine retrieval. Local hashing is lexical; optional HTTP embeddings are semantic."""
import hashlib
import json
import math
import re
import httpx
from .store import dump


def normalize(vector):
    if not vector or len(vector)>8192 or not all(type(v) in (int,float) and math.isfinite(v) for v in vector):
        raise ValueError("invalid_embedding")
    length=math.sqrt(sum(v*v for v in vector))
    return [v/length for v in vector] if length else list(vector)


class LocalEmbedding:
    name="local-char-ngram-v1-512"
    async def embed(self,texts):
        result=[]
        for text in texts:
            text=re.sub(r"\s+","",text.lower());v=[0.0]*512
            tokens=[text[i:i+n] for n in (1,2,3) for i in range(max(0,len(text)-n+1))]
            for token in tokens:
                h=hashlib.blake2b(token.encode(),digest_size=8).digest()
                v[int.from_bytes(h,"big")%512]+=1
            result.append(normalize(v))
        return result


class HTTPEmbedding:
    def __init__(self,settings,transport=None):
        self.settings,self.transport=settings,transport
        self.name="http:"+hashlib.sha256((settings.embedding_url+settings.embedding_model).encode()).hexdigest()
    async def embed(self,texts):
        s=self.settings
        async with httpx.AsyncClient(timeout=s.timeout,transport=self.transport) as client:
            response=await client.post(s.embedding_url.rstrip("/")+"/embeddings",headers={"Authorization":"Bearer "+s.embedding_key},json={"model":s.embedding_model,"input":texts})
            if response.is_error:raise ValueError("embedding_http_"+str(response.status_code))
            rows=sorted(response.json()["data"],key=lambda r:r["index"])
            if [r["index"] for r in rows]!=list(range(len(texts))):raise ValueError("embedding_batch_mismatch")
            vectors=[normalize(r["embedding"]) for r in rows]
            if len({len(v) for v in vectors})>1:raise ValueError("embedding_dimension_mismatch")
            return vectors


class VectorIndex:
    def __init__(self,store,embedding=None):
        self.store,self.embedding=store,embedding or LocalEmbedding()
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS vectors(user_id TEXT,scope TEXT,id TEXT,model TEXT,text TEXT,hash TEXT,vector TEXT,PRIMARY KEY(user_id,scope,id,model))")

    async def sync(self,user,scope,documents):
        # Caller holds the session lock for history; explicit preferences always sync by source.
        with self.store.connect() as db:
            rows=db.execute("SELECT id,hash FROM vectors WHERE user_id=? AND scope=? AND model=?",(user,scope,self.embedding.name)).fetchall()
        known={r["id"]:r["hash"] for r in rows}
        changed=[(str(i),text,hashlib.sha256(text.encode()).hexdigest()) for i,text in documents.items() if known.get(str(i))!=hashlib.sha256(text.encode()).hexdigest()]
        for start in range(0,len(changed),32):
            batch=changed[start:start+32]
            vectors=await self.embedding.embed([r[1] for r in batch])
            if len(vectors)!=len(batch):raise ValueError("embedding_count_mismatch")
            with self.store.connect() as db:
                for (ident,text,h),vector in zip(batch,vectors):
                    db.execute("INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?)",(user,scope,ident,self.embedding.name,text,h,dump(vector)))
        with self.store.connect() as db:
            for ident in known.keys()-set(map(str,documents)):
                db.execute("DELETE FROM vectors WHERE user_id=? AND scope=? AND id=?",(user,scope,ident))

    async def search(self,user,scope,query,limit=3):
        with self.store.connect() as db:
            rows=db.execute("SELECT * FROM vectors WHERE user_id=? AND scope=? AND model=?",(user,scope,self.embedding.name)).fetchall()
        if not rows:return []
        q=(await self.embedding.embed([query]))[0];ranked=[]
        for row in rows:
            v=json.loads(row["vector"])
            if len(q)!=len(v):raise ValueError("embedding_dimension_mismatch")
            score=sum(a*b for a,b in zip(q,v))
            if score>.05:ranked.append({"id":row["id"],"text":row["text"],"score":round(score,5),"model":self.embedding.name})
        return sorted(ranked,key=lambda r:r["score"],reverse=True)[:limit]
