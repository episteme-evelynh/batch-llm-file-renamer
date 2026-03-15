"""QtConcurrent-style high-level concurrency utilities.

Provides QRunnable-based task wrappers and a MapRunner that applies a
function to a sequence of items on a QThreadPool — replacing low-level
QThread subclassing with the higher-level map/run pattern.

Usage:

    # Run a single function in the background
    runner = TaskRunner()
    runner.signals.result.connect(on_done)
    runner.run(my_function, arg1, arg2)

    # Map a function over items (like QtConcurrent::mapped)
    mapper = MapRunner()
    mapper.signals.item_result.connect(on_each)
    mapper.signals.all_finished.connect(on_done)
    mapper.map(process_file, file_list)
"""

import logging
import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared signal carriers (QRunnable can't inherit QObject)
# ---------------------------------------------------------------------------

class TaskSignals(QObject):
    """Signals emitted by a single Task.

    result(object)   — the return value of the callable
    error(str)       — stringified exception on failure
    progress(object) — arbitrary progress data (status text, percentage, etc.)
    finished()       — always emitted, even on error
    """

    result = Signal(object)
    error = Signal(str)
    progress = Signal(object)
    finished = Signal()


class MapSignals(QObject):
    """Signals emitted by a MapRunner across all items.

    item_result(int, object) — (index, result) per item
    item_error(int, str)     — (index, error) per item
    progress(object)         — forwarded from individual tasks
    all_finished(int, int)   — (success_count, error_count)
    """

    item_result = Signal(int, object)
    item_error = Signal(int, str)
    progress = Signal(object)
    all_finished = Signal(int, int)


# ---------------------------------------------------------------------------
# QRunnable wrappers
# ---------------------------------------------------------------------------

class Task(QRunnable):
    """Run a callable on QThreadPool, emitting results via signals.

    The callable receives an optional ``cancel_event`` keyword argument
    (a ``threading.Event``) so it can check for cancellation, and an
    optional ``progress_callback`` for emitting progress updates.

    Example::

        task = Task(extract_and_summarize, filepath)
        task.signals.result.connect(handle_result)
        QThreadPool.globalInstance().start(task)
    """

    def __init__(self, fn, *args, cancel_event=None, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = TaskSignals()
        self.cancel_event = cancel_event or threading.Event()
        self.setAutoDelete(True)

    @Slot()
    def run(self):
        try:
            result = self.fn(
                *self.args,
                **self.kwargs,
                cancel_event=self.cancel_event,
                progress_callback=self.signals.progress.emit,
            )
            self.signals.result.emit(result)
        except Exception as exc:
            logger.exception("Task failed: %s", self.fn.__name__)
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


# ---------------------------------------------------------------------------
# High-level runners
# ---------------------------------------------------------------------------

class TaskRunner(QObject):
    """Run a single function in the thread pool (like QtConcurrent::run).

    Wraps QThreadPool.start() with signal-based result delivery.

    Example::

        runner = TaskRunner(parent=self)
        runner.signals.result.connect(self._on_scan_done)
        runner.signals.error.connect(self._on_scan_error)
        runner.run(scan_directory, root_dir)
    """

    def __init__(self, pool=None, parent=None):
        super().__init__(parent)
        self._pool = pool or QThreadPool.globalInstance()
        self._cancel = threading.Event()
        self._task = None
        self.signals = TaskSignals()

    def run(self, fn, *args, **kwargs):
        """Submit *fn* to the thread pool."""
        self._cancel.clear()
        task = Task(fn, *args, cancel_event=self._cancel, **kwargs)

        # Forward task signals to our own
        task.signals.result.connect(self.signals.result.emit)
        task.signals.error.connect(self.signals.error.emit)
        task.signals.progress.connect(self.signals.progress.emit)
        task.signals.finished.connect(self.signals.finished.emit)

        self._task = task
        self._pool.start(task)

    def cancel(self):
        """Request cancellation (cooperative — the function must check)."""
        self._cancel.set()

    @property
    def is_cancelled(self):
        return self._cancel.is_set()


class MapRunner(QObject):
    """Apply a function to each item in a sequence (like QtConcurrent::mapped).

    Items are processed on a QThreadPool. By default ``max_threads=1``
    for sequential processing (preserving order and allowing blocking
    error dialogs). Increase for I/O-bound parallel workloads.

    The mapped function signature must accept:
        fn(item, *, cancel_event, progress_callback) -> result

    Example::

        mapper = MapRunner(parent=self)
        mapper.signals.item_result.connect(self._on_file_done)
        mapper.signals.all_finished.connect(self._on_all_done)
        mapper.map(process_file, file_list)
    """

    def __init__(self, max_threads=1, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max_threads)
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._success = 0
        self._errors = 0
        self._pending = 0
        self.signals = MapSignals()

    def map(self, fn, items, **shared_kwargs):
        """Submit one task per item.

        *shared_kwargs* are passed to every invocation of *fn*
        alongside the item (e.g., model config, API URLs).
        """
        self._cancel.clear()
        self._success = 0
        self._errors = 0
        self._pending = len(items)

        if not items:
            self.signals.all_finished.emit(0, 0)
            return

        for idx, item in enumerate(items):
            task = Task(fn, item, cancel_event=self._cancel, **shared_kwargs)
            task.signals.result.connect(
                lambda r, i=idx: self._on_item_result(i, r)
            )
            task.signals.error.connect(
                lambda e, i=idx: self._on_item_error(i, e)
            )
            task.signals.progress.connect(self.signals.progress.emit)
            self._pool.start(task)

    def cancel(self):
        """Request cancellation of all pending tasks."""
        self._cancel.set()
        self._pool.clear()  # Remove queued (not yet started) tasks

    def wait(self, msecs=3000):
        """Wait for running tasks to finish (for cleanup on close)."""
        self._pool.waitForDone(msecs)

    @property
    def is_cancelled(self):
        return self._cancel.is_set()

    def _on_item_result(self, index, result):
        self.signals.item_result.emit(index, result)
        self._check_done(success=True)

    def _on_item_error(self, index, error_msg):
        self.signals.item_error.emit(index, error_msg)
        self._check_done(success=False)

    def _check_done(self, success: bool):
        with self._lock:
            if success:
                self._success += 1
            else:
                self._errors += 1
            remaining = self._pending - self._success - self._errors

        if remaining <= 0:
            self.signals.all_finished.emit(self._success, self._errors)
