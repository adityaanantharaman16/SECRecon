from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine


@contextmanager
def transaction(target: Engine | Connection) -> Iterator[Connection]:
    """Reuse a caller-owned transaction; otherwise establish one at the service boundary."""
    if isinstance(target, Connection):
        yield target
    else:
        with target.begin() as connection:
            yield connection
