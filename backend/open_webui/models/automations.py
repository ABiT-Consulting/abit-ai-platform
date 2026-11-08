import logging
import time
from typing import Literal, Optional
from uuid import uuid4

from open_webui.internal.db import Base, get_async_db_context
from open_webui.utils.misc import json_text_variants
from pydantic import BaseModel, ConfigDict, Field, Field
from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, Index, String, Text, and_, case, cast, delete, func, or_, select, update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import column_property

log = logging.getLogger(__name__)


####################
# Automation DB Schema
####################


class Automation(Base):
    __tablename__ = 'automation'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    folder_id = Column(Text, nullable=True)
    name = Column(Text, nullable=False)
    data = Column(JSON, nullable=False)  # {prompt, model_id, rrule}
    meta = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_run_at = Column(BigInteger, nullable=True)
    next_run_at = Column(BigInteger, nullable=True)
    attempt_limit = Column(BigInteger, nullable=False, server_default='0')
    attempts_used = Column(BigInteger, nullable=False, server_default='0')

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_automation_next_run', 'next_run_at'),
        Index('ix_automation_user_folder', 'user_id', 'folder_id'),
    )


class AutomationRun(Base):
    __tablename__ = 'automation_run'

    id = Column(Text, primary_key=True)
    automation_id = Column(Text, nullable=False)
    chat_id = Column(Text, nullable=True)
    status = Column(Text, nullable=False)  # running | success | error
    lease_until = Column(BigInteger, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_automation_run_automation_id', 'automation_id'),
        Index('ix_automation_run_aid_created', 'automation_id', 'created_at'),
        Index('ix_automation_run_lease', 'lease_until'),
    )


# Exhaustion stops claims immediately. The status becomes Stale after all
# previously claimed runs reach a terminal outcome.
Automation.is_stale = column_property(
    (Automation.attempt_limit > 0)
    & (Automation.attempts_used >= Automation.attempt_limit)
    & ~and_(
        Automation.meta['erp_task'].as_string().is_not(None),
        or_(Automation.meta['erp_managed'].as_boolean().is_(True),
            and_(Automation.id.startswith('erp-'), Automation.meta['erp_managed'].as_boolean().is_not(False))),
    )
    & ~select(AutomationRun.id)
    .where(AutomationRun.automation_id == Automation.id, AutomationRun.status == 'running')
    .correlate_except(AutomationRun)
    .exists()
)

SCHEDULED_RUN_LEASE_NS = 90 * 1_000_000_000


####################
# Pydantic Models
####################


class AutomationTerminalConfig(BaseModel):
    server_id: str
    cwd: Optional[str] = None


class AutomationTarget(BaseModel):
    type: Literal['chat', 'channel'] = 'chat'
    channel_id: Optional[str] = None
    # Legacy automations remain in new-chat mode unless explicitly changed.
    chat_mode: Literal['same', 'new'] = 'new'


