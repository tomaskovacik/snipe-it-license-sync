"""Shared QUIET flag: set QUIET=true in .env to suppress the per-user
listing each fetch script prints, keeping just the summary counts and
final status line. Useful for monitoring/Nagios checks where the full
per-user dump is just noise.
"""
import os


def is_quiet() -> bool:
    return os.environ.get("QUIET", "").strip().lower() in ("1", "true", "yes")
