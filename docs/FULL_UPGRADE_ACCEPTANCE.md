# 2026-10-22 全量补齐验收

54项离线通过、4项真实调用通过。离线默认跳过4项真实测试，保留1条Starlette/httpx弃用提示。

| 能力 | 实现 | 实际验收 |
|---|---|---|
| token streaming | stream=true、delta到前端、参数合并、分片think过滤 | 真LLM端到端；分片/参数/断流回归 |
| 持久异步任务 | SQLite、租约心跳、固定回执、取消/通知 | 真模型提交后台计算144；强杀已写待办未提交结果的进程，接管后仅1条待办 |
| 向量检索 | 持久向量、cosine top-k、版本/租户隔离 | 重开召回、用户/session隔离、源删除；HTTP Embedding契约测试 |
| 语义摘要 | 真实LLM、字段/来源校验、原始约束锚点 | 16轮真实压缩，后续正确回答杯底尺寸；非法摘要保留旧版本 |
| 多进程容错 | OS session锁、worker-safe恢复 | 持锁阻塞其他进程；强杀后接管；双Web worker并发8请求 |
| 天气预报/澄清 | daily、城市ID/国家筛选 | 多候选不发预报请求；指定ID返回正确国家；真实上海3天预报 |
| 手机界面 | 对话下方展示待办/后台任务 | 390×844实测，侧栏display=block，无水平溢出 |

默认向量是本地字符n-gram词面向量，不是预训练语义向量；语义Embedding适配器可配置，本次未配置外部模型，不把契约测试当真实服务验收。

## Literal结果

离线：`54 passed, 4 skipped, 1 warning in 6.43s`，退出0。

真实调用：`4 passed in 20.05s`，退出0。显式RUN_LIVE=1，tests/test_live.py及tests/test_live_upgrade.py。覆盖真实模型工具决策、摘要/召回、后台决策与真实天气。不输出密钥。

双进程Web：8请求命中PID46644和58052，同session最终16条消息user/assistant成对。夹具标OFFLINE MULTIPROCESS FIXTURE，不冒充真实模型。

浏览器任务：calculator从queued到succeeded，result=32768，显示“1条新结果”。手机面板可见；控制台0 error/0 warning；ui-mobile-full.png已打开检查。

## 边界

同机文件锁不是分布式锁；机器故障不在此范围。恢复session状态/单工具任务，不无损恢复HTTP连接。取消不能撤销已提交操作。外部请求允许至少一次，本地事务性todo/memory有固定回执去重。

摘要来源校验不能保证模型永不幻觉；保留原文回查，错误保留旧摘要。四角色沿用minagent/runtime.py、CHANGE.patch、VERIFICATION.txt、ROLLBACK.sh；BASELINE两项失败、MODIFIED两项通过、ROLLBACK两项失败，恢复hash一致。新模块及升级差异为补充证据。
