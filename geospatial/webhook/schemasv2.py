from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from enum import Enum

class WebhookEvent(str, Enum):
    JOB_QUEUED = "job.queued"
    JOB_STARTED = "job.started"
    JOB_CANCELLED="job.cancelled"
    JOB_SUCCEEDED = "job.succeeded"
    JOB_FAILED = "job.failed"
    JOB_RETRYING = "job.retrying"
    DATASET_CREATED = "dataset.created"
    DATASET_DELETED = "dataset.deleted"
    DATASET_VALIDATION_FAILED = "dataset.validation_failed"
    DATASET_UPLOADED = "dataset.uploaded"
    WEBHOOK_DELIVERY_FAILED = "webhook.delivery_failed"


class WebhookSubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    events: list[WebhookEvent] = Field(min_length=1)


    