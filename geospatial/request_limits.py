"""ASGI request-body limit that rejects oversized bodies before route parsing."""
import json

from .settings import get_max_request_body_bytes


class RequestBodyLimitMiddleware:
    """Bound body reads without buffering the entire upload in middleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        try:
            maximum = get_max_request_body_bytes()
        except RuntimeError as error:
            body = json.dumps({"detail": str(error)}).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 500,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            })
            await send({"type": "http.response.body", "body": body})
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = -1
            if declared_length > maximum:
                await self._send_too_large(send, maximum)
                return

        received = 0
        rejected = False
        response_started = False

        async def limited_receive():
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > maximum:
                    rejected = True
                    if not response_started:
                        await self._send_too_large(send, maximum)
                    return {"type": "http.disconnect"}
            return message

        async def limited_send(message):
            nonlocal response_started
            if rejected:
                return
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            # Some parsers raise when they encounter the synthetic disconnect.
            # Once the middleware has sent a 413, swallow that parser exception;
            # otherwise it could produce a second response from ServerErrorMiddleware.
            if not rejected:
                raise

    @staticmethod
    async def _send_too_large(send, maximum: int):
        body = json.dumps({
            "detail": f"Request body exceeds the configured limit of {maximum} bytes."
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
