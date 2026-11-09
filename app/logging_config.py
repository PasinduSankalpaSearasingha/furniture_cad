import json
import logging
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    def format(self, record):
        event = dict(timestamp=datetime.now(timezone.utc).isoformat(),level=record.levelname,
                     logger=record.name,message=record.getMessage())
        for key in ('job_id','request_id','workflow_id','error_code','provider_error'):
            if hasattr(record,key): event[key] = getattr(record,key)
        if record.exc_info: event['exception'] = self.formatException(record.exc_info)
        return json.dumps(event)


def configure_logging():
    logger = logging.getLogger('furniture')
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
