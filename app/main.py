"""巡小站第一课：只检查一个固定的本机测试网站。"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

TARGET_URL = "http://127.0.0.1:8765/"
CHECK_TIMEOUT_SECONDS = 10


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 单进程内共用一把锁，防止重复点击产生重叠请求。
    app.state.check_lock = asyncio.Lock()
    async with httpx.AsyncClient(
        follow_redirects=True, max_redirects=5, timeout=5, trust_env=False
    ) as client:
        app.state.http_client = client
        yield


app = FastAPI(title="巡小站", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/")
async def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/health")
async def health():
    # 这里只表示巡小站后端正在工作，不代表目标网站正常。
    return {"status": "ok"}


@app.post("/api/check")
async def check_target():
    if app.state.check_lock.locked():
        raise HTTPException(status_code=409, detail="已有检查正在执行，请稍后再试。")

    async with app.state.check_lock:
        checked_at = datetime.now(timezone.utc).isoformat()
        started = perf_counter()
        status_code = None
        error = None
        try:
            # 总时限覆盖连接、等待和全部重定向。
            async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
                # 只读取最终响应头，避免下载整个网页。
                async with app.state.http_client.stream("GET", TARGET_URL) as response:
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
            "target_url": TARGET_URL,
            "status": "up" if error is None else "down",
            "status_code": status_code,
            "response_ms": elapsed_ms,
            "checked_at": checked_at,
            "error": error,
        }
