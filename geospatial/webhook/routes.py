import uuid
import logging
from typing import List

from requests import get, session
from fastapi import APIRouter, Response, status,HTTPException, Depends
from sqlalchemy.orm import Session

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from geospatial.auth import get_current_owner, get_authenticated_user_id
from geospatial.models import WebhookStatus, WebhookSubscription

from geospatial.database import db
from geospatial.routes import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


class WebhookSubscriptionResponse(BaseModel):
    id: str
    url: str
    events: List[str]
    status: str
    created_at: str
class WebhookSubscriptionCreate(BaseModel):
    url: str
    events: List[str]

class WebhookSubscriptionUpdate(BaseModel):
    url: str = None
    events: List[str] = None
Class WebhookEventTypeCreate(BaseModel):
    event_type: str
    description: str


@router.get("/webhooks/events")
def get_webhook_events() -> Response:
    """
    Returns a list of all event types GEO_CLOUD supports.
    """
    return JSONResponse(content=[e.value for e in WebhookEventType], status_code=200)

router.post("/webhooks/subscriptions", response_model=WebhookSubscription, status_code=status.HTTP_201_CREATED)
def create_webhook_subscription(payload: WebhookSubscriptionCreate, db: Session = Depends(get_db)) -> Response:
    """
    Registers a new webhook URL and its chosen events.
    """
    invalid_events = [event for event in payload.events if event not in WebhookEventType.__members__]
    if invalid_events:
        raise HTTPException(content={"error": f"Invalid event types: {invalid_events}"}, status_code=400)
    subscription = WebhookSubscription(
        owner_id=get_authenticated_user_id(),
        url=payload.url,
        events=payload.events,
        secret=payload.secret,
        status=WebhookStatus.PENDING,
    )
    db.add(subscription)
    db.flush()  # Ensure the subscription ID is generated before committing
    db.commit()
    db.refresh(subscription)  # Optional: reloads DB-generated fields (like ID/timestamps) onto the object

    return JSONResponse(content={"id": str(subscription.id)}, status_code=201)

@router.get("/webhooks/subscriptions", response_model=WebhookSubscriptionResponse, status_code=status.HTTP_201_CREATED)
def list_webhook_subscriptions(db: Session = Depends(get_db)) -> Response:
    """
    Lists the authenticated owner's subscriptions.
    """
    owner_id = get_authenticated_user_id()
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
    owner_id = get_authenticated_user_id()
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


@router.patch("/webhooks/subscriptions/{uuid:subscription_id}")
def update_webhook_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_db), payload = WebhookSubscriptionResponse):
    """
    Updates its URL or subscribed events.
    """
    owner_id = get_authenticated_user_id()
    subscription = db.get(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        raise HTTPException(detail="Subscription not found", status_code=404)

    data = await request.json()
    if not data:
        raise HTTPException(detail="Invalid JSON payload", status_code=400)

    url = data.get("url")
    events = data.get("events")

    if url:
        subscription.url = url
    if events:
        # Validate events
        invalid_events = [event for event in events if event not in WebhookEventType.__members__]
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

@router.delete("/webhooks/subscriptions/{uuid:subscription_id}")
def delete_webhook_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """
    Disables a subscription without deleting its history.
    """
    owner_id = get_authenticated_user_id()
    subscription = db.query(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        raise HTTPException(detail="Subscription not found", status_code=404)

    subscription.status = WebhookStatus.DISABLED
    db.commit()

    return JSONResponse(content={"message": "Subscription disabled"}, status_code=200)


@router.post("/webhooks/subscriptions/{uuid:subscription_id}/rotate-secret")
def rotate_webhook_secret(subscription_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """
    Rotates the secret for a subscription.
    """
    owner_id = get_authenticated_user_id()
    subscription = db.query(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        raise HTTPException(detail="Subscription not found", status_code=404)

    new_secret = generate_new_secret()  # Implement this function to generate a new secret
    subscription.secret = new_secret
    db.commit()

    return JSONResponse(content={"message": "Secret rotated", "new_secret": new_secret}, status_code=200)


