"""文件锁：跨线程与跨进程的互斥访问（移植自上游 TradingAgents dataflows/files.py）。"""

import errno
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


@contextmanager
def locked(path, wait: bool = True) -> Iterator[bool]:
    """同一时刻只允许一个写入者持有 ``path``（跨线程与跨进程）。

    锁落在 ``path`` 旁边的 ``.lock`` 文件上。yield 是否拿到锁：
    ``wait=False`` 时他人持锁则不等待，直接 yield False。
    """
    with open(f"{path}.lock", "a+b") as handle:
        hold = _hold_windows if os.name == "nt" else _hold_posix
        with hold(handle, wait) as acquired:
            yield acquired


@contextmanager
def _hold_posix(handle, wait: bool = True) -> Iterator[bool]:
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _hold_windows(handle, wait: bool = True) -> Iterator[bool]:
    import msvcrt

    handle.seek(0)
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
            break
        except OSError as exc:
            if not wait and exc.errno in (errno.EACCES, errno.EDEADLK):
                yield False  # 锁被他人持有
                return
            # LK_LOCK 在他人持锁约 10 秒后放弃；其余错误不是等待
            if exc.errno != errno.EDEADLK:
                raise
    try:
        yield True
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def replace_file(path, write: Callable[[str], None]) -> None:
    """通过旁边唯一命名的临时文件写入 ``path``，再原子移入。

    读者只会看到旧文件或新文件，绝不会读到写了一半的内容。
    """
    import uuid

    path = Path(path)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        write(str(temp))
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
