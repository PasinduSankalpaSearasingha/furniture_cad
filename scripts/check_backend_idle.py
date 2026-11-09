"""Read the configured database when an unresponsive backend cannot serve status."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.config import Settings
from app.database import Database,AgentWorkflow
db=Database(Settings().database_url)
with db.sessions() as session:
    active=list(session.scalars(select(AgentWorkflow.id).where(AgentWorkflow.state.in_(('QUEUED','RUNNING','CAD_QUEUED','ESTIMATED_READY')))))
if active:raise SystemExit('Refusing restart: active furniture workflows exist')
print('Database confirms no active furniture workflows')