class AutomationAttachment(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


class AutomationData(BaseModel):
    prompt: str
    model_id: str
    fallback_model_id: Optional[str] = None
    rrule: str
    terminal: Optional[AutomationTerminalConfig] = None
    target: Optional[AutomationTarget] = None
    attachments: list[AutomationAttachment] = Field(default_factory=list, max_length=10)


class AutomationModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    folder_id: Optional[str] = None
    name: str
    data: dict
    meta: Optional[dict] = None
    is_active: bool
    last_run_at: Optional[int] = None
    next_run_at: Optional[int] = None
    attempt_limit: int = 0
    attempts_used: int = 0
    is_stale: bool = False

    @property
    def attempts_remaining(self) -> Optional[int]:
        return max(0, self.attempt_limit - self.attempts_used) if self.attempt_limit else None

    created_at: int
    updated_at: int


class AutomationRunModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    automation_id: str
    chat_id: Optional[str] = None
    status: str
    error: Optional[str] = None
    created_at: int
    attempt_number: Optional[int] = None
    cycle: Optional[int] = None


class AutomationForm(BaseModel):
    name: str
    folder_id: Optional[str] = None
    data: AutomationData
    meta: Optional[dict] = None
    is_active: Optional[bool] = True
    attempt_limit: int = Field(default=0, strict=True, ge=0, le=9_007_199_254_740_991)


class AutomationResponse(AutomationModel):
    last_run: Optional[AutomationRunModel] = None
    next_runs: Optional[list[int]] = None


class AutomationListResponse(BaseModel):
    items: list[AutomationModel]
    total: int


####################
# AutomationTable
####################


class AutomationTable:
    async def insert(
        self,
        user_id: str,
        form: AutomationForm,
        next_run_at: int,
        db: Optional[AsyncSession] = None,
    ) -> AutomationModel:
        async with get_async_db_context(db) as db:
            now = int(time.time_ns())
            row = Automation(
                id=str(uuid4()),
                user_id=user_id,
                folder_id=form.folder_id,
                name=form.name,
                data=form.data.model_dump(),
                meta=form.meta,
                is_active=form.is_active is not False,
                next_run_at=next_run_at if form.is_active is not False else None,
                attempt_limit=form.attempt_limit,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return AutomationModel.model_validate(row)

    async def count_by_user(self, user_id: str, db: Optional[AsyncSession] = None) -> int:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(func.count()).select_from(Automation).filter_by(user_id=user_id))
            return result.scalar()

    async def get_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[AutomationModel]:
        async with get_async_db_context(db) as db:
            row = await db.get(Automation, id)
            return AutomationModel.model_validate(row) if row else None

    async def get_active_by_user(self, user_id: str, db: Optional[AsyncSession] = None) -> list[AutomationModel]:
        """Get active automations for a user (for calendar RRULE expansion)."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Automation)
                .filter_by(user_id=user_id, is_active=True)
                .where(Automation.next_run_at.is_not(None))
                .order_by(Automation.created_at.desc())
            )
            return [AutomationModel.model_validate(r) for r in result.scalars().all()]

    async def search_automations(
        self,
        user_id: str,
        query: Optional[str] = None,
        status: Optional[str] = None,
        folder_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 30,
        db: Optional[AsyncSession] = None,
    ) -> 'AutomationListResponse':
        async with get_async_db_context(db) as db:
            stmt = select(Automation).filter_by(user_id=user_id)

            if folder_id:
                stmt = stmt.filter(Automation.folder_id == folder_id)

            if query:
                # Search the name column and the prompt inside the JSON data.
                data_text = cast(Automation.data, String)
                stmt = stmt.filter(
                    or_(
                        Automation.name.ilike(f'%{query}%'),
                        *(data_text.ilike(f'%{variant}%') for variant in json_text_variants(query)),
                    )
                )

            if status == 'active':
                stmt = stmt.filter(Automation.is_active == True, ~Automation.is_stale)
            elif status == 'paused':
                stmt = stmt.filter(Automation.is_active == False, ~Automation.is_stale)
            elif status == 'stale':
                stmt = stmt.filter(Automation.is_stale)

            stmt = stmt.order_by(Automation.created_at.desc())

            # Get total count
            count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
            total = count_result.scalar()

            if skip:
                stmt = stmt.offset(skip)
            if limit:
                stmt = stmt.limit(limit)

            result = await db.execute(stmt)
            rows = result.scalars().all()
            return AutomationListResponse(
                items=[AutomationModel.model_validate(r) for r in rows],
                total=total,
            )

    async def update_by_id(
        self,
        id: str,
        form: AutomationForm,
        next_run_at: int,
        db: Optional[AsyncSession] = None,
    ) -> Optional[AutomationModel]:
        async with get_async_db_context(db) as db:
            row = await db.get(Automation, id)
            if not row:
                return None
            data = form.data.model_dump()
            if 'fallback_model_id' not in form.data.model_fields_set:
                data['fallback_model_id'] = (row.data or {}).get('fallback_model_id')
            active = Automation.is_active if form.is_active is None else form.is_active
            values = {}
            if 'attempt_limit' in form.model_fields_set:
                values['attempt_limit'] = form.attempt_limit
            attempt_limit = values.get('attempt_limit', Automation.attempt_limit)
            await db.execute(
                update(Automation)
                .where(Automation.id == id)
                .values(
                    name=form.name,
                    folder_id=form.folder_id,
                    data=data,
                    meta=form.meta,
                    is_active=active,
                    next_run_at=case(
                        (and_(active, or_(attempt_limit == 0, Automation.attempts_used < attempt_limit)), next_run_at),
                        else_=None,
                    ),
                    updated_at=int(time.time_ns()),
                    **values,
                )
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            row = await db.get(Automation, id, populate_existing=True)
            return AutomationModel.model_validate(row) if row else None

    async def clear_folder_ids(
        self,
        user_id: str,
        folder_ids: list[str],
        db: Optional[AsyncSession] = None,
    ) -> int:
        if not folder_ids:
            return 0
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(Automation)
                .where(Automation.user_id == user_id, Automation.folder_id.in_(folder_ids))
                .values(folder_id=None, updated_at=int(time.time_ns()))
            )
            await db.commit()
            return result.rowcount or 0

    async def toggle(
        self,
        id: str,
        next_run_at: Optional[int],
        db: Optional[AsyncSession] = None,
    ) -> Optional[AutomationModel]:
        async with get_async_db_context(db) as db:
            await db.execute(
                update(Automation)
                .where(Automation.id == id, ~Automation.is_stale)
                .values(
                    is_active=~Automation.is_active,
                    next_run_at=case(
                        (
                            ~Automation.is_active
                            & or_(Automation.attempt_limit == 0, Automation.attempts_used < Automation.attempt_limit),
                            next_run_at,
                        ),
                        else_=None,
                    ),
                    updated_at=int(time.time_ns()),
                )
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            row = await db.get(Automation, id, populate_existing=True)
            return AutomationModel.model_validate(row) if row else None

    async def delete(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            row = await db.get(Automation, id)
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def claim_due(
        self, now_ns: int, limit: int = 10, db: Optional[AsyncSession] = None
    ) -> list[tuple[AutomationModel, str]]:
        """Reserve each occurrence and create its run record in the same transaction."""
        async with get_async_db_context(db) as db:
            # A continuation needs the previous turn to finish before it can
            # resolve its chat and history. Separate chats and channels may overlap.
            same_chat = and_(
                func.coalesce(Automation.data['target']['type'].as_string(), 'chat') == 'chat',
                func.coalesce(Automation.data['target']['chat_mode'].as_string(), 'new') == 'same',
            )
            running = select(AutomationRun.id).where(
                AutomationRun.automation_id == Automation.id, AutomationRun.status == 'running'
            ).correlate(Automation).exists()
            can_start = or_(~same_chat, ~running)
            stmt = (
                select(Automation)
                .where(
                    Automation.is_active == True,
                    Automation.next_run_at <= now_ns,
                    or_(Automation.attempt_limit == 0, Automation.attempts_used < Automation.attempt_limit),
                    can_start,
                )
                .order_by(Automation.next_run_at)
                .limit(limit)
            )

            if db.bind.dialect.name == 'postgresql':
                stmt = stmt.with_for_update(skip_locked=True)

            result = await db.execute(stmt)
            rows = result.scalars().all()

            from open_webui.utils.automations import next_run_ns
            from open_webui.utils.recurrence import RecurrenceEvaluationTimeout

            # Batch-fetch user timezones so rescheduling respects each
            # user's local timezone instead of falling back to server time.
            user_ids = list({row.user_id for row in rows})
            timezone_by_user_id: dict[str, Optional[str]] = {}
            if user_ids:
                from open_webui.models.users import User

                tz_result = await db.execute(select(User.id, User.timezone).where(User.id.in_(user_ids)))
                timezone_by_user_id = {uid: tz for uid, tz in tz_result.all()}

            claimed = []
            for row in rows:
                attempts_used = row.attempts_used + 1
                next_run_at = None
                if not row.attempt_limit or attempts_used < row.attempt_limit:
                    try:
                        next_run_at = await next_run_ns(row.data.get('rrule', ''), tz=timezone_by_user_id.get(row.user_id))
                    except RecurrenceEvaluationTimeout:
                        log.warning('Skipping automation %s: recurrence evaluation timed out', row.id)
                        continue
                # Compare the snapshot as well as using PostgreSQL row locks.
                # SQLite workers can read the same due row before either writes.
                updated = await db.execute(
                    update(Automation)
                    .where(
                        Automation.id == row.id,
                        Automation.is_active == True,
                        Automation.next_run_at == row.next_run_at,
                        Automation.attempts_used == row.attempts_used,
                        Automation.updated_at == row.updated_at,
                        can_start,
                    )
                    .values(last_run_at=now_ns, next_run_at=next_run_at, attempts_used=attempts_used)
                    .execution_options(synchronize_session=False)
                )
                if not updated.rowcount:
                    continue

                run_id = str(uuid4())
                db.add(AutomationRun(
                    id=run_id,
                    automation_id=row.id,
                    status='running',
                    created_at=now_ns,
                    lease_until=time.time_ns() + SCHEDULED_RUN_LEASE_NS,
                ))
                automation = AutomationModel.model_validate(row)
                automation.last_run_at = now_ns
                automation.next_run_at = next_run_at
                automation.attempts_used = attempts_used
                claimed.append((automation, run_id))

            await db.commit()
            return claimed


####################
# AutomationRunTable
####################


class AutomationRunTable:
    async def renew_claim(self, run_id: str) -> bool:
        now = time.time_ns()
        async with get_async_db_context() as db:
            result = await db.execute(
                update(AutomationRun)
                .where(AutomationRun.id == run_id, AutomationRun.lease_until > now)
                .values(lease_until=now + SCHEDULED_RUN_LEASE_NS)
            )
            await db.commit()
            return bool(result.rowcount)

    async def finish_claim(
        self, run_id: str, status: str, chat_id: Optional[str] = None, error: Optional[str] = None
    ) -> None:
        async with get_async_db_context() as db:
            await db.execute(
                update(AutomationRun)
                .where(AutomationRun.id == run_id, AutomationRun.status == 'running')
                .values(status=status, chat_id=chat_id, error=error, lease_until=None)
            )
            await db.commit()

    async def expire_claims(self, now_ns: int) -> None:
        async with get_async_db_context() as db:
            await db.execute(
                update(AutomationRun)
                .where(AutomationRun.lease_until <= now_ns)
                .values(status='error', error='Scheduler stopped before the run finished', lease_until=None)
            )
            await db.commit()

    async def insert(
        self,
        automation_id: str,
        status: str,
        chat_id: Optional[str] = None,
        error: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> AutomationRunModel:
        async with get_async_db_context(db) as db:
            row = AutomationRun(
                id=str(uuid4()),
                automation_id=automation_id,
                chat_id=chat_id,
                status=status,
                error=error,
                created_at=int(time.time_ns()),
            )
            db.add(row)
            await db.commit()
            return AutomationRunModel.model_validate(row)

    async def get_latest(self, automation_id: str, db: Optional[AsyncSession] = None) -> Optional[AutomationRunModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AutomationRun)
                .filter_by(automation_id=automation_id)
                .where(AutomationRun.status != 'running')
                .order_by(AutomationRun.created_at.desc())
                .limit(1)
            )
            row = result.scalars().first()
            return AutomationRunModel.model_validate(row) if row else None

    async def get_latest_chat(
        self, automation_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[AutomationRunModel]:
        """Find this automation's latest chat thread across successful or failed runs."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AutomationRun)
                .where(
                    AutomationRun.automation_id == automation_id,
                    AutomationRun.chat_id.is_not(None),
                    ~AutomationRun.chat_id.like('channel:%'),
                )
                .order_by(AutomationRun.created_at.desc())
                .limit(1)
            )
            row = result.scalars().first()
            return AutomationRunModel.model_validate(row) if row else None

    async def get_latest_batch(
        self, automation_ids: list[str], db: Optional[AsyncSession] = None
    ) -> dict[str, AutomationRunModel]:
        """Fetch the latest run for each automation in a single query."""
        if not automation_ids:
            return {}
        async with get_async_db_context(db) as db:
            # Subquery: max created_at per automation_id
            subq = (
                select(
                    AutomationRun.automation_id,
                    func.max(AutomationRun.created_at).label('max_created'),
                )
                .filter(AutomationRun.automation_id.in_(automation_ids), AutomationRun.status != 'running')
                .group_by(AutomationRun.automation_id)
                .subquery()
            )
            result = await db.execute(
                select(AutomationRun).join(
                    subq,
                    (AutomationRun.automation_id == subq.c.automation_id)
                    & (AutomationRun.created_at == subq.c.max_created),
                )
                .where(AutomationRun.status != 'running')
            )
            rows = result.scalars().all()
            return {row.automation_id: AutomationRunModel.model_validate(row) for row in rows}

    async def get_by_automation(
        self,
        automation_id: str,
        skip: int = 0,
        limit: int = 50,
        db: Optional[AsyncSession] = None,
    ) -> list[AutomationRunModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AutomationRun)
                .filter_by(automation_id=automation_id)
                .where(AutomationRun.status != 'running')
                .order_by(AutomationRun.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            rows = result.scalars().all()
            return [AutomationRunModel.model_validate(r) for r in rows]

    async def delete_by_automation(self, automation_id: str, db: Optional[AsyncSession] = None) -> int:
        async with get_async_db_context(db) as db:
            result = await db.execute(delete(AutomationRun).filter_by(automation_id=automation_id))
            await db.commit()
            return result.rowcount

    async def get_runs_by_user_range(
        self,
        user_id: str,
        start_ns: int,
        end_ns: int,
        limit: int = 500,
        db: Optional[AsyncSession] = None,
    ) -> list[tuple['AutomationRunModel', 'AutomationModel']]:
        """Get runs within a date range for a user, joined with parent automation."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AutomationRun, Automation)
                .join(Automation, Automation.id == AutomationRun.automation_id)
                .filter(
                    Automation.user_id == user_id,
                    AutomationRun.status != 'running',
                    AutomationRun.created_at >= start_ns,
                    AutomationRun.created_at < end_ns,
                )
                .order_by(AutomationRun.created_at.desc())
                .limit(limit)
            )
            return [
                (AutomationRunModel.model_validate(run), AutomationModel.model_validate(auto))
                for run, auto in result.all()
            ]


Automations = AutomationTable()
AutomationRuns = AutomationRunTable()
