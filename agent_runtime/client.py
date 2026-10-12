"""Synchronous facade over cancellable asynchronous SDK requests."""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import threading
from types import SimpleNamespace

from openai import AsyncOpenAI
from .execution import current_task


class CancellableClient:
    def __init__(self, **kwargs):
        self._client = AsyncOpenAI(**kwargs)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **options):
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: self._create(options=options, **kwargs))))

    def _create(self, options=None, **kwargs):
        if self._closed:
            raise RuntimeError('Model client is closed')
        client = self._client.with_options(**(options or {}))
        future = asyncio.run_coroutine_threadsafe(client.chat.completions.create(**kwargs), self._loop)
        task = current_task.get()
        try:
            while True:
                if task:
                    task.check()
                try:
                    return future.result(timeout=0.05)
                except FutureTimeout:
                    if future.done():
                        return future.result()
        except BaseException:
            future.cancel()
            raise

    def close(self):
        if self._closed:
            return
        self._closed = True
        async def shutdown():
            current = asyncio.current_task()
            pending = [task for task in asyncio.all_tasks() if task is not current]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await self._client.close()
        try:
            asyncio.run_coroutine_threadsafe(shutdown(), self._loop).result(timeout=5)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            if not self._thread.is_alive():
                self._loop.close()
