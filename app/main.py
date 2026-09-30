"""巡小站：管理多个目标，并手动检查它的 HTTP 响应。"""
import asyncio
import logging
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SQLite 操作放到工作线程，不在事件循环里等待磁盘。
    await asyncio.to_thread(database.initialize)
    app.state.target_locks = {}
    async with httpx.AsyncClient(
        follow_redirects=True, max_redirects=5, timeout=5, trust_env=False
    ) as client:
        app.state.http_client = client
        yield


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
        return await asyncio.to_thread(database.save_target, target_id, target.name, target.url)


@app.patch("/api/targets/{target_id}/state")
async def update_pause(target_id: int, state: PauseInput):
    lock = await lock_for(target_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="该目标正在检查或保存，请完成后再修改暂停状态")
    async with lock:
        return await asyncio.to_thread(database.set_paused, target_id, state.paused)


@app.post("/api/targets/{target_id}/check")
async def check_target(target_id: int):
    lock = await lock_for(target_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="该目标正在检查或保存，请稍后再试")

    async with lock:
        target = await asyncio.to_thread(database.get_target, target_id)
        if target["paused"]:
            raise HTTPException(status_code=409, detail="目标已暂停，请先恢复巡检")
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

        return {
            "target_id": target["id"],
            "target_name": target["name"],
            "target_url": target["url"],
            "status": "up" if error is None else "down",
            "status_code": status_code,
            "response_ms": elapsed_ms,
            "checked_at": checked_at,
            "error": error,
        }
