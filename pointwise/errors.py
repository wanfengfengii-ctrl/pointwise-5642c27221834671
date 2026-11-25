"""Typed errors, one per failure family in the specification."""


class PointwiseError(Exception):
    """Base class. ``code`` is a stable machine token."""

    code = "error"
    exit_code = 1

    def __init__(self, message, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class ValidationError(PointwiseError):
    """A request that must be refused outright (拒办)."""

    code = "refused"
    exit_code = 2


class MissingError(PointwiseError):
    """An unknown revision / mapping target / report (报缺)."""

    code = "missing"
    exit_code = 3


class ConflictError(PointwiseError):
    """A direction contradiction inside the source (源内方向矛盾)."""

    code = "conflict"
    exit_code = 4


class CorruptError(PointwiseError):
    """A damaged artifact (坏件) or unusable stored record."""

    code = "corrupt"
    exit_code = 5


class DuplicateError(PointwiseError):
    """Same number, different artifact (同号异件) or a duplicate key."""

    code = "duplicate"
    exit_code = 6
