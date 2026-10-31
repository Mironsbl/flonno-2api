import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Union
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
from pydantic import BaseModel, Field
import uvicorn

from core.pool import FlonnoKeyPool
from core.minter import FlonnoMinter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("flonno_gateway")

app = FastAPI(
    title="Flonno 2API Autonomous Gateway",
    description="Zero-Cost OpenAI-compatible proxy for Flonno Qwen3.8-27B with key auto-rotation",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pool = FlonnoKeyPool()

# Настройки фонового автоминтинга при исчерпании пула
IMAP_USER = os.getenv("FLONNO_IMAP_USER", "megashopingspbd@gmail.com")
IMAP_PASS = os.getenv("FLONNO_IMAP_PASS", "llmbqphfajyaevbs")
CAPSOLVER_KEY = os.getenv("CAPSOLVER_API_KEY", "")
PROXY_URL = os.getenv("FLONNO_PROXY_URL", None)

minter = FlonnoMinter(
    imap_user=IMAP_USER,
    imap_pass=IMAP_PASS,
    capsolver_key=CAPSOLVER_KEY,
    proxy_url=PROXY_URL
)

class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[Any]]

class ChatCompletionRequest(BaseModel):
    model: str = "qwen3.8-27b"
    messages: List[ChatMessage]
    max_tokens: Optional[int] = 4096
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    stream: Optional[bool] = False
    reasoning_effort: Optional[str] = "none"

@app.get("/health")
async def health():
    return {"status": "ok", "pool_stats": pool.get_stats()}

@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "qwen3.8-27b",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "flonno",
                "permission": [],
                "root": "qwen3.8-27b",
                "parent": None
            }
        ]
    }

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request):
    # Получаем ключ из пула или заголовок авторизации от клиента
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip() if auth_header else ""
    
    # Всегда берем рабочий ключ из пула, если клиент не передал валидный flonno_live_ ключ
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip() if auth_header else ""
    
    if token.startswith("flonno_live_"):
        active_key = token
    else:
        active_key = await pool.get_key()
    
    # Если ключей в пуле нет и задан Capsolver, пробуем сминтить на лету
    if not active_key:
        if CAPSOLVER_KEY:
            logger.info("No keys in pool. Triggering on-demand auto-mint...")
            try:
                new_key, email = await minter.mint_key()
                await pool.add_key(new_key, email=email)
                active_key = new_key
            except Exception as e:
                logger.error(f"On-demand mint failed: {e}")
                raise HTTPException(status_code=503, detail="Key pool depleted and auto-mint failed.")
        else:
            raise HTTPException(status_code=503, detail="Key pool empty. Add keys to keys_pool.json or set CAPSOLVER_API_KEY.")

    target_url = "https://www.flonno.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {active_key}",
        "Content-Type": "application/json"
    }

    # Принудительно отключаем reasoning_effort если не запрошен явно, для максимальной скорости TTFT
    payload = req.model_dump(exclude_none=True)
    if "reasoning_effort" not in payload:
        payload["reasoning_effort"] = "none"
    payload["model"] = "qwen3.8-27b"

    client = httpx.AsyncClient(timeout=180.0)

    # 1. Стриминговый режим (SSE)
    if req.stream:
        async def stream_generator():
            try:
                async with client.stream("POST", target_url, headers=headers, json=payload) as response:
                    if response.status_code in (401, 402):
                        await pool.mark_key_exhausted(active_key)
                        return
                    async for line in response.aiter_lines():
                        if line:
                            yield f"{line}\n\n".encode()
            except Exception as e:
                logger.error(f"Stream error: {e}")
            finally:
                await client.aclose()

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    # 2. Обычный non-streaming режим
    try:
        response = await client.post(target_url, headers=headers, json=payload)
        logger.info(f"Upstream response status: {response.status_code}")
        if response.status_code in (401, 402):
            logger.warning(f"Upstream returned {response.status_code}: {response.text}")
            await pool.mark_key_exhausted(active_key)
            await client.aclose()
            raise HTTPException(status_code=402, detail=f"Flonno key exhausted ({response.status_code}): {response.text}")
        
        await client.aclose()
        # Возвращаем статус и JSON с чистым Content-Type
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type="application/json"
        )
    except httpx.RequestError as e:
        await client.aclose()
        logger.error(f"Upstream Flonno request failed: {e}")
        raise HTTPException(status_code=502, detail=f"Upstream Flonno gateway error: {e}")

@app.post("/pool/mint")
async def trigger_mint():
    """Эндпоинт ручного вызова генерации нового аккаунта с $1 баланса"""
    if not CAPSOLVER_KEY:
        raise HTTPException(status_code=400, detail="CAPSOLVER_API_KEY environment variable is not configured.")
    try:
        new_key, email = await minter.mint_key()
        await pool.add_key(new_key, email=email)
        return {"success": True, "key": new_key, "email": email, "pool_stats": pool.get_stats()}
    except Exception as e:
        logger.error(f"Manual mint failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8097"))
    logger.info(f"Starting Flonno 2API Gateway on port {port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
