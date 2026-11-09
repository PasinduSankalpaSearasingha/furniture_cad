import os
from alembic import context
from sqlalchemy import engine_from_config, pool
from pathlib import Path
from app.database import Base

config = context.config
url = os.getenv('DATABASE_URL', config.get_main_option('sqlalchemy.url'))
config.set_main_option('sqlalchemy.url',url.replace('%','%%'))
if url.startswith('sqlite:///'):
    Path(url.removeprefix('sqlite:///')).resolve().parent.mkdir(parents=True,exist_ok=True)

if context.is_offline_mode():
    context.configure(url=url,target_metadata=Base.metadata,literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    connectable = engine_from_config(config.get_section(config.config_ini_section),prefix='sqlalchemy.',poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,target_metadata=Base.metadata,compare_type=True)
        with context.begin_transaction(): context.run_migrations()
