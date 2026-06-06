"""Typed ingestion errors.

`DataUnavailable` means the source was reachable but the datum is genuinely
absent (e.g. no CIK for a ticker). `SourceFailure` means a transport/rate-limit
error. The dispatcher uses the distinction to decide whether a missing source is
a recorded gap (both cases here) versus something a caller might retry.
"""

from __future__ import annotations


class IngestionError(RuntimeError):
    """Base class for all ingestion failures."""


class DataUnavailable(IngestionError):
    """The source responded but the requested datum does not exist."""


class SourceFailure(IngestionError):
    """A network, rate-limit, or transport error reaching the source."""
