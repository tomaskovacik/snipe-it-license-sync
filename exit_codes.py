"""Shared Nagios/monitoring-plugin exit codes
(https://nagios-plugins.org/doc/guidelines.html), plus a common wrapper so
every script in this project classifies failures the same way:

  0 OK       - ran successfully
  1 WARNING  - ran successfully but found something worth a human's
               attention: a license diff (snipe_it_sync.py), or a fetch
               script that completed but couldn't resolve every user's
               email (atlassian_licenses.py, bitbucket_licenses.py)
  2 CRITICAL - couldn't reach a required host (network/VPN down, DNS
               failure, timeout)
  3 UNKNOWN  - not properly configured (missing .env variable) or an
               unexpected error
"""

from __future__ import annotations

import sys
from typing import Callable, Optional

import requests

EXIT_OK = 0
EXIT_WARNING = 1
EXIT_CRITICAL = 2
EXIT_UNKNOWN = 3


def run(main_func: Callable[[], Optional[int]]) -> None:
    """Call main_func(), classify any exception into the exit codes above,
    print a one-line status prefix, and sys.exit with that code.

    main_func may return an exit code to report a non-OK outcome that isn't
    an exception (e.g. EXIT_WARNING when a fetch finished but some emails
    couldn't be resolved); returning None means EXIT_OK."""
    try:
        code = main_func()
    except KeyError as e:
        print(f"UNKNOWN: missing required .env variable: {e}")
        sys.exit(EXIT_UNKNOWN)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        print(f"CRITICAL: required host not reachable: {e}")
        sys.exit(EXIT_CRITICAL)
    except Exception as e:
        print(f"UNKNOWN: {e}")
        sys.exit(EXIT_UNKNOWN)
    sys.exit(EXIT_OK if code is None else code)
