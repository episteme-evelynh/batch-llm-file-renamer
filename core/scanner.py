"""Recursive PDF/EPUB file scanner using QThreadPool.

Replaces the old QThread subclass with a plain function that runs on
the global QThreadPool via ``TaskRunner``.  The function accepts
``cancel_event`` and ``progress_callback`` from the concurrency layer,
so it can be cancelled cooperatively and emit live progress.
"""

import os

SUPPORTED_EXTENSIONS = {".pdf", ".epub"}


def scan_directory(root_dir, *, cancel_event=None, progress_callback=None):
    """Walk *root_dir* and return a list of PDF/EPUB file paths.

    This is a plain function (no QObject inheritance needed).  It is
    designed to be submitted to ``TaskRunner.run()`` which supplies
    the ``cancel_event`` and ``progress_callback`` keyword arguments.

    Progress updates are dicts with keys:
        file_found (str)  — full path of the file just discovered
        count (int)       — running total of files found so far
    """
    found_files = []
    count = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        if cancel_event and cancel_event.is_set():
            break

        # Skip hidden directories (e.g., .git, .Spotlight-V100)
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        for filename in filenames:
            if cancel_event and cancel_event.is_set():
                break

            _, ext = os.path.splitext(filename)
            if ext.lower() in SUPPORTED_EXTENSIONS:
                full_path = os.path.join(dirpath, filename)
                found_files.append(full_path)
                count += 1
                if progress_callback:
                    progress_callback({
                        "file_found": full_path,
                        "count": count,
                    })

    return found_files
