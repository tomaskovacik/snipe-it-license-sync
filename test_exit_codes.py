"""Tests for exit_codes.run() — in particular that a plain int return from
main_func becomes the process exit code (the mechanism that lets a fetch
script report WARNING for unresolved emails instead of a silent OK)."""

import pytest
import requests

from exit_codes import (
    EXIT_CRITICAL,
    EXIT_OK,
    EXIT_UNKNOWN,
    EXIT_WARNING,
    run,
)


def _run_code(main_func) -> int:
    with pytest.raises(SystemExit) as exc:
        run(main_func)
    return exc.value.code


def test_none_return_is_ok():
    assert _run_code(lambda: None) == EXIT_OK


def test_int_return_is_used_verbatim():
    assert _run_code(lambda: EXIT_WARNING) == EXIT_WARNING


def test_explicit_ok_return():
    assert _run_code(lambda: EXIT_OK) == EXIT_OK


def test_keyerror_is_unknown():
    def main():
        raise KeyError("MS_TENANT_ID")

    assert _run_code(main) == EXIT_UNKNOWN


def test_connection_error_is_critical():
    def main():
        raise requests.exceptions.ConnectionError("no route to host")

    assert _run_code(main) == EXIT_CRITICAL


def test_unexpected_exception_is_unknown():
    def main():
        raise ValueError("boom")

    assert _run_code(main) == EXIT_UNKNOWN
