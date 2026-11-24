from contextvars import ContextVar

# Request ID for the request currently being handled. Read by the logger and by error bodies.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
