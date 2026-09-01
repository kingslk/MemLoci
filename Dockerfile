# 只提供 MemLoci 依赖的 PostgreSQL 和 Redis。
# API、Worker、MCP 和 Web 在宿主机运行，通过 localhost 连接这两个端口。
FROM postgres:16-bookworm

USER root

# 命名卷根目录可能带 lost+found；用子目录作 PGDATA，避免 initdb 拒绝初始化。
ENV PGDATA=/var/lib/postgresql/data/pgdata

# PostgreSQL 使用官方镜像的初始化和启动逻辑；Redis 在同一容器内后台运行。
RUN apt-get update \
    && apt-get install -y --no-install-recommends redis-server \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 5432 6379

# Redis 监听所有容器网卡，才能被宿主机端口映射访问。
# PostgreSQL 继续交给官方 entrypoint，保留 POSTGRES_USER/PASSWORD/DB 初始化能力。
CMD ["sh", "-c", "redis-server --daemonize yes --bind 0.0.0.0 --protected-mode no && exec docker-entrypoint.sh postgres"]
