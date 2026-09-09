from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from secrecon.config import Settings


def make_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"},
    )


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
