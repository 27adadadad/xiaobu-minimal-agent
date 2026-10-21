# 实施与验收记录

目标：Python 手写 Agent Runtime + 本地浏览器 UI，不引入 Agent 框架。

1. 先写循环、工具、隔离、解析和 HTTP 测试并运行红灯。
2. 实现协议解析、OpenAI-compatible HTTP 适配器、SQLite 存储和工具注册。
3. 实现单 session 串行、跨 session 并行、超时、步数预算和请求幂等。
4. 实现本地 UI，展示公开工具事件，不展示私有推理。
5. 运行离线测试、UI 检查；真实模型仅使用本项目显式配置的凭证。
6. 写 README、真实 AI 使用记录、五模块各一题，审查公开仓库内容再发布。

选择：使用原生 function calling 而非关键词路由；SQLite 而非内存字典；本地单进程而非提前引入消息中间件。天气采用真实 Open-Meteo HTTP（无密钥），search 明确 mock。
