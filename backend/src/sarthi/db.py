from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from sarthi.config import get_settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        s = get_settings()
        s.db_file.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{s.db_file}", connect_args={"check_same_thread": False})

        @event.listens_for(_engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return _engine


def reset_engine():
    """Drop the cached engine so the next call re-reads settings (used by tests)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def init_db():
    import sarthi.models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(get_engine())


def session() -> Session:
    return Session(get_engine())
