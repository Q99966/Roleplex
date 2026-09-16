"""预算模式迁移保留旧值，降级不擅自改写不限配置。"""
from datetime import datetime, timezone
import sqlalchemy as sa
from scripts.check_migrations import run_alembic
from test_workspace_commands import command_root


def test_unlimited_migration_preserves_old_settings_and_rejects_lossy_downgrade(command_root):
    """Args:
        command_root：本轮独立工作区目录，迁移库不与业务测试共享。
    """
    url=f"sqlite:///{(command_root / 'migration.db').as_posix()}"
    migration_url=url.replace("sqlite:", "sqlite+aiosqlite:")
    result=run_alembic(migration_url,'upgrade','0016_usage_history_index')
    assert result.returncode==0
    engine=sa.create_engine(url)
    metadata=sa.MetaData()
    table=sa.Table('instance_settings',metadata,autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(table.insert().values(id=1,created_at=datetime.now(timezone.utc),
            decision_limit=128,budget_revision=7))
    engine.dispose()
    assert run_alembic(migration_url,'upgrade','head').returncode==0
    engine=sa.create_engine(url)
    with engine.begin() as connection:
        row=connection.execute(sa.select(table.c.decision_limit,table.c.budget_revision)).one()
        assert tuple(row)==(128,7)
        connection.execute(table.update().values(decision_limit=None))
    engine.dispose()
    result=run_alembic(migration_url,'downgrade','0016_usage_history_index')
    assert result.returncode!=0
    assert 'DECISION_DOWNGRADE_REQUIRES_LEGACY_VALUES' in result.stderr
    engine=sa.create_engine(url)
    with engine.begin() as connection:
        assert connection.scalar(sa.select(table.c.decision_limit)) is None
        connection.execute(table.update().values(decision_limit=512))
    engine.dispose()
    assert run_alembic(migration_url,'downgrade','0016_usage_history_index').returncode!=0
    engine=sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(table.update().values(decision_limit=128))
    engine.dispose()
    assert run_alembic(migration_url,'downgrade','0016_usage_history_index').returncode==0
