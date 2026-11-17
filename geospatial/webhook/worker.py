import secrets
import ipaddress
import socket
import random

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from sqlalchemy import update
from sqlalchemy.orm import Session 

from geospatial.models import WebhookSubscription, WebhookStatus
from geospatial.database import SessionLocal
from geospatial.webhook.repository import claim_next_verification


MAX_VERIFICATION_ATTEMPTS = 5
BASE_RETRY_DELAY_SECONDS = 30
MAX_RETRY_DELAY_SECONDS = 3600


class PermanentVerificationError(Exception):
  """Raised when verification fails due to a non-retryable error (e.g., SSRF, 4xx)."""

class UnsafeWebhookURLError(PermanentVerificationError):
  """Raised when a webhook URL violates SSRF or scheme security rules."""



def _normalize_ip(ip_str: str) -> str:
    cleaned = ip_str.split("%")[0].strip()
    ip = ipaddress.ip_address(cleaned)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip)



def _is_public_ip(ip_str: str) -> bool:
  try:
    cleaned = ip_str.split("%")[0].strip()
    ip = ipaddress.ip_address(cleaned)
  except ValueError:
    return False
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


def validate_webhook_url(url: str) -> list[str]:
  if not url or not isinstance(url, str):
    raise UnsafeWebhookURLError("Webhook URL must be a non-empty string.")
  parsed = urlparse(url.strip())
  if parsed.scheme.lower() !="https":
    raise UnsafeWebhookURLError(
        f"Unsupported URL scheme: '{parsed.scheme}'.Allowed: 'https'"
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
    normalized = _normalize_ip(ip_str)
    if normalized not in safe_ips:
      safe_ips.append(normalized)

  return safe_ips
class SafeIPTransport(httpx.HTTPTransport):
  def __init__(self, target_ips: list[str], *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.allowed_ips = {_normalize_ip(ip) for ip in target_ips if ip and ip.strip()}
    if not self.allowed_ips:
      raise UnsafeWebhookURLError("No allowed target IPs provided to transport.")
  def handle_request(self, request: httpx.Request) -> httpx.Response:
    original_host = request.url.host
    if not original_host:
      raise UnsafeWebhookURLError("Request URL is missing a valid hostname.")

    pinned_ip = next(iter(self.allowed_ips))
    ip_obj = ipaddress.ip_address(pinned_ip)
    formatted_host = f"[{pinned_ip}]" if ip_obj.version == 6 else pinned_ip
    
    pinned_url = request.url.copy_with(host=formatted_host)
    headers = request.headers.copy()

    if "host" not in headers:
        host_header = (
          f"[{original_host}]" if ":" in original_host else original_host
        )
        if request.url.port is not None:
            host_header = f"{host_header}:{request.url.port}"

        headers["host"] = host_header

    extensions = dict(request.extensions)
    extensions["sni_hostname"] = original_host
    pinned_request = httpx.Request(
        method=request.method,
        url=pinned_url,
        headers=headers,
        content=request.stream,
        extensions=extensions,
    )
    response = super().handle_request(pinned_request)
    self._verify_connected_peer(response, pinned_ip)
    return response

  def _verify_connected_peer(self, response: httpx.Response, expected_ip:str) -> None:
    network_stream = response.extensions.get("network_stream")
    if network_stream is None:
        response.close()
        raise UnsafeWebhookURLError(
          "Could not verify the connected server IP"
        )

    server_addr = network_stream.get_extra_info("server_addr")
    if not server_addr or not isinstance(server_addr, tuple):
          response.close()
          raise UnsafeWebhookURLError(
              f"Could not determine the connected server IP."
          )
    try:
        peer_ip = _normalize_ip(server_addr[0])
    except ValueError as exc:
        response.close()
        raise UnsafeWebhookURLError(
            "Connected server returned an invalid peer IP."
        ) from exc

    if peer_ip != expected_ip:
        response.close()
        raise UnsafeWebhookURLError(
            f"Connected peer IP {peer_ip} does not match "
            f"the pinned IP {expected_ip}."
        )
def send_verification_challenge(verification: WebhookSubscription) -> httpx.Response:
    safe_ips = validate_webhook_url(verification.url)
    if not safe_ips:
        raise UnsafeWebhookURLError(
            f"Webhook URL '{verification.url}' did not resolve to any public IP addresses."
        )
    challenge_token = verification.verification_token
    if not challenge_token:
        raise PermanentVerificationError(
            "Subscription has no verification token."
        )

    payload = {
        "challenge_token": challenge_token,
        "subscription_id": str(verification.id),
    }
    transport = SafeIPTransport(target_ips=safe_ips)

    with httpx.Client(transport=transport, timeout=5.0, follow_redirects=False) as client:
        try:
        response = client.post(
            verification.url, 
            json=payload, 
            headers={"Content-Type": "application/json"})
        except UnsafeWebhookURLError: 
            raise
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error occurred: {exc}") from exc
    if 300 <= response.status_code < 400:
        raise PermanentVerificationError(
            f"Subscriber responded with a redirect (HTTP {response.status_code})."
        )
    if 400 <= response.status_code < 500 and response.status_code != 429:
        raise PermanentVerificationError(
            f"Subscriber responded with a client error (HTTP {response.status_code})."
        )
    response.raise_for_status()
    return response


def verify_challenge_response(
    response: httpx.Response, verification: WebhookSubscription
) -> None:
  if response.status_code != 200:
    raise PermanentVerificationError(
        f"Subscriber responded with status {response.status_code} instead of 200 OK."
    )
  body = response.text.strip()
  expected_token = verification.verification_token or ""
  if not body or not secrets.compare_digest(body, expected_token):
    raise PermanentVerificationError(
        "Subscriber's response did not match the expected challenge token."
    )
def _clear_lease(
    verification: WebhookSubscription,
    lease_token,
) -> bool:

    # Only the worker that owns this lease can clear it.
    if verification.verification_lease_token != lease_token:
        return False

    verification.verification_lease_token = None
    verification.verification_lease_expires_at = None

    return True
# ---------------------------------------------------------------------------
# Step 3: State Updaters (Next Pieces to Implement)
# ---------------------------------------------------------------------------
def mark_subscription_verified(
    session: Session,
    verification: WebhookSubscription,
    lease_token,
) -> bool:

    now = datetime.now(timezone.utc)

    result = session.execute(
        update(WebhookSubscription)
        .where(
            WebhookSubscription.id == verification.id,
            WebhookSubscription.verification_lease_token == lease_token,
        )
        .values(
            status=WebhookStatus.ACTIVE,
            verified_at=now,
            next_verification_at=None,
            last_verification_error=None,
            verification_lease_token=None,
            verification_lease_expires_at=None,
        )
    )

    return result.rowcount == 1

def mark_subscription_failed(
    session: Session,
    verification: WebhookSubscription,
    error: Exception,
    lease_token,
) -> bool:

    result = session.execute(
        update(WebhookSubscription)
        .where(
            WebhookSubscription.id == verification.id,
            WebhookSubscription.verification_lease_token == lease_token,
        )
        .values(
            status=WebhookStatus.FAILED,
            next_verification_at=None,
            last_verification_error=str(error)[:1000],
            verification_lease_token=None,
            verification_lease_expires_at=None,
        )
    )

    return result.rowcount == 1

def schedule_verification_retry(
    session: Session,
    verification: WebhookSubscription,
    error: Exception,
    lease_token,
) -> bool:

    # Attempts were already incremented when the job was claimed.
    attempts = verification.verification_attempts or 0

    # Stop retrying after the maximum number of attempts.
    if attempts >= MAX_VERIFICATION_ATTEMPTS:
        return mark_subscription_failed(
            session,
            verification,
            RuntimeError(
                f"Maximum verification attempts "
                f"({MAX_VERIFICATION_ATTEMPTS}) reached. "
                f"Last error: {error}"
            ),
            lease_token,
        )

    # Calculate exponential backoff.
    backoff = min(
        BASE_RETRY_DELAY_SECONDS * (2 ** (attempts - 1)),
        MAX_RETRY_DELAY_SECONDS,
    )

    # Add random jitter to avoid synchronized retries.
    jitter = random.uniform(0, backoff * 0.2)

    next_run = datetime.now(timezone.utc) + timedelta(
        seconds=backoff + jitter
    )

    # Update only if this worker still owns the lease.
    result = session.execute(
        update(WebhookSubscription)
        .where(
            WebhookSubscription.id == verification.id,
            WebhookSubscription.verification_lease_token == lease_token,
        )
        .values(
            status=WebhookStatus.PENDING,
            next_verification_at=next_run,
            last_verification_error=str(error)[:1000],
            verification_lease_token=None,
            verification_lease_expires_at=None,
        )
    )

    return result.rowcount == 1

