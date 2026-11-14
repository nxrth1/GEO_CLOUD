"""
Subscription verification
Later: event delivery and retries

Find subscriptions whose verification is due.
Claim one safely using a database lock and a lease.
Send the HTTP verification challenge to the subscriber.
Check whether the response contains the correct challenge.
Update the subscription to ACTIVE, or schedule another attempt.

Worker claims a subscription

PostgreSQL transaction 1
Select one due PENDING subscription using FOR UPDATE SKIP LOCKED.

Worker establishes ownership
Assign a lease expiration, generate a lease token, increment attempts, and prepare the verification challenge.

Transaction commits The worker releases the database lock.

Worker communicates with customer
Sends the HTTP challenge outside the database transaction. The request has a timeout and SSRF protections.

Worker records the result
Success

Set ACTIVE, record verified_at, clear temporary verification state.
Failure

Set the next retry time, release the lease or mark FAILED when attempts are exhausted.
"""
import time
from geospatial.models import WebhookSubscription, WebhookStatus
from geospatial.database import SessionLocal

from sqlalchemy import select, update, func
from sqlalchemy.orm import Session
from uuid import UUID

def process_next_verification():
    verification = claim_next_verification()

    if verification is None:
        return False

    try:
        response = send_verification_challenge(verification)
        verify_challenge_response(response, verification)
        mark_subscription_verified(verification)
    except Exception as error:
        schedule_verification_retry(verification, error)

    return True



def claim_next_verification():
    pass

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