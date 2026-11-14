import secrets

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from geospatial.models import WebhookSubscription, WebhookStatus



def create_webhook_subscription(session: Session, webhook_subscription: WebhookSubscription) -> WebhookSubscription:
    session.add(webhook_subscription)
    session.flush()
    return webhook_subscription

def claim_next_verification(session: Session, lease_duration: int = 300,  verification_ttl_seconds: int = 3600) -> WebhookSubscription | None:
    """
    Claim the next webhook subscription that is due for verification.
    This function should be called within a transaction to ensure that
    the claimed subscription is locked for processing.
    """
    now = datetime.now(timezone.utc)
    subscription = (
        session.query(WebhookSubscription)
        .filter(
            WebhookSubscription.status == WebhookStatus.PENDING,
            WebhookSubscription.next_verification_at <= now,
            or_(
                WebhookSubscription.verification_lease_expires_at.is_(None),
                WebhookSubscription.verification_lease_expires_at <= now,
            ),
        )
        .order_by(WebhookSubscription.next_verification_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not subscription:
        return None

    subscription.verification_lease_token =  uuid4()
    subscription.verification_lease_expires_at = now + timedelta(seconds=lease_duration)
    subscription.verification_attempts += 1
    if (
        not subscription.verification_token
        or not subscription.verification_token_expires_at
        or subscription.verification_token_expires_at <= now
    ):
        subscription.verification_token = secrets.token_urlsafe(32)
        subscription.verification_token_expires_at = now + timedelta(
            seconds=verification_ttl_seconds
        )

    session.flush()


    return subscription
"""
Later, we'll change this to return a small data snapshot so the worker doesn't accidentally depend on a detached ORM object.
"""