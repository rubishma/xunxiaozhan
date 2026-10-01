"""巡小站：管理多个目标，并手动检查它的 HTTP 响应。"""
import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app import database
from app.process_lock import single_instance
from app.scheduler import Scheduler

CHECK_TIMEOUT_SECONDS = 10
logger = logging.getLogger(__name__)


class TargetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        if not value.lower().startswith(("http://", "https://")) or any(c.isspace() for c in value):
            raise ValueError("请输入完整的 HTTP/HTTPS 网址，网址中不能包含空白")
        try:
            parsed = HttpUrl(value)
        except ValueError:
            raise ValueError("网址格式无效，请检查主机名和端口") from None
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("网址中不能包含账号或密码")
        if parsed.fragment is not None:
            raise ValueError("请去掉网址中的 # 片段")
        return str(parsed)


class PauseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: bool = Field(strict=True)


def get_check_interval():
    value = os.environ.get("XUN_CHECK_INTERVAL_SECONDS", "60")
    try:
        interval = int(value)
    except ValueError:
        raise RuntimeError("XUN_CHECK_INTERVAL_SECONDS 必须是 30–86400 的整数") from None
    if not 30 <= interval <= 86400:
        raise RuntimeError("XUN_CHECK_INTERVAL_SECONDS 必须是 30–86400 的整数")
    return interval


async def scheduled_targets():
    return await asyncio.to_thread(database.list_targets)


async def scheduled_check(target_id):
    try:
        await perform_check(target_id, source="scheduled")
    except database.TargetNotFound:
        pass  # 读取列表后目标可能已被删除。
    except HTTPException as exc:
        if exc.status_code not in (404, 409):
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    interval = get_check_interval()
    # 文件锁持续覆盖数据库初始化、HTTP 客户端与整个调度生命周期。
    with single_instance(database.DB_PATH):
        await asyncio.to_thread(database.initialize)
        app.state.target_locks = {}
        async with httpx.AsyncClient(
            follow_redirects=True, max_redirects=5, timeout=5, trust_env=False
        ) as client:
            app.state.http_client = client
            app.state.scheduler = Scheduler(interval, scheduled_targets, scheduled_check)
            task = asyncio.create_task(app.state.scheduler.run(), name="inspection-scheduler")
            app.state.scheduler_task = task
            logging.getLogger("uvicorn.error").info("Scheduler started: interval=%ss, single process", interval)
            try:
                yield
            finally:
                # 不取消正在执行的检查，等它在超时范围内完成保存，再关闭客户端和释放进程锁。
                app.state.scheduler.stop.set()
                await task
                logging.getLogger("uvicorn.error").info("Scheduler stopped")


