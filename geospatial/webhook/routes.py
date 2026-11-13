import logging
import secrets
from typing import List, Optional
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Response, status,HTTPException, Depends
from sqlalchemy.orm import Session

from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

from geospatial.auth import get_current_owner
from geospatial.models import WebhookStatus, WebhookSubscription
from geospatial.dependancies import get_db


router = APIRouter()
logger = logging.getLogger(__name__)


class WebhookEventType(str, Enum):
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"

class WebhookSubscriptionResponse(BaseModel):
    id: UUID
    url: str
    events: List[str]
    status: str
    created_at: str

    model_config = {
        "from_attributes": True,
    }

class WebhookSubscriptionCreateResponse(BaseModel):
    id: UUID
    url: str
    events: List[str]
    status: str
    created_at: str
    secret: str

class WebhookSubscriptionCreate(BaseModel):
    url: HttpUrl
    events: List[str]

class WebhookSubscriptionUpdate(BaseModel):
    url: Optional[HttpUrl] = None
    events: Optional[List[WebhookEventType]] = None


@router.get("/webhooks/events")
def get_webhook_events() -> Response:
    """
    Returns a list of all event types GEO_CLOUD supports.
    """
    return JSONResponse(content=[e.value for e in WebhookEventType], status_code=200)

@router.post("/webhooks/subscriptions",response_model=WebhookSubscriptionCreateResponse, status_code=status.HTTP_201_CREATED)
def create_webhook_subscription(payload: WebhookSubscriptionCreate, db: Session = Depends(get_db), owner_id: str = Depends(get_current_owner)) -> WebhookSubscriptionCreateResponse:
    """
    Registers a new webhook URL and its chosen events.
    """
    valid_events = {event.value for event in WebhookEventType}
    invalid_events = [event for event in payload.events if event not in valid_events]
    if invalid_events:
        raise HTTPException(status_code=400, detail={"error": f"Invalid event types: {invalid_events}"})

    signed_secret = f"whsec_{secrets.token_urlsafe(32)}" # Generate a secure random secret
    subscription = WebhookSubscription(
        owner_id = owner_id,
        url=str(payload.url),
        events=payload.events,
        secret = signed_secret,
        status=WebhookStatus.PENDING,
    )
    db.add(subscription)
    db.flush()  # Ensure the subscription ID is generated before committing
    db.commit()
    db.refresh(subscription)  # Optional: reloads DB-generated fields (like ID/timestamps) onto the object

    return {
        "id": subscription.id,
        "url": subscription.url,
        "events": subscription.events,
        "status": subscription.status.value,
        "created_at": subscription.created_at.isoformat(),
        "secret": signed_secret,
    }

@router.get("/webhooks/subscriptions", response_model=list[WebhookSubscriptionResponse], status_code=status.HTTP_200_OK)
def list_webhook_subscriptions(db: Session = Depends(get_db)) -> Response:
    """
    Lists the authenticated owner's subscriptions.
    """
    owner_id = Depends(get_current_owner)
    subscriptions = db.query(WebhookSubscription).filter_by(owner_id=owner_id).all()
    return JSONResponse(content=[{
        "id": str(sub.id),
        "url": sub.url,
        "events": sub.events,
        "status": sub.status.value,
        "created_at": sub.created_at.isoformat(),
    } for sub in subscriptions], status_code=200)


@router.get("/webhooks/subscriptions/{subscription_id}")
def get_webhook_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """
    Retrieves one subscription.
    """
    owner_id = Depends(get_current_owner)
    subscription = db.query(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        raise HTTPException(detail="Subscription not found", status_code=404)

    return JSONResponse(content={
        "id": str(subscription.id),
        "url": subscription.url,
        "events": subscription.events,
        "status": subscription.status.value,
        "created_at": subscription.created_at.isoformat(),
    }, status_code=200)


@router.patch("/webhooks/subscriptions/{subscription_id}")
def update_webhook_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_db), payload: WebhookSubscriptionUpdate = None) -> Response:
    """
    Updates its URL or subscribed events.
    """
    owner_id = Depends(get_current_owner)
    subscription = db.query(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        raise HTTPException(detail="Subscription not found", status_code=404)

    if not payload:
        raise HTTPException(detail="Invalid JSON payload", status_code=400)

    url = payload.url
    events = payload.events

    if url:
        subscription.url = url
    if events:
        # Validate events
        valid_events = {event.value for event in WebhookEventType}
        invalid_events = [event for event in events if event not in valid_events]
        if invalid_events:
            raise HTTPException(detail=f"Invalid event types: {invalid_events}", status_code=400)
        subscription.events = events

    db.commit()
    db.refresh(subscription)  # Optional: reloads DB-generated fields (like ID/timestamps) onto the object
    

    return JSONResponse(content={
        "id": str(subscription.id),
        "url": subscription.url,
        "events": subscription.events,
        "status": subscription.status.value,
        "created_at": subscription.created_at.isoformat(),
    }, status_code=200)

@router.delete("/webhooks/subscriptions/{subscription_id}")
def delete_webhook_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """
    Disables a subscription without deleting its history.
    """
    owner_id = Depends(get_current_owner)
    subscription = db.query(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        raise HTTPException(detail="Subscription not found", status_code=404)

    subscription.status = WebhookStatus.DISABLED
    db.commit()

    return JSONResponse(content={"message": "Subscription disabled"}, status_code=200)


@router.post("/webhooks/subscriptions/{subscription_id}/rotate-secret")
def rotate_webhook_secret(subscription_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """
    Rotates the secret for a subscription.
    """
    owner_id = Depends(get_current_owner)
    subscription = db.query(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        raise HTTPException(detail="Subscription not found", status_code=404)

    new_secret = generate_new_secret()  # Implement this function to generate a new secret
    subscription.secret = new_secret
    db.commit()

    return JSONResponse(content={"message": "Secret rotated", "new_secret": new_secret}, status_code=200)


