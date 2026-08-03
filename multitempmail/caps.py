"""Provider capability identifiers.

Every provider declares the subset of capabilities it implements as
:attr:`multitempmail.base.BaseProvider.capabilities`. Query them with
``provider.supports(Cap.X)`` / ``mailbox.supports(Cap.X)``; attempting an
unsupported operation raises :class:`multitempmail.errors.UnsupportedFeature`.
"""

__all__ = ["Cap"]


class Cap:
    """Capability identifiers used by the provider capability system."""

    #: can create new mailboxes (``provider.create()``)
    CREATE = "create"
    #: can list messages (``mailbox.messages()``)
    LIST = "list"
    #: can fetch a single full message (``mailbox.message(id)``)
    READ = "read"
    #: can delete individual messages
    DELETE_MESSAGE = "delete_message"
    #: can delete / clear the whole mailbox
    DELETE_MAILBOX = "delete_mailbox"
    #: can mark messages as read
    MARK_READ = "mark_read"
    #: messages carry attachments and ``mailbox.download(...)`` works
    ATTACHMENTS = "attachments"
    #: the provider implements server-side long-poll waiting
    #: (``provider.wait()``); used automatically by the ``wait_*`` helpers
    SERVER_WAIT = "server_wait"
    #: the provider exposes a domains API used to pick a domain on create
    DOMAIN_SELECT = "domain_select"
