# 小步 · Hand-built Agent

不依赖 Agent 框架，自行实现模型决策循环、工具注册、会话、记忆与运行控制。

**2026-10-22 全量补齐**：真实 token streaming、持久化后台任务、向量检索、LLM 语义摘要、同机多进程故障恢复、未来天气预报、同名城市澄清和手机待办面板。

验收：**54 项离线测试通过，4 项真实调用测试通过**。离线默认跳过4项真实测试，另有1条 Starlette/httpx 弃用提示。两个 Web worker 接收8个同session请求，历史保持16条正确配对消息；进程强杀和任务接管有独立测试。[验收与边界](docs/FULL_UPGRADE_ACCEPTANCE.md)

## 安装运行

Python 3.11+，推荐3.13，从仓库根目录运行：

```powershell
git clone https://github.com/27adadadad/xiaobu-minimal-agent.git
cd xiaobu-minimal-agent
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-lock.txt
.\.venv\Scripts\python -m pip install -e . --no-deps
Copy-Item .env.example .env
```

本地编辑 `.env` 的 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL。基础地址不要带 /chat/completions。模型须支持原生工具调用和 Chat Completions SSE。不猜测可用型号，不把密钥放进前端、聊天或Git。

```powershell
# 单进程UI，内置后台任务worker
.\.venv\Scripts\python -m minagent
# 同机两个Web worker，共享同一AGENT_DB
.\.venv\Scripts\python -m minagent --workers 2
# 只执行后台任务，不开网页，可独立重启
.\.venv\Scripts\python -m minagent --worker-only
```

打开 http://127.0.0.1:8000。换端口用 --port 8001。macOS/Linux对应 .venv/bin/python 和 cp .env.example .env。

## 演示输入

- 你好，解释什么是Agent：普通问题直接回答。
- 计算 (128+256)*3/8：模型自主调用calculator得到144，答案增量显示。
- 查上海未来三天天气：返回当地日期预报；有同名候选先澄清。
- 添加待办：检查商品主图；列出待办；把编号1标记完成。
- 请长期记住我住上海；新会话问常住城市。同用户偏好共享，历史不串窗口。
- 请用background在后台计算128*256，只提交任务：先返回任务编号，任务栏随后通知。
- 两分钟后在后台添加待办：准备面试：延迟单工具任务，重启后队列仍在。

正式入口不加载假模型，未配置就报错。截图中的 OFFLINE FIXTURE 仅为浏览器测试夹具。

## 架构与工具

输入 → 进程内门 + OS session锁 → 近期完整turn/语义摘要/向量证据 → 真实LLM → 解析 → 注册工具 → 按tool_call_id回填 → 再次决策或返回。background把单工具任务写入SQLite，由worker执行并持久化通知。

| 文件 | 职责 |
|---|---|
| runtime.py | 手写循环、幂等、预算、取消 |
| llm.py / protocol.py | 真实HTTP/SSE、工具参数分片合并、输出解析 |
| tools.py | 六个工具、本地JSON Schema校验 |
| jobs.py | 持久队列、原子领取、租约心跳、接管、通知 |
| coordination.py | 同机跨进程session锁，进程退出自动释放 |
| vectors.py | 持久向量、模型版本隔离、cosine top-k、HTTP Embedding |
| context.py | 真实LLM摘要、来源校验、原文约束锚点、证据回查 |
| store.py | 历史、偏好、待办、事务性回执 |
| web.py / web/ | 本机UI、流式输出、任务查看/取消/已读 |

工具：calculator（安全AST算术）、search（**固定语料MOCK**）、todo、weather、memory、background。模型根据实际Schema自主决策，不是关键词路由。

## 真实流式输出

请求stream=true，解析delta.content推送前端，不是完整答案的假打字。工具参数按index合并，校验后执行。分片think标签与reasoning_content不展示。没有DONE的断流报告失败；正文已发出后不重试追加。适用于非思考模式工具合约，不支持必须回传供应商私有推理字段的模式。

## Memory召回与放置

**显式偏好**：仅明确授权后remember；同key更新，forget删除结构化偏好。每次模型决策前取当前用户最近8条，放独立USER_MEMORY参考消息，同轮修改下一步刷新。

