"""测试共享 Fixture：默认使用独立 PostgreSQL 测试库，避免污染开发数据。"""

import os
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# 测试必须显式使用确定性的本地 LLM，并连到独立的 PostgreSQL 库，避免读写开发库。
os.environ["LLM_PROVIDER"] = "heuristic"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://memloci:memloci@127.0.0.1:5432/memloci_test",
)

from packages.common import models  # noqa: E402,F401
from packages.common.config import get_settings  # noqa: E402
from packages.common.db import Base  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session")
def test_engine() -> Generator[Engine]:
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        engine.dispose()
        pytest.exit(
            "pytest 需要本机 PostgreSQL（默认库 memloci_test）。"
            f"请确认可连接 {os.environ['DATABASE_URL']}。原始错误: {exc}",
            returncode=1,
        )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def db(test_engine: Engine) -> Generator[Session]:
    table_names = ", ".join(table.name for table in Base.metadata.sorted_tables)
    # TRUNCATE 比 drop/create 快，且 RESTART IDENTITY 保证各测试主键从 1 开始、互不污染。
    with test_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    session_factory = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
