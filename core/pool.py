import asyncio
import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger("flonno_pool")

BASE_URL = "https://www.flonno.com"
CLERK_API = "https://clerk.flonno.com/v1/client"
CLERK_PK = "pk_live_Y2xlcmsuZmxvbm5vLmNvbSQ"
KEYS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "keys_pool.json")

class FlonnoKeyPool:
    def __init__(self, keys_file: str = KEYS_FILE):
        self.keys_file = keys_file
        self.keys: List[Dict[str, Any]] = []
        self._current_index = 0
        self._lock = asyncio.Lock()
        self.load_keys()

    def load_keys(self):
        if os.path.exists(self.keys_file):
            try:
                with open(self.keys_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        self.keys = json.loads(content)
                    else:
                        self.keys = []
                logger.info(f"Loaded {len(self.keys)} Flonno API keys from pool.")
            except Exception as e:
                logger.error(f"Failed to load keys from {self.keys_file}: {e}")
                self.keys = []
        else:
            self.keys = []
            # Не перезаписываем keys_pool.json пустым списком, если файл еще не создан
            pass

    def save_keys(self):
        try:
            with open(self.keys_file, "w", encoding="utf-8") as f:
                json.dump(self.keys, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save keys: {e}")

    async def add_key(self, api_key: str, email: str = "", balance: float = 1.0):
        async with self._lock:
            # Check if exists
            for item in self.keys:
                if item.get("key") == api_key:
                    return
            entry = {
                "key": api_key,
                "email": email,
                "balance": balance,
                "added_at": time.time(),
                "requests_count": 0,
                "is_active": True
            }
            self.keys.append(entry)
            self.save_keys()
            logger.info(f"Added new key for {email} to pool. Total keys: {len(self.keys)}")

    async def get_key(self) -> Optional[str]:
        async with self._lock:
            active_keys = [k for k in self.keys if k.get("is_active", True)]
            if not active_keys:
                return None
            self._current_index = (self._current_index + 1) % len(active_keys)
            selected = active_keys[self._current_index]
            selected["requests_count"] = selected.get("requests_count", 0) + 1
            return selected["key"]

    async def mark_key_exhausted(self, api_key: str):
        async with self._lock:
            for item in self.keys:
                if item.get("key") == api_key:
                    item["is_active"] = False
                    item["exhausted_at"] = time.time()
                    logger.warning(f"Key {api_key[:10]}... marked as exhausted/revoked.")
                    break
            self.save_keys()

    def get_stats(self) -> Dict[str, Any]:
        active = [k for k in self.keys if k.get("is_active", True)]
        return {
            "total_keys": len(self.keys),
            "active_keys": len(active),
            "estimated_pool_tokens_in_M": len(active) * 33.33,
            "estimated_pool_tokens_out_M": len(active) * 4.0
        }
