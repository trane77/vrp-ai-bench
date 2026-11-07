"""Local JSON publication; no persistence concerns in domain or search code."""
import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path, document, *, overwrite=False):
    """Publish a complete file, without a check-then-overwrite race."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            # Atomic exclusive publication on the same filesystem.
            os.link(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