**历史向量检索**：超过12个turn或24,000个序列化字符后，旧turn同步到SQLite索引。按user_id + session_id + embedding_model检索top-3，保留来源ID，不混入其他窗口。源删除同步到索引；维度不匹配拒绝。

默认512维本地字符n-gram哈希向量，是确定性**词面相似检索，不冒称深度语义Embedding**。真实语义模型需本地配置：

```dotenv
EMBEDDING_MODEL=你有权限的嵌入模型
EMBEDDING_BASE_URL=支持embeddings的服务基础地址
EMBEDDING_API_KEY=本地填写
```

未配置模型用本地向量；配置后调用失败会产生retrieval_failed事件，不伪称语义召回成功。HTTP适配器有契约测试，本次未配置外部Embedding做真实验收。

**语义摘要**：达到阈值，保留最近6个完整turn，对旧turn分批调用真实LLM生成objective、constraints、facts、decisions、open_items、references。引用序号必须属于本批；含必须/不要/禁止/目标/不能的原始输入作为锚点保留。验证通过才原子替换，失败保留旧摘要并发context_notice。源批次约32,000字符、摘要上限12,000、组装约36,000字符，不是精确token数。

摘要和HISTORY_EVIDENCE是独立不可信参考，后接近期完整原文，不切断工具调用/结果。摘要可能丢细节，配合原文回查。forget不是全库合规删除，旧聊天可能仍含相关事实，生产需完整删除传播。

## 后台任务与恢复

持久化的是**一个注册工具与固定参数**，不是任意代码或无限模型计划。允许calculator/search/weather/todo/memory，不允许background递归提交。

- queued→running→succeeded/failed；支持取消及最多7天一次性延后。
- SQLite事务原子领取，默认30秒租约、10秒心跳；进程消失后租约到期可接管，最多3次领取。
- 结果带attempt token，旧执行者不能覆盖新结果。完成/失败/取消通知持久化，网页重连后可见并可标已读。
- todo/memory用固定job-id回执，修改与回执同事务；强杀接管测试验证仅1条待办。
- 取消不撤销已提交操作。外部只读请求可能重复，不承诺任意系统exactly-once，不自动重放整段模型计划。

## 多进程容错边界

同session先取进程内门，再取数据库对应OS文件锁；活进程持锁时其他worker等待，强杀后内核释放。拿锁后只恢复本session遗留running turn，新worker不误伤其他worker。

限**同机、本地文件系统、同一数据库**，不是跨机HA，不对网络盘文件锁作可靠承诺。容量8是每worker上限，最多8个worker；本进程每门最多16请求，锁等待最多240秒。HTTP断开后重连查看状态；机器损坏仍需独立备份。

## 测试与提交材料

```powershell
.\.venv\Scripts\python -m pytest -q
# 真实调用会产生费用，需先配置本地.env
$env:RUN_LIVE="1"
.\.venv\Scripts\python -m pytest tests/test_live.py tests/test_live_upgrade.py -q -s
Remove-Item Env:RUN_LIVE
```

Windows临时目录异常可先创建artifacts，再用新的 --basetemp=artifacts/pytest-UUID；pytest会清理basetemp，不能指向源码。

- [全量补齐验收](docs/FULL_UPGRADE_ACCEPTANCE.md)
- [AI Prompt与真实记录](docs/AI_PROMPTS_AND_DECISIONS.md)
- [五模块架构题](docs/ARCHITECTURE_ANSWERS.md)
- [升级计划](docs/UPGRADE_PLAN.md)
- [patch、literal输出与回滚](docs/verification/)

## 安全边界

用户A/B是本机隔离演示，**不是登录认证**，访问者能指定身份，不得公网部署。已有Host/Origin/CSP和安全文本渲染，不等于生产认证、隐私删除、审计与权限治理完成。

天气支持当前和最多16天预报，同名城市需确认location_id，预报有不确定性。手机待办/任务栏在对话下方。search仍为mock，后台是单工具队列，不是通用调度平台。

参考：[OpenAI工具协议](https://developers.openai.com/api/docs/guides/function-calling)、[SQLite事务](https://www.sqlite.org/lang_transaction.html)、[Open-Meteo预报](https://open-meteo.com/en/docs)。
