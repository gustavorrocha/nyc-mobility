import sqlalchemy as sa
from alembic import context

from nyc_mobility.config import database_url

engine = sa.create_engine(database_url(), poolclass=sa.pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
