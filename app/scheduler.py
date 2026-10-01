"""少量目标的异步定时循环；串行检查到期目标，不积压补跑任务。"""
import asyncio
import logging
from time import monotonic

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, interval, list_targets, check, *, clock=monotonic, poll_seconds=1):
        self.interval = interval
        self.list_targets = list_targets
        self.check = check
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.deadlines = {}
        self.stop = asyncio.Event()

    def defer(self, target_id):
        self.deadlines[target_id] = self.clock() + self.interval

    def is_due(self, target_id):
        return self.clock() >= self.deadlines.get(target_id, float("inf"))

    async def run_once(self):
        targets = await self.list_targets()
        existing = {target["id"] for target in targets}
        for target_id in list(self.deadlines):
            if target_id not in existing:
                self.deadlines.pop(target_id, None)
        for target in targets:
            if self.stop.is_set():
                break
            target_id = target["id"]
            if target["paused"] or target_id not in self.deadlines:
                self.defer(target_id)
                continue
            if not self.is_due(target_id):
                continue
            try:
                await self.check(target_id)
            except Exception:
                # 单个目标或数据库报错不终止后续巡检；下个间隔再试。
                logger.exception("Scheduled check failed for target %s", target_id)
            finally:
                self.defer(target_id)

    async def run(self):
        while not self.stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Unable to read targets for scheduler")
            try:
                async with asyncio.timeout(self.poll_seconds):
                    await self.stop.wait()
            except TimeoutError:
                pass
