"""Private numerical worker executable, compatible with local and Celery jobs."""
import json
from pathlib import Path
import sys


def main():
    request_path, events_path = map(Path, sys.argv[1:])
    request = json.loads(request_path.read_text(encoding='utf-8'))
    with events_path.open('a', encoding='utf-8') as stream:
        def emit(message):
            stream.write(json.dumps(message, allow_nan=False) + '\n')
            stream.flush()
        try:
            from app.cad_optimizer import optimize_cad
            def progress(evaluations, maximum):
                emit(dict(kind='progress', evaluations=evaluations))
            def publish(plan, report):
                emit(dict(kind='best', plan=plan.model_dump(mode='json'), report=report))
            result = optimize_cad(**request, progress=progress, publish_best=publish)
            emit(dict(kind='result', plan=result.plan.model_dump(mode='json'), report=result.report))
        except Exception as exc:
            emit(dict(kind='error', error=type(exc).__name__))


if __name__ == '__main__':
    main()
