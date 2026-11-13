from symtable import Class
import uuid
import logging
from typing import List
from fastapi import APIRouter, Response, status,HTTPException, Depends

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .authentication import 
from geospatial.models import WebhookStatus, WebhookSubscription, WebhookEventType

from geospatial.database import db

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
    secret: str
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


@router.post("/webhooks/events",response_model=WebhookEventTypeCreate, status_code=status.HTTP_201_CREATED )
def create_webhook_event() -> Response:
    """
    Creates a new webhook event type.
    """
        data = await request.json()
    if not data:
        return JSONResponse(content={"error": "Invalid JSON payload"}, status_code=400)

    event_type = data.get("event_type")
    description = data.get("description")

    if not event_type or not description:
        return JSONResponse(content={"error": "Missing required fields"}, status_code=400)

    # Check if the event type already exists
    if event_type in WebhookEventType.__members__:
        return JSONResponse(content={"error": "Event type already exists"}, status_code=400)

    # Create the new event type
    new_event = WebhookEventType(event_type, description)
    db.session.add(new_event)
    db.session.commit()

    return JSONResponse(content={"message": "Event type created", "event_type": event_type}, status_code=201)

@router.post("/webhooks/subscriptions", response_model=WebhookSubscription, status_code=status.HTTP_201_CREATED)
def create_webhook_subscription(payload: WebhookSubscriptionCreate) -> Response:
    """
    Registers a new webhook URL and its chosen events.
    """
    invalid_events = [event for event in payload.events if event not in WebhookEventType.__members__]
    if invalid_events:
        return HTTPException(content={"error": f"Invalid event types: {invalid_events}"}, status_code=400)
    subscription = WebhookSubscription(
        owner_id=get_authenticated_user_id(),
        url=payload.url,
        events=payload.events,
        secret=payload.secret,
        status=WebhookStatus.PENDING,
    )
    db.session.add(subscription)
    db.session.commit()

    return JSONResponse(content={"id": str(subscription.id)}, status_code=201)

@router.get("/webhooks/subscriptions", response_model=WebhookSubscriptionResponse, status_code=status.HTTP_201_CREATED)
def list_webhook_subscriptions() -> Response:
    """
    Lists the authenticated owner's subscriptions.
    """
    owner_id = get_authenticated_user_id()
    subscriptions = db.session.query(WebhookSubscription).filter_by(owner_id=owner_id).all()
    return JSONResponse(content=[{
        "id": str(sub.id),
        "url": sub.url,
        "events": sub.events,
        "status": sub.status.value,
        "created_at": sub.created_at.isoformat(),
    } for sub in subscriptions], status_code=200)


@router.get("/webhooks/subscriptions/{subscription_id}")
def get_webhook_subscription(subscription_id: uuid.UUID) -> Response:
    """
    Retrieves one subscription.
    """
    owner_id = get_authenticated_user_id()
    subscription = db.session.query(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        return HTTPException(detail="Subscription not found", status_code=404)

    return JSONResponse(content={
        "id": str(subscription.id),
        "url": subscription.url,
        "events": subscription.events,
        "status": subscription.status.value,
        "created_at": subscription.created_at.isoformat(),
    }, status_code=200)


@router.patch("/webhooks/subscriptions/{uuid:subscription_id}")
def update_webhook_subscription(subscription_id: uuid.UUID) -> Response:
    """
    Updates its URL or subscribed events.
    """
    owner_id = get_authenticated_user_id()
    subscription = db.session.query(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        return HTTPException(detail="Subscription not found", status_code=404)

    data = await request.json()
    if not data:
        return HTTPException(detail="Invalid JSON payload", status_code=400)

    url = data.get("url")
    events = data.get("events")

    if url:
        subscription.url = url
    if events:
        # Validate events
        invalid_events = [event for event in events if event not in WebhookEventType.__members__]
        if invalid_events:
            return HTTPException(detail=f"Invalid event types: {invalid_events}", status_code=400)
        subscription.events = events

    db.session.commit()

    return JSONResponse(content={
        "id": str(subscription.id),
        "url": subscription.url,
        "events": subscription.events,
        "status": subscription.status.value,
        "created_at": subscription.created_at.isoformat(),
    }, status_code=200)

@router.delete("/webhooks/subscriptions/{uuid:subscription_id}")
def delete_webhook_subscription(subscription_id: uuid.UUID) -> Response:
    """
    Disables a subscription without deleting its history.
    """
    owner_id = get_authenticated_user_id()
    subscription = db.session.query(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        return HTTPException(detail="Subscription not found", status_code=404)

    subscription.status = WebhookStatus.DISABLED
    db.session.commit()

    return JSONResponse(content={"message": "Subscription disabled"}, status_code=200)


@router.post("/webhooks/subscriptions/{uuid:subscription_id}/rotate-secret")
def rotate_webhook_secret(subscription_id: uuid.UUID) -> Response:
    """
    Rotates the secret for a subscription.
    """
    owner_id = get_authenticated_user_id()
    subscription = db.session.query(WebhookSubscription).filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        return HTTPException(detail="Subscription not found", status_code=404)

    new_secret = generate_new_secret()  # Implement this function to generate a new secret
    subscription.secret = new_secret
    db.session.commit()

    return JSONResponse(content={"message": "Secret rotated", "new_secret": new_secret}, status_code=200)


