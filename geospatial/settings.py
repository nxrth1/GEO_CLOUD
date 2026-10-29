"""Small environment-backed runtime settings for dataset handling."""
import os

DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
REQUEST_BODY_OVERHEAD_BYTES = 1024 * 1024


def get_max_upload_bytes() -> int:
    raw_value = os.environ.get("GEO_CLOUD_MAX_UPLOAD_BYTES")
    try:
        value = int(raw_value) if raw_value is not None else DEFAULT_MAX_UPLOAD_BYTES
    except ValueError as error:
        raise RuntimeError("GEO_CLOUD_MAX_UPLOAD_BYTES must be a positive integer.") from error
    if value <= 0:
        raise RuntimeError("GEO_CLOUD_MAX_UPLOAD_BYTES must be a positive integer.")
    return value


def get_max_request_body_bytes() -> int:
    # Multipart boundaries/headers add some overhead over the file itself.
    return get_max_upload_bytes() + REQUEST_BODY_OVERHEAD_BYTES
