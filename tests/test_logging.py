import logging

from app.utils.logger import HealthCheckFilter


def make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_health_check_filter_drops_health_paths() -> None:
    health_filter = HealthCheckFilter()

    assert health_filter.filter(make_record('127.0.0.1 - "GET /health HTTP/1.1" 200')) is False
    assert health_filter.filter(make_record('127.0.0.1 - "GET /api/health HTTP/1.1" 200')) is False


def test_health_check_filter_keeps_non_health_logs() -> None:
    health_filter = HealthCheckFilter()

    assert health_filter.filter(make_record('127.0.0.1 - "GET /api/subtitles/search" 200'))
