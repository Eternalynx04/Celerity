"""Database connections and the one deliberately removable index."""

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


ORDER_INDEX = "ix_orders_customer_created"


def make_engine(database_url: str) -> Engine:
    engine = create_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise ValueError("This lab requires PostgreSQL; SQLite cannot verify its query plans.")
    return engine


def make_sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def configure_order_index(engine: Engine, *, missing: bool) -> None:
    # Identifiers are constants. No user-supplied text is interpolated into SQL.
    with engine.begin() as connection:
        if missing:
            connection.execute(text(f"DROP INDEX IF EXISTS {ORDER_INDEX}"))
        else:
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {ORDER_INDEX} "
                    "ON orders (customer_id, created_at DESC, id DESC)"
                )
            )
        connection.execute(text("ANALYZE orders"))


def order_index_present(engine: Engine) -> bool:
    return any(index["name"] == ORDER_INDEX for index in inspect(engine).get_indexes("orders"))
