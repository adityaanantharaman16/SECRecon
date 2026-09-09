from alembic import context

from secrecon.config import Settings
from secrecon.db.session import make_engine

config = context.config


def run_migrations() -> None:
    engine = make_engine(Settings())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url.get_secret_value(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations()
