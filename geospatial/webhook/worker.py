import time
from geospatial.models import WebhookSubscription, WebhookStatus
from geospatial.database import Session, SessionLocal

from geospatial.webhook.repository import claim_next_verification, create_webhook_subscription
from sqlalchemy import select, update, func
from sqlalchemy.orm import Session
from uuid import UUID


class PermanentVerificationError(Exception):
  """Raised when verification fails due to a non-retryable error (e.g., SSRF, 4xx)."""

class UnsafeWebhookURLError(PermanentVerificationError):
  """Raised when a webhook URL violates SSRF or scheme security rules."""

def process_next_verification(session: Session | None = None ) -> bool:
    if session is None:
        with SessionLocal() as session:
            return process_next_verification(session)

    verification = claim_next_verification(session)

    if verification is None:
        return False

    try:
        response = send_verification_challenge(verification)
        verify_challenge_response(response, verification)
        mark_subscription_verified(verification)
    except Exception as error:
        schedule_verification_retry(verification, error)

    return True

"""
process_next_verification()
    |
    v
Open DB transaction
    |
    v
Claim subscription
    |
    v
Commit transaction
    |
    v
Send HTTP challenge
    |
    v
Check subscriber's response
    |
    v
Open new DB transaction
    |
    v
Record verification result
"""


def send_verification_challenge():
    pass

def verify_challenge_response():
    pass
def mark_subscription_verified():
    pass
def schedule_verification_retry():
    pass




def run_webhook_worker(poll_interval=2.0):
    while True:
        did_work = process_next_verification()

        if not did_work:
            time.sleep(poll_interval)

def event_delivery():
    if not subscription.is_active():
        raise Exception("Subscription is not active")
    

def retries():
    pass

import ipaddress
import socket
from urllib.parse import urlparse
from sqlalchemy.orm import Session

# Assume SessionLocal, WebhookSubscription, and claim_next_verification are imported


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class PermanentVerificationError(Exception):
  """Raised when verification fails due to a non-retryable error."""


class UnsafeWebhookURLError(PermanentVerificationError):
  """Raised when a webhook URL violates SSRF or scheme security rules."""


# ---------------------------------------------------------------------------
# Step 1: URL & IP Validation (SSRF Protection)
# ---------------------------------------------------------------------------
def _is_public_ip(ip_str: str) -> bool:
  """Verify that an IP address is strictly public and globally routable."""
  try:
    ip = ipaddress.ip_address(ip_str)
  except ValueError:
    return False

  # Unwrap IPv4-mapped IPv6 addresses (e.g., ::ffff:127.0.0.1)
  if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
    ip = ip.ipv4_mapped

  return (
      ip.is_global
      and not ip.is_private
      and not ip.is_loopback
      and not ip.is_link_local
      and not ip.is_multicast
      and not ip.is_reserved
      and not ip.is_unspecified
  )


def validate_webhook_url(url: str, allow_http: bool = False) -> list[str]:
  """Validate a webhook URL against SSRF vectors and return safe resolved IPs."""
  if not url or not isinstance(url, str):
    raise UnsafeWebhookURLError("Webhook URL must be a non-empty string.")

  parsed = urlparse(url.strip())

  allowed_schemes = {"https", "http"} if allow_http else {"https"}
  if parsed.scheme.lower() not in allowed_schemes:
    raise UnsafeWebhookURLError(
        f"Unsupported URL scheme: '{parsed.scheme}'. Allowed: {allowed_schemes}"
    )

  if parsed.username or parsed.password:
    raise UnsafeWebhookURLError(
        "Credentials in webhook URLs are not permitted."
    )

  hostname = parsed.hostname
  if not hostname:
    raise UnsafeWebhookURLError("Webhook URL is missing a valid hostname.")

  lower_host = hostname.lower().rstrip(".")
  if lower_host == "localhost" or lower_host.endswith(".localhost"):
    raise UnsafeWebhookURLError("Localhost targets are not permitted.")

  port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

  try:
    addr_info = socket.getaddrinfo(
        hostname,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
  except socket.gaierror as exc:
    raise UnsafeWebhookURLError(
        f"Could not resolve hostname '{hostname}'."
    ) from exc

  if not addr_info:
    raise UnsafeWebhookURLError(
        f"No DNS records found for hostname '{hostname}'."
    )

  safe_ips: list[str] = []
  for entry in addr_info:
    ip_str = entry[4][0]
    if not _is_public_ip(ip_str):
      raise UnsafeWebhookURLError(
          f"Hostname '{hostname}' resolved to a restricted IP address: {ip_str}"
      )
    if ip_str not in safe_ips:
      safe_ips.append(ip_str)

  return safe_ips


# ---------------------------------------------------------------------------
# Step 2: Outbound Request & Response Verification (Next Pieces to Implement)
# ---------------------------------------------------------------------------
def send_verification_challenge(verification: WebhookSubscription):
  """Validate the target URL and send the signed challenge payload."""
  # Guard against SSRF before making any outbound connection
  safe_ips = validate_webhook_url(verification.url)

  # TODO: Send HTTP POST request pinned to safe_ips[0] with challenge_token
  raise NotImplementedError("HTTP delivery logic not yet implemented.")


def verify_challenge_response(
    response, verification: WebhookSubscription
) -> None:
  """Verify HTTP 2xx status and confirm the challenge_token was echoed back."""
  # TODO: Implement response validation
  raise NotImplementedError("Response validation logic not yet implemented.")


# ---------------------------------------------------------------------------
# Step 3: State Updaters (Next Pieces to Implement)
# ---------------------------------------------------------------------------
def mark_subscription_verified(
    session: Session, verification: WebhookSubscription
) -> None:
  # TODO: Set status='verified', clear lease fields, record verified_at
  raise NotImplementedError


def mark_subscription_failed(
    session: Session, verification: WebhookSubscription, error: Exception
) -> None:
  # TODO: Set status='failed', clear lease fields, store failure reason
  raise NotImplementedError


def schedule_verification_retry(
    session: Session, verification: WebhookSubscription, error: Exception
) -> None:
  # TODO: Calculate exponential backoff for next_verification_at or fail if max attempts reached
  raise NotImplementedError


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def process_next_verification(session: Session | None = None) -> bool:
  """Claim and process a single pending webhook verification."""
  owns_session = session is None
  db = session or SessionLocal()

  try:
    verification = claim_next_verification(db)
    if verification is None:
      return False

    # Commit the lease immediately so the DB row lock isn't held during network I/O
    db.commit()

    try:
      response = send_verification_challenge(verification)
      verify_challenge_response(response, verification)
    except PermanentVerificationError as error:
      # Catches UnsafeWebhookURLError and other non-retryable errors
      mark_subscription_failed(db, verification, error)
      db.commit()
      return True
    except Exception as error:
      # Transient network/timeout/5xx errors get retried
      schedule_verification_retry(db, verification, error)
      db.commit()
      return True

    mark_subscription_verified(db, verification)
    db.commit()
    return True

  except Exception:
    db.rollback()
    raise
  finally:
    if owns_session:
      db.close()
