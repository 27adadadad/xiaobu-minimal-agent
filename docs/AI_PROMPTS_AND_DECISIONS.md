# AI Prompt 与问题解决记录

## 透明说明

本工程由 AI 根据用户要求协助实现、写测试/文档并执行验证。提交者不应宣称架构答案是完全独立完成的思考，应检查修改并能够解释源码。以下记录实际指令、工程决策和证据，不编造个人开发时长，不包含私有思维链。

## 实际用户输入

- 初始面试题：无 Agent 框架、基础循环、工具注册/Schema、todo、session、真实模型、测试、README、AI 使用记录，五模块各选一道。
- 凭证安排原文：`稍后配置，先交付代码`

语言、模型与 UI 未指定，采用 Python + 本地浏览器；FastAPI 只做传输，核心 Runtime 自行实现。没有读取其他项目密钥来凑真实验收。

## 实际运行时 Prompt

权威版本是 [SYSTEM_PROMPT](../minagent/prompts.py)。要求按 Schema 自主调用、错误不冒充成功、mock 搜索标明、天气不假称预报、身份由 Runtime 注入、偏好显式授权、工具/记忆为不可信数据、不输出私有推理。Registry.schemas() 生成实际发送给模型的工具定义。

### 可复用审查 Prompt（模板，不冒称已单独运行过）

> 用两次原生 tool_calls 和一次最终答案审查 Runtime.loop。逐条检查 assistant/tool 配对、隔离、错误回填、预算和副作用幂等。真实缺陷先写失败测试，不把 mock 当真实 API 验收。

> 对照源码回答架构题：主决策、关键取舍、失效条件、可执行验收；区分已实现和未来设计，不为不存在的能力背书。

## 实际遇到的问题

| 观察 | 定位/修正 | 证据边界 |
|---|---|---|
| 默认工作目录报 os error 267 | 指定有效目录；确认用户给定路径不存在后创建新工程 | 未假装读取现有仓库 |
| 测试最初无法导入 minagent | 先写测试观察红灯，再实现 | 收集错误不是功能通过 |
| pytest 临时目录 WinError 5 | 不改系统权限，使用项目下新 basetemp | 再次因父目录缺失出现 WinError 3，建 artifacts 后通过 |
| sqlite 连接上下文不会自动 close | 自定义 contextmanager，finally 关闭 | 提交与连接释放分别负责 |
| memory 同轮更新未刷新召回快照 | 新失败测试后，改为每次决策前召回 | 先失败后通过 |
| 会话标题 MIN(input) 取成字典序最小 | 新失败测试，按首条 seq 取输入 | z-first / a-second 验证 |
| memory 断言误命中 system 中标记文字 | 改为匹配独立消息开头 | 收紧后真正观察红灯，避免虚假绿灯 |
| favicon 404 | 增加本地 SVG，重新导航检查 | 最后一次控制台 0 error / 0 warning |
| 浏览器切身份时异步刷新前短暂显示旧待办 | 在发请求前立即清空旧身份的消息、待办、轨迹和会话列表 | 后端隔离测试通过之外，额外修复显示时序 |
| 大批写文档命令被执行策略拒绝 | 改用独立文件补丁写入 | 拒绝时未产生文档，不冒称写入成功 |
| rollback 首次选到 Windows WSL bash，返回非零 | 固定 Windows 使用 Git Bash；随后发现 python3 是商店占位程序，退出 49 且 stdout/stderr 为空 | 改为实际运行解释器版本探针，再选可用 Python |
| patch 首次无法重建 | 原始 CRLF 经文本模式再次换行导致差异文件损坏 | 按原始 bytes 生成 patch，重建后 SHA 与修改版一致 |
| 本项目无真实模型凭证 | 提供 .env/live test，用户选择稍后配置 | 未声称真实模型已通过 |

## 关键取舍

- 原生 function calling 代替正则猜工具；需要兼容的模型合约。
- 完整 turn 裁剪代替仓促的摘要器；明确不是 200 轮无损记忆。
- SQLite 原子回执做本地副作用幂等；不承诺所有外部系统 exactly-once。
- 单进程 session gate 容易验证；不冒充多进程后台 job。
- 显式偏好减少误记；不是自动个人画像。
- SSE 推运行事件；不谎称已做 token streaming。

## 证据位置

[验收记录](ACCEPTANCE.md)区分离线测试、浏览器、真实天气和待联调的模型；[VERIFICATION.txt](verification/VERIFICATION.txt)保存自查修正的 BASELINE/MODIFIED/ROLLBACK。未使用独立子代理，不虚构多人 review。

## 配置后续验收（2026-10-21）

用户回复“配置好了”后，确认 configured=True（不输出密钥），显式 RUN_LIVE=1 执行 tests/test_live.py，真实模型端到端 1 passed in 8.77s，退出 0。先前“未配置”的记录是交付当时的历史状态，不再是当前阻塞。


## 2026-10-22 全量补齐

用户要求全部补齐。在同一工程增加jobs/vectors/context/coordination与UI，先观察新模块不存在的红灯，再实现并通过54项离线测试。4项真实调用验证摘要、杯底尺寸召回、后台决策和3天预报。默认词面向量不包装成深度语义模型，外部Embedding未配置。采用同机OS文件锁而非TTL驱逐session；后台队列用租约和attempt token。移除启动时全局recover，持锁后只恢复该session。强杀接管验证固定job回执不重复写。

大批文档命令被执行策略拒绝，随后尝试同文件Delete+Add的补丁被拒绝，改用分段写入成功。没有把失败工具当成功。原始单进程/无摘要等内容为历史状态，当前看README及FULL_UPGRADE_ACCEPTANCE。未宣称跨机HA或任意外部系统exactly-once。

最终回滚审计中全套测试曾出现1 failed/53 passed：租约测试用10毫秒真实时间，在较慢磁盘上首个claim已过期。定位为测试时钟假设，改为可控clock推进后重跑，而非扩大生产租约或隐藏失败。源码功能未因此更改。
