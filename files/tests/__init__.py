try:
    from .user_utils import create_account  # noqa: F401
except ImportError:  # pragma: no cover - optional test helpers
    create_account = None
