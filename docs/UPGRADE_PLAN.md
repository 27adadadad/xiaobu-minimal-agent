# 全量补齐设计与验收

1. 进程协调：同一 SQLite 库同一 session 使用操作系统文件锁，进程退出自动释放。进入锁后仅恢复该 session 的中断 turn，禁止新 worker 全局恢复其他 worker。边界：同机本地磁盘，不支持 NFS/跨机。
2. 后台任务：SQLite 队列、租约/心跳、原子领取、attempt token 防迟到提交；注册 background 工具和状态查询 UI。重试保持单工具固定参数/回执，不盲目重放整段模型计划。
3. 向量检索：持久化文本向量与 cosine top-k；默认本地字符 n-gram 哈希向量（词面，不冒称深度语义），可配置真实 Embedding HTTP。历史按 user+session 隔离，显式偏好按 user 隔离并同步删除。
4. 摘要：完整 turn 分批压缩，真实 LLM 结构化摘要、来源序号校验、原始用户约束锚点保留；失败保留旧摘要。近期完整 turn 和证据回查并存。
5. 验收：先红灯，再单元/HTTP测试、跨进程互斥/强杀/任务接管；真实 LLM 摘要与流式；天气候选/预报与手机 UI。更新四角色及 README，发布 GitHub。
