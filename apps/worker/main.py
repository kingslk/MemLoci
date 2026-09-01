"""Dramatiq Worker 启动入口。

Worker 和 API 共用配置，但只有 Worker 必须建立 Redis Broker；把 Broker 配置集中在这里，
避免任务模块各自创建连接。
"""

from packages.common.config import get_settings


def configure_broker() -> None:
    """让 Dramatiq 使用配置中的 Redis，而不是默认 Broker。"""
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker
    from dramatiq.middleware import Middleware

    class ReclaimOnBoot(Middleware):
        def before_worker_boot(self, broker, worker) -> None:  # noqa: ANN001
            from threading import Thread

            from apps.worker.tasks import auto_dream_scheduler_loop
            from packages.common.db import SessionLocal
            from packages.common.jobs import reclaim_orphaned_jobs

            with SessionLocal() as db:
                reclaim_orphaned_jobs(db, interrupt_running=True)
            if get_settings().auto_dream_enabled:
                Thread(target=auto_dream_scheduler_loop, daemon=True).start()

    broker = RedisBroker(url=get_settings().redis_url)
    broker.add_middleware(ReclaimOnBoot())
    dramatiq.set_broker(broker)


def run() -> None:
    """通过 `dramatiq apps.worker.tasks` 启动实际 Worker。"""

    configure_broker()
    from dramatiq.cli import main

    main()


if __name__ == "__main__":
    run()
