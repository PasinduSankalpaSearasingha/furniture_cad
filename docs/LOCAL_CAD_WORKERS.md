# Responsive local UI during CAD calculations

Native CAD and VTK calculations can hold the Python interpreter even when called
from a background thread. Previously, local photo-to-CAD jobs shared the web
server's interpreter, so a long calculation could make the UI and health endpoint
stop responding.

With `JOB_EXECUTOR=local`, Engineering CAD workflows now run in separate Python
worker processes by default. The existing bounded, serial local queues remain in
control. The web server waits for the child without running its native calculations,
and continues serving pages, downloads and status requests.

Workers read the same database and private artifact store, claim the existing
workflow using its lease, and preserve checkpoints and the final AI request allowance.
Starting a worker does not create another furniture workflow or repeat completed
AI stages. API credentials are inherited through the process environment; settings
travel on stdin, and command arguments contain only workflow and lease IDs.
Windows workers start without a visible console.

Private diagnostic logs are written to
`outputs/runtime/workflow-WORKFLOW_ID-LEASE.log`. An unexpected process exit retains
available reference/plan output. It cannot overwrite a different worker's lease.

```dotenv
JOB_EXECUTOR=local
LOCAL_WORKFLOW_PROCESS=1
```

Set `LOCAL_WORKFLOW_PROCESS=0` only when deliberately debugging execution inside
the web process. Eager test execution and Celery deployments keep their existing
execution paths.

Verification includes a real child-process CAD re-export with no AI calls, repeated
responsive health requests while that child runs, process-PID separation, bounded
queues, configuration serialization, and worker-start/exit recovery. See
[worker test results](workflow-process-test-results.xml).
