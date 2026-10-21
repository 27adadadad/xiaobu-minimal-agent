# 实际验收记录

日期：2026-10-21。环境：Windows、Python 3.13.12；确切依赖见 requirements-lock.txt。

## 已观察结果

| 检查 | 实际结果 |
|---|---|
| 离线单元/HTTP/SSE 测试 | 36 passed, 1 skipped, 1 warning；退出 0 |
| 全新虚拟环境安装 | 按 requirements-lock.txt 安装，再次 36 passed, 1 skipped, 1 warning；退出 0 |
| 真 LLM 测试 | 用户本地配置后显式执行：1 passed in 8.77s，退出 0；使用真实 OpenAI-compatible HTTP，不使用模拟模型 |
| JS 语法 | node --check web/app.js，退出 0 |
| 浏览器生产入口 | 未配置提示可见，发送按钮禁用，不伪造模型回复 |
| 浏览器离线夹具 | 真实 Runtime + 测试模型，calculator 返回 144；工具轨迹/最终答案可见，发送按钮恢复 |
| 浏览器身份切换 | A 添加离线待办后切 B：B 待办为空，聊天不含 A 待办；切换前先清空旧显示 |
| 1440×960 桌面 | 没有水平溢出，见 ui-desktop.png |
| 390×844 手机 | scrollWidth=390，composer 底部约 809.6 < 844，发送按钮可见，见 ui-mobile.png |
| 浏览器控制台 | 最后一次重新导航后 0 error、0 warning |
| 真实天气服务 | Open-Meteo 返回上海、中国、Asia/Shanghai；2026-10-21T18:15，17.6°C，降水 0.0 mm |

天气是本次观测样本，不是长期承诺；它证明天气 HTTP 工具可用，它与下方独立执行的真实 LLM 测试是不同证据。移动截图带 OFFLINE UI FIXTURE 标识，不冒充真实模型。

唯一离线测试 warning 来自当前 Starlette TestClient 对 httpx 的弃用提示，不是失败；没有隐藏 warning。

## 自查修正的可回滚验证

改变的是 `Runtime.loop` 的 memory 刷新位置、`Store.sessions` 的标题查询。

- BASELINE：同轮新记忆没有独立召回消息；标题为 a-second。两项回归失败，退出 1。
- MODIFIED：每次模型决策重新召回；标题为首条 z-first。两项通过，退出 0。
- ROLLBACK：在单独副本执行 ROLLBACK.sh，恢复上述原始行为；两项失败，退出 1，恢复 hash 与原始一致。
- CHANGE.patch 可从原始副本重建修改版，两个源码文件 SHA-256 都匹配。正式源码保留修正，不回退已交付功能。

四个角色的绝对路径、命令、输入、literal stdout/stderr、退出码和 hash 均在 [VERIFICATION.txt](verification/VERIFICATION.txt)。对应文件是 minagent/runtime.py（修改版）、CHANGE.patch、VERIFICATION.txt、ROLLBACK.sh；store.py 是补充修改文件。所有文件已重新打开读取。

仅在**可丢弃工程副本**上测试 rollback，它会恢复自查阶段的已知缺陷，不应在日常运行目录执行。脚本不触碰 .env、数据库和用户数据。安装依赖后可运行 `python scripts/verify_delivery.py` 复现整个对照。

## 未验收或未实现

- 真实模型端到端已通过当前配置的 tests/test_live.py；真实模型天气→待办组合、所有供应商及更多异常输入尚未覆盖。
- 没有生产认证/多机部署/持久化后台任务；不做生产可用保证。
- 真实 LLM 的每种提示都不保证必然按同一顺序调用；以 live test 实际轨迹为准。
- GitHub CI 与本机测试是不同证据，发布后以仓库 Actions 当次结果为准。

## 提交完整性

源码、测试、README、运行系统 Prompt、AI 使用记录、五题架构答案、UI 截图、依赖锁、离线 CI 和回滚证据均在仓库。未提交 .env、密钥、运行数据库或个人聊天。

## 配置后追加验收

真实测试结果：`1 passed in 8.77s`，退出 0。检查 calculator 实际工具结果为 144、todo 真实持久化、memory 存储上海、新 session 回答上海、用户 B 无 A 的待办/结构化记忆，以及新 session 不混入旧聊天。断言源码见 tests/test_live.py。没有读取或打印凭证；HTTP 响应来自实际配置的服务，未注入测试 transport。原离线的 1 skipped 是未显式开启 RUN_LIVE 时的预期行为，不代表此追加验收跳过。
