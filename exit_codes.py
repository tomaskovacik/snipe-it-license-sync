"""Shared Nagios/monitoring-plugin exit codes
(https://nagios-plugins.org/doc/guidelines.html), plus a common wrapper so
every script in this project classifies failures the same way:

  0 OK       - ran successfully
  1 WARNING  - ran successfully but found something worth a human's
               attention (only snipe_it_sync.py uses this, for a license
               diff — the fetch scripts don't have a "warning" state)
  2 CRITICAL - couldn't reach a required host (network/VPN down, DNS
               failure, timeout)
  3 UNKNOWN  - not properly configured (missing .env variable) or an
               unexpected error
"""

from __future__ import annotations

import sys
from typing import Callable

import requests

EXIT_OK = 0
EXIT_WARNING = 1
EXIT_CRITICAL = 2
EXIT_UNKNOWN = 3


def run(main_func: Callable[[], None]) -> None:
    """Call main_func(), classify any exception into the exit codes above,
    print a one-line status prefix, and sys.exit with that code."""
    try:
        main_func()
    except KeyError as e:
        print(f"UNKNOWN: missing required .env variable: {e}")
        sys.exit(EXIT_UNKNOWN)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        print(f"CRITICAL: required host not reachable: {e}")
        sys.exit(EXIT_CRITICAL)
    except Exception as e:
        print(f"UNKNOWN: {e}")
        sys.exit(EXIT_UNKNOWN)
    sys.exit(EXIT_OK)
