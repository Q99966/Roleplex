"""只为命令 E2E 临时数据库播种 Guest 成员，不给产品新增入群旁路。"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


async def seed(database: Path, conversation_id: int) -> None:
    """在精确的 fake E2E World 内幂等播种 Guest 与成员关系。

    Args:
        database：项目 data 下命令测试本轮 default 数据库。
        conversation_id：本轮由浏览器建立的测试会话。
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models import Conversation, ConversationMember, User
    from app.security import hash_password
    if database != database.resolve() or database.parent.name != 'default' or database.name != 'roleplex.db':
        raise ValueError('Invalid test database')
    if database.parent.parent.parent != BACKEND.parent / 'data':
        raise ValueError('Invalid test database scope')
    match = re.fullmatch(r'roleplex-command-e2e-(\d{14})', database.parent.parent.name)
    if not match or not database.is_file():
        raise ValueError('Missing isolated test database')
    engine = create_async_engine(f'sqlite+aiosqlite:///{database}', hide_parameters=True)
    try:
        async with async_sessionmaker(engine)() as session:
            if await session.get(Conversation, conversation_id) is None:
                raise ValueError('Missing test conversation')
            username = f'test{match[1]}_toolviewer'
            user = await session.scalar(select(User).where(User.username == username))
            now = datetime.now(timezone.utc)
            if user is None:
                user = User(username=username, nickname='工具查看 Guest', password_hash=hash_password('Roleplex-Test-1234'),
                            is_owner=False, token_version=0, created_at=now)
                session.add(user)
                await session.flush()
            if user.is_owner:
                raise ValueError('Unexpected test owner')
            member = await session.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation_id,
                ConversationMember.member_type == 'user', ConversationMember.member_id == user.id))
            if member is None:
                session.add(ConversationMember(conversation_id=conversation_id, member_type='user', member_id=user.id, joined_at=now))
            await session.commit()
    finally:
        await engine.dispose()


if __name__ == '__main__':
    try:
        asyncio.run(seed(Path(sys.argv[1]), int(sys.argv[2])))
    except Exception:
        raise SystemExit('Guest test fixture initialization failed') from None
