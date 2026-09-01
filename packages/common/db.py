"""数据库连接和 Session 生命周期。

API、Worker 和 MCP 都使用短生命周期 Session；业务层负责提交事务，本模块负责连接池和释放连接。
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from packages.common.config import get_settings


class Base(DeclarativeBase):
    """所有持久化模型的基类。"""


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """显式的本地一次性建表工具；API/MCP 启动不会绕过 Alembic。"""

    from packages.common import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session]:
    """FastAPI 依赖：请求结束后保证释放连接。"""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
