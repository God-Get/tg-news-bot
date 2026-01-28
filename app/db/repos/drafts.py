from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.draft import Draft


class DraftsRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, draft_id: int) -> Draft | None:
        q = await self.session.execute(select(Draft).where(Draft.id == draft_id).limit(1))
        return q.scalar_one_or_none()

    async def get_by_normalized_url(self, normalized_url: str) -> Draft | None:
        q = await self.session.execute(select(Draft).where(Draft.normalized_url == normalized_url).limit(1))
        return q.scalar_one_or_none()

    async def get_by_card(self, group_chat_id: int, message_id: int) -> Draft | None:
        q = await self.session.execute(
            select(Draft).where(and_(Draft.group_chat_id == group_chat_id, Draft.message_id == message_id)).limit(1)
        )
        return q.scalar_one_or_none()

    async def create(self, **fields) -> Draft:
        d = Draft(**fields)
        self.session.add(d)
        await self.session.commit()
        await self.session.refresh(d)
        return d

    async def update(self, draft_id: int, **fields) -> Draft:
        fields["updated_at"] = datetime.utcnow()
        await self.session.execute(update(Draft).where(Draft.id == draft_id).values(**fields))
        await self.session.commit()
        d = await self.get(draft_id)
        assert d is not None
        return d

    async def set_state(self, draft_id: int, state: str, topic_id: int | None = None) -> Draft:
        payload = {"state": state, "updated_at": datetime.utcnow()}
        if topic_id is not None:
            payload["topic_id"] = topic_id
        await self.session.execute(update(Draft).where(Draft.id == draft_id).values(**payload))
        await self.session.commit()
        d = await self.get(draft_id)
        assert d is not None
        return d

    async def list_due_scheduled_ids(self, limit: int = 20) -> list[int]:
        now = datetime.utcnow()
        q = (
            select(Draft.id)
            .where(
                and_(
                    Draft.state == "SCHEDULED",
                    Draft.scheduled_at.is_not(None),
                    Draft.scheduled_at <= now,
                )
            )
            .order_by(Draft.scheduled_at.asc())
            .limit(limit)
        )
        res = await self.session.execute(q)
        return [int(x) for x in res.scalars().all()]
