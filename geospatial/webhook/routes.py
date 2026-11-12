import uuid
import logging
from flask import request, jsonify, Response
import JSON

from .authentication import



@route("/webhooks/events", methods=["GET"])
def get_webhook_events() -> Response:
    """
    Returns a list of all event types GEO_CLOUD supports.
    """
    return jsonify(list(WebhookEventType))

@route("/webhooks/subscriptions", methods=["POST"])
def create_webhook_subscription() -> Response:
    """
    Registers a new webhook URL and its chosen events.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    url = data.get("url")
    events = data.get("events")
    secret = data.get("secret")

    if not url or not events or not secret:
        return jsonify({"error": "Missing required fields"}), 400

    # Validate events
    invalid_events = [event for event in events if event not in WebhookEventType.__members__]
    if invalid_events:
        return jsonify({"error": f"Invalid event types: {invalid_events}"}), 400

    # Create the subscription
    subscription = WebhookSubscription(
        owner_id=get_authenticated_user_id(),
        url=url,
        events=events,
        secret=secret,
        status=WebhookStatus.PENDING,
    )
    
    db.session.add(subscription)
    db.session.commit()

    return jsonify({"id": str(subscription.id)}), 201



@route("/webhooks/subscriptions", methods=["GET"])
def list_webhook_subscriptions() -> Response: 
    """
    Lists the authenticated owner's subscriptions.
    """
    owner_id = get_authenticated_user_id()
    subscriptions = WebhookSubscription.query.filter_by(owner_id=owner_id).all()
    return jsonify([{
        "id": str(sub.id),
        "url": sub.url,
        "events": sub.events,
        "status": sub.status.value,
        "created_at": sub.created_at.isoformat(),
    } for sub in subscriptions]), 200




@route("/webhooks/subscriptions/<uuid:subscription_id>", methods=["GET"])
def get_webhook_subscription(subscription_id: uuid.UUID) -> Response:
    """
    Retrieves one subscription.
    """
    owner_id = get_authenticated_user_id()
    subscription = WebhookSubscription.query.filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        return jsonify({"error": "Subscription not found"}), 404

    return jsonify({
        "id": str(subscription.id),
        "url": subscription.url,
        "events": subscription.events,
        "status": subscription.status.value,
        "created_at": subscription.created_at.isoformat(),
    }), 200


@route("/webhooks/subscriptions/<uuid:subscription_id>", methods=["PATCH"])
def update_webhook_subscription(subscription_id: uuid.UUID) -> Response:
    """
    Updates its URL or subscribed events.
    """
    owner_id = get_authenticated_user_id()
    subscription = WebhookSubscription.query.filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        return jsonify({"error": "Subscription not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    url = data.get("url")
    events = data.get("events")

    if url:
        subscription.url = url
    if events:
        # Validate events
        invalid_events = [event for event in events if event not in WebhookEventType.__members__]
        if invalid_events:
            return jsonify({"error": f"Invalid event types: {invalid_events}"}), 400
        subscription.events = events

    db.session.commit()

    return jsonify({
        "id": str(subscription.id),
        "url": subscription.url,
        "events": subscription.events,
        "status": subscription.status.value,
        "created_at": subscription.created_at.isoformat(),
    }), 200



@route("/webhooks/subscriptions/<uuid:subscription_id>", methods=["DELETE"])
def delete_webhook_subscription(subscription_id: uuid.UUID) -> Response:
    """
    Disables a subscription without deleting its history.
    """
    owner_id = get_authenticated_user_id()
    subscription = WebhookSubscription.query.filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        return jsonify({"error": "Subscription not found"}), 404

    subscription.status = WebhookStatus.DISABLED
    db.session.commit()

    return jsonify({"message": "Subscription disabled"}), 200


@route("/webhooks/subscriptions/<uuid:subscription_id>/rotate-secret", methods=["POST"])
def rotate_webhook_secret(subscription_id: uuid.UUID) -> Response:
    """
    Rotates the secret for a subscription.
    """
    owner_id = get_authenticated_user_id()
    subscription = WebhookSubscription.query.filter_by(id=subscription_id, owner_id=owner_id).first()
    if not subscription:
        return jsonify({"error": "Subscription not found"}), 404

    new_secret = generate_new_secret()  # Implement this function to generate a new secret
    subscription.secret = new_secret
    db.session.commit()

    return jsonify({"message": "Secret rotated", "new_secret": new_secret}), 200


