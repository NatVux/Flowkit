"""Structured logging safety tests."""

import json
import logging

from agent.logging_utils import log_event, redact


def test_redact_removes_sensitive_nested_fields():
    value = redact({
        "request_id": "req-1",
        "authorization": "Bearer secret",
        "nested": {"api_key": "secret-key", "safe": "ok"},
        "refresh_token": "refresh-secret",
    })
    assert value["request_id"] == "req-1"
    assert value["authorization"] == "[REDACTED]"
    assert value["nested"]["api_key"] == "[REDACTED]"
    assert value["refresh_token"] == "[REDACTED]"
    assert value["nested"]["safe"] == "ok"


def test_log_event_emits_json_with_correlation_fields(caplog):
    logger = logging.getLogger("flowkit.test.logging")
    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(logger, logging.INFO, "job_started", request_id="req-1", project_id="proj-1", token="hidden")
    payload = json.loads(caplog.records[-1].message)
    assert payload["event"] == "job_started"
    assert payload["request_id"] == "req-1"
    assert payload["project_id"] == "proj-1"
    assert payload["token"] == "[REDACTED]"
