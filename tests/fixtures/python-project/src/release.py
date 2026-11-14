"""The one source file of the fixture: clean under pyright, with one import to install.

The project has no pyright config, so the check runs in basic mode on every file.
"""

from packaging.version import Version


def newer(a: str, b: str) -> bool:
    """Whether release ``a`` is newer than release ``b``."""
    return Version(a) > Version(b)


def broken(a: str) -> str:
    """A throwaway fault, to prove the check fails: Version has no such attribute."""
    return Version(a).no_such_attribute()
