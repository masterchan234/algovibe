"""
shared/pid_lock.py — Single-Instance PID Lock
==============================================
Prevents multiple bot instances from running against the same database.
On start, writes the current PID to a lock file.  If the file already
exists and the recorded PID still refers to a live process, startup is
aborted.

Usage:
    lock = PidLock()           # defaults to data/algovibe_bot.pid
    lock.acquire()             # raises RuntimeError if already running
    ...
    lock.release()             # clean exit
    # or use as context manager:
    with PidLock():
        run_bot()
"""

import logging
import os
import signal

logger = logging.getLogger(__name__)

_DEFAULT_PID_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "algovibe_bot.pid",
)


class PidLock:
    def __init__(self, pid_file: str = _DEFAULT_PID_FILE):
        self._pid_file = pid_file
        self._acquired = False

    #  Public API 

    def acquire(self) -> None:
        """
        Write PID to lock file.

        Raises
        ------
        RuntimeError
            If another instance is already running (stale PID files are
            cleaned up automatically).
        """
        os.makedirs(os.path.dirname(self._pid_file), exist_ok=True)

        existing_pid = self._read_pid()
        if existing_pid is not None:
            if self._pid_alive(existing_pid):
                raise RuntimeError(
                    f"AlgoVibe bot is already running (PID {existing_pid}). "
                    f"Lock file: {self._pid_file}"
                )
            logger.warning(
                f"[PidLock] Stale PID file found (PID {existing_pid} is dead). "
                "Removing and continuing."
            )
            self._remove()

        with open(self._pid_file, "w") as fh:
            fh.write(str(os.getpid()))
        self._acquired = True
        logger.info(f"[PidLock] Acquired (PID {os.getpid()}) → {self._pid_file}")

    def release(self) -> None:
        """Remove the PID file."""
        if self._acquired:
            self._remove()
            self._acquired = False
            logger.info(f"[PidLock] Released → {self._pid_file}")

    #  Context manager 

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()

    #  Helpers 

    def _read_pid(self) -> "int | None":
        try:
            with open(self._pid_file) as fh:
                return int(fh.read().strip())
        except (FileNotFoundError, ValueError):
            return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Return True if a process with *pid* is currently running."""
        try:
            os.kill(pid, 0)  # signal 0 = existence check, no signal sent
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _remove(self) -> None:
        try:
            os.remove(self._pid_file)
        except FileNotFoundError:
            pass
