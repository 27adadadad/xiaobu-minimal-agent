"use strict";
const $ = (id) => document.getElementById(id);
const welcome = $("welcome").cloneNode(true);
let user = localStorage.getItem("agent-user") || "user-A";
if (!["user-A", "user-B"].includes(user)) user = "user-A";
let session = sessionStorage.getItem("agent-session-" + user) || crypto.randomUUID();
let controller = null, busy = false, configured = false, viewVersion = 0;
$("user").value = user;
function saveSession() { sessionStorage.setItem("agent-session-" + user, session); }
saveSession();
async function get(url) {
  const res = await fetch(url); if (!res.ok) throw new Error("读取失败 " + res.status); return res.json();
}
function query(values) { return new URLSearchParams(values).toString(); }
function bubble(role, content, error = false) {
  $("welcome")?.remove();
  const item = document.createElement("article"); item.className = "message " + role + (error ? " error" : "");
  const label = document.createElement("div"); label.className = "who"; label.textContent = role === "user" ? "YOU / 你" : "小步 / AGENT";
  const body = document.createElement("div"); body.className = "content"; body.textContent = content;
  item.append(label, body); $("messages").append(item); $("messages").scrollTop = $("messages").scrollHeight;
}
function addTrace(event) {
  if (!["tool_start", "tool_result"].includes(event.type)) return;
  if (!$("trace").querySelector(".trace")) $("trace").replaceChildren();
  const d = document.createElement("details"); d.className = "trace";
  const summary = document.createElement("summary");
  summary.textContent = (event.type === "tool_start" ? "调用 " : event.result?.ok ? "完成 " : "失败 ") + event.name;
  const pre = document.createElement("pre"); pre.textContent = JSON.stringify(event.arguments || event.result, null, 2);
  d.append(summary, pre); $("trace").append(d);
}
async function refreshSide() {
  const current = user, version = viewVersion;
  const [sessions, todos] = await Promise.all([get("/api/sessions?" + query({user_id: current})), get("/api/todos?" + query({user_id: current}))]);
  if (version !== viewVersion || current !== user) return;
  $("sessions").replaceChildren();
  const rows = [...sessions.sessions];
  if (!rows.some(r => r.session_id === session)) rows.unshift({session_id:session, title:"新会话"});
  for (const row of rows) {
    const button = document.createElement("button"); button.className = "session" + (row.session_id === session ? " active" : "");
    button.textContent = row.title; button.title = row.title; button.disabled = busy;
    button.onclick = () => { session = row.session_id; saveSession(); loadHistory().catch(showError); };
    $("sessions").append(button);
  }
  $("todos").replaceChildren(); $("todo-count").textContent = todos.items.filter(t => !t.done).length;
  if (!todos.items.length) { const p = document.createElement("p"); p.className="muted"; p.textContent="还没有待办，在对话中添加一个。"; $("todos").append(p); }
  for (const item of todos.items) {
    const row = document.createElement("div"); row.className = "todo" + (item.done ? " done" : "");
    const box = document.createElement("span"); box.className="box"; box.textContent=item.done ? "☑" : "☐";
    const text = document.createElement("span"); text.className="text"; text.textContent="#" + item.id + " " + item.text;
    row.append(box,text); $("todos").append(row);
  }
}
async function loadHistory() {
  const version = ++viewVersion;
  const data = await get("/api/history?" + query({user_id:user,session_id:session}));
  if (version !== viewVersion) return;
  $("messages").replaceChildren(); $("trace").replaceChildren();
  for (const msg of data.messages) {
    if (msg.role === "user" || (msg.role === "assistant" && !msg.tool_calls && msg.content)) bubble(msg.role, msg.content);
  }
  if (!$("messages").children.length) $("messages").append(welcome.cloneNode(true));
  await refreshSide();
}
function setBusy(value) {
  busy=value; $("send").disabled=value || !configured; $("stop").hidden=!value;
  $("user").disabled=value; $("new-session").disabled=value;
  for (const b of $("sessions").querySelectorAll("button")) b.disabled=value;
}
function showError(error) { bubble("assistant", error.message || "连接失败，请重试。", true); }
$("user").onchange = async () => {
  user=$("user").value; localStorage.setItem("agent-user",user);
  // Clear old identity data before async requests finish, not just after refresh.
  ++viewVersion;
  for (const id of ["messages", "todos", "sessions", "trace"]) $(id).replaceChildren();
  $("todo-count").textContent="0";
  session=sessionStorage.getItem("agent-session-"+user) || crypto.randomUUID(); saveSession();
  await loadHistory().catch(showError);
};
$("new-session").onclick = () => { session=crypto.randomUUID(); saveSession(); loadHistory().catch(showError); };
$("messages").addEventListener("click", (event) => {
  const b=event.target.closest("[data-prompt]"); if (b && !busy) { $("prompt").value=b.dataset.prompt; $("prompt").focus(); }
});
$("prompt").addEventListener("keydown",(event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("composer").requestSubmit(); }
});
$("stop").onclick=() => controller?.abort();
$("composer").onsubmit=async(event) => {
  event.preventDefault(); const message=$("prompt").value.trim();
  if (!message || busy || !configured) return;
  const requestId=crypto.randomUUID(); controller=new AbortController(); setBusy(true);
  $("prompt").value=""; bubble("user",message); $("trace").replaceChildren(); $("run-status").textContent="正在提交…";
  let receivedFinal=false;
  try {
    const response=await fetch("/api/chat/stream",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({user_id:user,session_id:session,request_id:requestId,message}),signal:controller.signal});
    if (!response.ok) { const body=await response.json(); throw new Error(typeof body.detail === "string" ? body.detail : "请求失败 " + response.status); }
    const reader=response.body.getReader(), decoder=new TextDecoder(); let buffer="";
    while(true) {
      const {value,done}=await reader.read(); if(done) break;
      buffer+=decoder.decode(value,{stream:true}); const frames=buffer.split("\n\n"); buffer=frames.pop();
      for(const frame of frames) {
        const line=frame.split("\n").find(l=>l.startsWith("data: ")); if(!line) continue;
        const data=JSON.parse(line.slice(6)); addTrace(data);
        if(data.type === "queued") $("run-status").textContent="已入队，等待本会话…";
        if(data.type === "model_start") $("run-status").textContent="模型决策 · 第 " + data.step + " 步";
        if(data.type === "tool_start") $("run-status").textContent="执行工具 · " + data.name;
        if(data.type === "final") { receivedFinal=true; bubble("assistant",data.answer,data.status === "failed"); }
        if(data.type === "error") throw new Error(data.message);
      }
    }
    if (!receivedFinal) throw new Error("连接中断，尚未收到最终结果。请刷新会话核对，避免重复提交有副作用的任务。");
  } catch(error) {
    if(error.name === "AbortError") bubble("assistant","已停止等待并请求取消。已完成的工具修改不会自动撤销，请核对待办。");
    else showError(error);
  } finally {
    controller=null; setBusy(false); $("run-status").textContent="Enter 发送 · Shift + Enter 换行";
    await refreshSide().catch(showError); $("prompt").focus();
  }
};
(async() => {
  try {
    const h=await get("/api/health"); configured=h.configured;
    $("model-name").textContent=h.model; $("model-status").classList.toggle("ready",configured);
    $("config-notice").hidden=configured; setBusy(false); await loadHistory();
  } catch(error) { configured=false; setBusy(false); showError(error); }
})();
