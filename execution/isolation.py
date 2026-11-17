"""Fail-closed policy for executing untrusted project code.

This module deliberately does not infer isolation from environment variables,
containers, command allowlists or a scrubbed environment. A future implementation
must delegate execution to an independently verified, isolated worker; the
current service has no such worker and therefore never enables host execution.
"""


def isolated_worker_available() -> bool:
    return False


BLOCKED_MESSAGE = "Untrusted project execution is blocked: a verified isolated worker is required"
