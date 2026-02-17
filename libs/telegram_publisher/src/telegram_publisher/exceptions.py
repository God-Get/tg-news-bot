"""Publisher exceptions."""

from __future__ import annotations


class PublisherError(Exception):
    pass


class PublisherEditNotAllowed(PublisherError):
    pass


class PublisherNotFound(PublisherError):
    pass


class PublisherNotModified(PublisherError):
    pass
