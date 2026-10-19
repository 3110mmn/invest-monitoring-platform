from logging.config import fileConfig

from alembic import context

from app.config import settings

config = context.config
# ConfigParser treats `%` as interpolation syntax. Passwords in PostgreSQL URLs are
# percent-encoded, so pass an escaped representation only to Alembic's config value.
# Runtime connections below continue to use the original URL.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    sqlalchemy_url = settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sqlalchemy_url, pool_pre_ping=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
