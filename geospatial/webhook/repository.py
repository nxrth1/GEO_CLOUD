from sqlalchemy.orm import Session

from geospatial.models import WebhookSubscription


def create_webhook_subscription(
    session: Session,
    webhook_subscription: WebhookSubscription,
) -> WebhookSubscription:
    session.add(webhook_subscription)
    session.flush()
    return webhook_subscription