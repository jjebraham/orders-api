import json
import logging

from app.context import request_id_var
from app.logging_config import JsonFormatter, RequestIdFilter


def test_json_log_line_includes_request_id_and_extra_fields():
    record = logging.LogRecord("orders_api", logging.INFO, __file__, 1, "order reserved", (), None)
    record.order_id = 7
    token = request_id_var.set("req-xyz")
    try:
        RequestIdFilter().filter(record)
        line = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)

    payload = json.loads(line)
    assert payload["message"] == "order reserved"
    assert payload["level"] == "INFO"
    assert payload["order_id"] == 7
    assert payload["request_id"] == "req-xyz"
