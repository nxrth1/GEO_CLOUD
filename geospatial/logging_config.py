import logging
import json
from datetime import datetime, timezone

class JSONFormatter(logging.Formatter):

    def __init__(self, service_name):
        super().__init__()
        self.service_name = service_name
    

    def format(self, record):

        log_record = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                timezone.utc
            ).isoformat(),

            "level": record.levelname,
            "service": self.service_name,
            "message": record.getMessage(),
            "logger": record.name
        }
        for field in (
            "event",
            "dataset_id",
            "job_id",
            "worker_id",
            "request_id"
        ):
            value = getattr(record, field, None)
#getattr(object, attribute_name, default_value)  

        # Inject contextual information if provided in the 'extra' dictionary
            if value is not None:
                log_record[field] = value
            
        # Include traceback details if an exception occurred
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_record, default=str)

def setup_logging(level=logging.INFO, service_name="api"):

    logger = logging.getLogger()
    logger.setLevel(level)
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    handler = logging.StreamHandler()

    handler.setFormatter(
        JSONFormatter(service_name)
    )

    logger.addHandler(handler)
    
    return logger


