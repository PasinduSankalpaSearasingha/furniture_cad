"""Bounded, serial background execution for a single-process local workspace."""
from concurrent.futures import ThreadPoolExecutor
from threading import Condition, Lock
import logging

logger = logging.getLogger('furniture.local_executor')
_creation_lock = Lock()


class LocalQueueFull(RuntimeError):
    pass


class LocalExecutor:
    def __init__(self, capacity=32):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='furniture-local')
        self.condition = Condition()
        self.capacity, self.pending, self.closed = capacity, 0, False

    def submit(self, function, *args):
        with self.condition:
            if self.closed: raise RuntimeError('Local executor is stopping')
            if self.pending >= self.capacity: raise LocalQueueFull('Local queue is full; retry after current work finishes')
            self.pending += 1
            try: return self.pool.submit(self._run, function, args)
            except Exception:
                self.pending -= 1
                raise

    def _run(self, function, args):
        try: return function(*args)
        except Exception:
            logger.exception('local_task_failed',extra={'task':function.__name__})
            raise
        finally:
            with self.condition:
                self.pending -= 1
                self.condition.notify_all()

    def shutdown(self):
        # Running CAD can enqueue its final AI review, so drain descendants too.
        with self.condition:
            self.condition.wait_for(lambda: self.pending == 0)
            self.closed = True
        self.pool.shutdown(wait=True)


def submit_local(config, function, *args):
    with _creation_lock:
        if config._local_executor is None: config._local_executor = LocalExecutor()
        executor = config._local_executor
    return executor.submit(function, *args)


def submit_render(config,function,*args):
    with _creation_lock:
        if config._render_executor is None: config._render_executor=LocalExecutor(capacity=4)
        executor=config._render_executor
    return executor.submit(function,*args)
