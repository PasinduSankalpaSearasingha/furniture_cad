"""Content-addressed, bounded caches. Never cache mutable OCCT objects globally."""
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import threading
from zipfile import BadZipFile
import numpy as np

VERSION = 'mesh-cad-v1'


def content_hash(*values):
    digest = hashlib.sha256(VERSION.encode())
    for value in values:
        if isinstance(value, np.ndarray):
            digest.update(str((value.shape, value.dtype.str)).encode())
            digest.update(np.ascontiguousarray(value).tobytes())
        elif isinstance(value, bytes):
            digest.update(value)
        else:
            digest.update(json.dumps(value, sort_keys=True, allow_nan=False).encode())
    return digest.hexdigest()


class GeometryCache:
    def __init__(self, max_bytes=256 * 1024**2, directory=None):
        self.max_bytes, self.used = max_bytes, 0
        self.values = OrderedDict()
        self.lock = threading.RLock()
        self.directory = Path(directory) if directory else None
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)

    def get(self, key):
        with self.lock:
            item = self.values.get(key)
            if item is None:
                return None
            self.values.move_to_end(key)
            return item[0]

    def put(self, key, value, size=None):
        size = size if size is not None else getattr(value, 'nbytes', 1024)
        with self.lock:
            if key in self.values:
                self.used -= self.values.pop(key)[1]
            if size > self.max_bytes:
                return value
            while self.values and self.used + size > self.max_bytes:
                self.used -= self.values.popitem(last=False)[1][1]
            self.values[key] = value, size
            self.used += size
        return value

    def save_arrays(self, key, **arrays):
        if not self.directory:
            return
        target = self.directory / (key + '.npz')
        # A unique temporary file avoids corrupting concurrent cache writers.
        import tempfile
        with tempfile.NamedTemporaryFile(dir=self.directory, suffix='.npz', delete=False) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(stream, **arrays)
        if temporary.stat().st_size > self.max_bytes:
            temporary.unlink(missing_ok=True)
            return
        files = sorted((path for path in self.directory.glob('*.npz') if path not in (temporary, target)),
                       key=lambda path: path.stat().st_mtime)
        disk_bytes = sum(path.stat().st_size for path in files) + temporary.stat().st_size
        for path in files:
            if disk_bytes <= self.max_bytes:
                break
            disk_bytes -= path.stat().st_size
            path.unlink(missing_ok=True)
        temporary.replace(target)

    def load_arrays(self, key):
        if not self.directory:
            return None
        try:
            with np.load(self.directory / (key + '.npz'), allow_pickle=False) as data:
                return {name: data[name] for name in data.files}
        except (OSError, ValueError, EOFError, BadZipFile):
            return None


class ResourceBudget:
    def __init__(self, memory_mb=2048, max_points=1_000_000, workers=4):
        if memory_mb < 8 or not 1 <= workers <= 32 or not 100 <= max_points <= 1_000_000:
            raise ValueError('Invalid geometry resource budget')
        self.memory_mb, self.max_points, self.workers = memory_mb, max_points, workers

    def sample_count(self, requested, bytes_per_point=320):
        import psutil
        available = min(self.memory_mb * 1024**2, psutil.virtual_memory().available * .5)
        count = min(requested, self.max_points, int(available // bytes_per_point))
        if count < 100:
            raise MemoryError('Insufficient memory for even 100 surface samples')
        return count

    def snapshot(self):
        import psutil
        process = psutil.Process()
        return dict(rss_mb=process.memory_info().rss / 1024**2,
                    available_mb=psutil.virtual_memory().available / 1024**2,
                    cpu_seconds=sum(process.cpu_times()[:2]))
