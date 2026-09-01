# MemLoci 备份与恢复

## PostgreSQL

PostgreSQL 是 Project、Evidence、Memory、DreamRun 和 Workflow State 的事实源。生产环境使用定期 `pg_dump`，不要把 Secret 放进命令行历史：

```bash
pg_dump --format=custom --file="$BACKUP_DIR/memloci-$(date +%Y%m%d%H%M).dump" "$DATABASE_URL"
pg_restore --clean --if-exists --dbname="$DATABASE_URL" "$BACKUP_FILE"
uv run alembic upgrade head
```

恢复后检查：

```bash
curl "$MEMLOCI_API_URL/health/ready"
uv run alembic current
```

## Mirror 与 Redis

- `MIRROR_ROOT` 是分析缓存，不是事实源；丢失后按 Repository 配置重新执行 `sync` 即可重建。
- Redis 只保存 Dramatiq 消息，不参与业务恢复；恢复后可从 PostgreSQL 中的 `retryable`/未处理状态重新投递任务。
- `jobs.checkpoint` 必须随 PostgreSQL 一起恢复，初始化从最近完成 Pass 继续。

## 安全边界

备份文件必须由部署平台加密并限制访问。`GITLAB_TOKEN`、`GITLAB_WEBHOOK_SECRET`、`ADMIN_TOKEN`、`MCP_TOKEN` 和 LLM Key 不进入数据库备份、Mirror remote URL、日志或前端构建产物。
