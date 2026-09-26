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


def test_configure_logging_writes_rotating_utf8_file_and_console(tmp_path):
    from logging.handlers import RotatingFileHandler
    from agent.logging_utils import configure_logging

    log_file = tmp_path / "logs" / "server.log"
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        configure_logging(log_file)
        kinds = [type(h) for h in root.handlers]
        assert logging.StreamHandler in kinds
        file_handler = next(h for h in root.handlers if isinstance(h, RotatingFileHandler))
        assert file_handler.encoding == "utf-8"
        assert file_handler.maxBytes > 0 and file_handler.backupCount > 0

        logging.getLogger("flowkit.test.file").warning("Mèo Con — Chợ Đêm")
        file_handler.flush()
        assert "Mèo Con — Chợ Đêm" in log_file.read_text(encoding="utf-8")
    finally:
        for h in root.handlers:
            h.close()
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])


def test_configure_logging_without_file_keeps_console_only():
    from logging.handlers import RotatingFileHandler
    from agent.logging_utils import configure_logging

    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        configure_logging("")
        assert not any(isinstance(h, RotatingFileHandler) for h in root.handlers)
        assert any(type(h) is logging.StreamHandler for h in root.handlers)
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
