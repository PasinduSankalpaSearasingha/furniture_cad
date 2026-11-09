"""Private local worker entrypoint. Configuration arrives on stdin, never in argv."""
import json
import os
import sys
from uuid import UUID


def main():
    from app.config import Settings
    from app.database import Database,AgentWorkflow,AgentEvent
    from app.logging_config import configure_logging
    from app.agents.orchestrator import WorkflowRunner
    workflow_id,lease=(str(UUID(value)) for value in sys.argv[1:3])
    config=Settings(**json.load(sys.stdin));configure_logging();db=Database(config.database_url)
    try:
        with db.sessions.begin() as session:
            row=session.get(AgentWorkflow,workflow_id)
            if not row or row.state!='QUEUED':return
            session.add(AgentEvent(workflow_id=workflow_id,event='isolated_worker_started',stage='starting',
                details=dict(pid=os.getpid(),lease=lease)))
        runner=WorkflowRunner(workflow_id,db,config);runner.lease=lease;runner.run()
    finally:db.engine.dispose()


if __name__=='__main__':main()
