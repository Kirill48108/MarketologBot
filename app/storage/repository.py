import asyncio
from typing import Optional

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class MessageLog(Base):
    __tablename__ = "message_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String, index=True)
    message_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LinkStat(Base):
    __tablename__ = "link_stat"
    __table_args__ = (UniqueConstraint("slug", name="uq_link_slug"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String, index=True)
    target_url: Mapped[str] = mapped_column(Text)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def init_db(dsn: str) -> sessionmaker[Session]:
    engine = create_engine(dsn, echo=False, pool_pre_ping=True, future=True)
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


# --- sync-функции + async-обёртки ---


def _add_message_log_sync(db: sessionmaker[Session], chat_id: str, text: str) -> None:
    with db() as session:
        session.add(MessageLog(chat_id=chat_id, message_text=text))
        session.commit()


async def add_message_log(db: sessionmaker[Session], chat_id: str, text: str) -> None:
    await asyncio.to_thread(_add_message_log_sync, db, chat_id, text)


def _get_link_sync(db: sessionmaker[Session], slug: str) -> Optional[LinkStat]:
    with db() as session:
        res = session.execute(select(LinkStat).where(LinkStat.slug == slug))
        return res.scalar_one_or_none()


def _upsert_link_sync(db: sessionmaker[Session], slug: str, target_url: str) -> LinkStat:
    with db() as session:
        res = session.execute(select(LinkStat).where(LinkStat.slug == slug))
        row = res.scalar_one_or_none()
        if row:
            row.target_url = target_url
        else:
            row = LinkStat(slug=slug, target_url=target_url, clicks=0)
            session.add(row)
        session.commit()
        session.refresh(row)
        return row


def _increment_click_sync(db: sessionmaker[Session], slug: str) -> Optional[str]:
    with db() as session:
        res = session.execute(select(LinkStat).where(LinkStat.slug == slug))
        row = res.scalar_one_or_none()
        if not row:
            return None
        row.clicks += 1
        target = row.target_url
        session.commit()
        return target


async def upsert_link(db: sessionmaker[Session], slug: str, target_url: str) -> LinkStat:
    return await asyncio.to_thread(_upsert_link_sync, db, slug, target_url)


async def increment_click(db: sessionmaker[Session], slug: str) -> Optional[str]:
    return await asyncio.to_thread(_increment_click_sync, db, slug)