app = FastAPI(title="巡小站", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.exception_handler(sqlite3.Error)
async def database_error(request: Request, exc: sqlite3.Error):
    logger.error("SQLite operation failed", exc_info=(type(exc), exc, exc.__traceback__))
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请查看后端终端日志"})


@app.get("/")
async def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/health")
async def health():
    # 只表示巡小站接口可响应，不代表目标网站或数据库健康。
    return {"status": "ok"}


@app.get("/api/scheduler")
async def scheduler_status():
    return {
        "running": not app.state.scheduler_task.done() and not app.state.scheduler.stop.is_set(),
        "interval_seconds": app.state.scheduler.interval,
    }


@app.exception_handler(database.TargetNotFound)
async def missing_target(request: Request, exc: database.TargetNotFound):
    return JSONResponse(status_code=404, content={"detail": "目标不存在，请刷新目标列表"})


@app.exception_handler(database.DuplicateTarget)
async def duplicate_target(request: Request, exc: database.DuplicateTarget):
    return JSONResponse(status_code=409, content={"detail": "该网址已在目标列表中，请选择已有目标"})


@app.get("/api/targets")
def read_targets():
    return database.list_targets()


@app.post("/api/targets", status_code=201)
def add_target(target: TargetInput):
    # 保存配置不执行网站请求。
    return database.create_target(target.name, target.url)


@app.get("/api/targets/{target_id}")
def read_target(target_id: int):
    return database.get_target(target_id)


@app.get("/api/targets/{target_id}/history")
def read_history(target_id: int):
    return database.list_checks(target_id)


async def lock_for(target_id):
    # 先确认目标存在，避免给不存在的编号创建锁。
    await asyncio.to_thread(database.get_target, target_id)
    return app.state.target_locks.setdefault(target_id, asyncio.Lock())


@app.put("/api/targets/{target_id}")
async def update_target(target_id: int, target: TargetInput):
    lock = await lock_for(target_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="该目标正在检查或保存，请稍后再试")
    async with lock:
        saved = await asyncio.to_thread(database.save_target, target_id, target.name, target.url)
        app.state.scheduler.defer(target_id)
        return saved


@app.patch("/api/targets/{target_id}/state")
async def update_pause(target_id: int, state: PauseInput):
    lock = await lock_for(target_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="该目标正在检查或保存，请完成后再修改暂停状态")
    async with lock:
        saved = await asyncio.to_thread(database.set_paused, target_id, state.paused)
        app.state.scheduler.defer(target_id)
        return saved


@app.delete("/api/targets/{target_id}")
async def remove_target(target_id: int):
    lock = await lock_for(target_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="该目标正在检查或保存，请完成后再删除")
    async with lock:
        await asyncio.to_thread(database.delete_target, target_id)
    # 锁保留到进程结束，避免已取得旧锁的请求与新锁并存。
    return {"deleted_id": target_id}


@app.post("/api/targets/{target_id}/check")
async def check_target(target_id: int):
    return await perform_check(target_id, source="manual")


async def perform_check(target_id, source):
    lock = await lock_for(target_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="该目标正在检查或保存，请稍后再试")

    async with lock:
        target = await asyncio.to_thread(database.get_target, target_id)
        if target["paused"]:
            raise HTTPException(status_code=409, detail="目标已暂停，请先恢复巡检")
        # 手动检查可能刚刚完成；取得锁后再次确认定时任务仍然到期。
        if source == "scheduled" and not app.state.scheduler.is_due(target_id):
            return None
        try:
            return await inspect_and_save(target, source)
        finally:
            app.state.scheduler.defer(target_id)


async def inspect_and_save(target, source):
    target_id = target["id"]
    checked_at = datetime.now(timezone.utc).isoformat()
    started = perf_counter()
    status_code = None
    error = None
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            # 只读取最终响应头，避免下载整个网页。
            async with app.state.http_client.stream("GET", target["url"]) as response:
                status_code = response.status_code
                elapsed_ms = round((perf_counter() - started) * 1000, 1)
        if not 200 <= status_code < 300:
            error = f"目标返回 HTTP {status_code}"
    except (TimeoutError, httpx.TimeoutException):
        error = "请求超时"
    except httpx.TooManyRedirects:
        error = "重定向次数超过 5 次"
    except httpx.ConnectError:
        error = "无法建立连接，请检查目标服务、地址及 HTTPS 证书"
    except httpx.RequestError:
        error = "网络请求失败，请检查目标服务与网络"
    except httpx.InvalidURL:
        error = "目标 URL 无效"

    if status_code is None:
        elapsed_ms = round((perf_counter() - started) * 1000, 1)

    result = {
        "source": source,
        "target_id": target["id"],
        "target_name": target["name"],
        "target_url": target["url"],
        "status": "up" if error is None else "down",
        "status_code": status_code,
        "response_ms": elapsed_ms,
        "checked_at": checked_at,
        "error": error,
    }
    try:
        return await asyncio.to_thread(database.save_check, result)
    except sqlite3.Error:
        logger.exception("Failed to save check history for target %s", target_id)
        raise HTTPException(status_code=503, detail="检查已执行，但历史保存失败，请查看后端终端日志") from None
