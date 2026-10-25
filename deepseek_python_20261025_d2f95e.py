"""
MAYA ULTRA-POWER SERVER - CORE ENGINE
FILE 1: core_engine.py
-------------------------------------------------------------------------
Features:
1. Ultra-Secure Config (Pydantic Settings - Server crashes if secrets missing)
2. Async SQLite Database (Zero event-loop blocking)
3. Cryptographic Token Manager (V2 Format: v2.base64.hmac, with 30-day expiry)
4. Hardware ID (HWID) License Engine & Admin Controls
5. Built-in Async Rate Limiter (Prevents Brute-force)
"""

import time
import json
import hmac
import hashlib
import secrets
import base64
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

import aiosqlite
from pydantic_settings import BaseSettings, SettingsConfigDict

# ============================================================================
# 1. LOGGING & CONFIGURATION
# ============================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("MayaCore")

class MayaSettings(BaseSettings):
    """Loads strictly from environment variables. No hardcoded secrets."""
    SERVER_API_KEY: str = ""
    ADMIN_SECRET: str = ""
    HMAC_SECRET: str = ""
    DB_PATH: str = "maya_ultra_core.db"
    TOKEN_TTL_HOURS: int = 720  # 30 Days
    RATE_LIMIT_WINDOW: int = 60
    RATE_LIMIT_MAX_REQ: int = 60

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def validate_secrets(self):
        if not self.SERVER_API_KEY or not self.ADMIN_SECRET or not self.HMAC_SECRET:
            raise RuntimeError("CRITICAL: MAYA_SERVER_API_KEY, MAYA_ADMIN_SECRET, and MAYA_HMAC_SECRET must be set in .env!")

settings = MayaSettings()
settings.validate_secrets()

# ============================================================================
# 2. VERIFIED CONTEXT (Prevents IDOR & Unauthorized Access)
# ============================================================================
@dataclass
class VerifiedUser:
    license_key: str
    hwid: str
    persona: str
    user_name: str

# ============================================================================
# 3. ASYNC DATABASE ENGINE
# ============================================================================
class AsyncDatabaseEngine:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA synchronous=NORMAL;")
            
            # License Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS licenses (
                    license_key TEXT PRIMARY KEY,
                    owner_label TEXT DEFAULT 'User',
                    max_devices INTEGER DEFAULT 1,
                    duration_days INTEGER DEFAULT 30,
                    created_at INTEGER NOT NULL,
                    activated_at INTEGER DEFAULT 0,
                    expires_at INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    bound_hwids_json TEXT DEFAULT '[]',
                    device_models_json TEXT DEFAULT '[]',
                    active_persona TEXT DEFAULT 'GF',
                    active_voice TEXT DEFAULT 'Leda',
                    user_display_name TEXT DEFAULT 'Boss',
                    user_gender TEXT DEFAULT 'Ladka',
                    auto_open_chrome INTEGER DEFAULT 1,
                    last_seen_at INTEGER DEFAULT 0
                )
            """)
            
            # Audit Logs
            await db.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    details TEXT
                )
            """)
            await db.commit()
        logger.info("✅ Async Database Initialized.")

    async def get_license(self, license_key: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM licenses WHERE license_key = ?", (license_key,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_all_licenses(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM licenses ORDER BY created_at DESC") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def execute(self, query: str, params: tuple = ()) -> bool:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(query, params)
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"DB Execute Error: {e}")
            return False

# ============================================================================
# 4. CRYPTOGRAPHIC TOKEN MANAGER
# ============================================================================
class TokenManager:
    @staticmethod
    def create_token(license_key: str, hwid: str) -> str:
        """Format: v2.<base64url_payload>.<hmac_signature>"""
        now = int(time.time())
        payload = {
            "lic": license_key,
            "hwid": hwid,
            "iat": now,
            "exp": now + (settings.TOKEN_TTL_HOURS * 3600)
        }
        payload_json = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        payload_b64 = base64.urlsafe_b64encode(payload_json).decode('utf-8').rstrip('=')
        
        msg = f"v2.{payload_b64}".encode('utf-8')
        sig = hmac.new(settings.HMAC_SECRET.encode('utf-8'), msg, hashlib.sha256).hexdigest()
        return f"v2.{payload_b64}.{sig}"

    @staticmethod
    def verify_token(token: str) -> Tuple[bool, str, Dict[str, Any]]:
        if not token or not token.startswith("v2."):
            return False, "Invalid token format", {}
        
        parts = token.split(".")
        if len(parts) != 3:
            return False, "Malformed token structure", {}
            
        _, payload_b64, provided_sig = parts
        msg = f"v2.{payload_b64}".encode('utf-8')
        expected_sig = hmac.new(settings.HMAC_SECRET.encode('utf-8'), msg, hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(expected_sig, provided_sig):
            return False, "Signature mismatch (Tampering detected)", {}

        try:
            padded_b64 = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded_b64).decode('utf-8'))
        except Exception:
            return False, "Invalid payload encoding", {}

        if time.time() > payload.get("exp", 0):
            return False, "Token expired. Please re-login.", {}
            
        return True, "OK", payload

# ============================================================================
# 5. ASYNC RATE LIMITER
# ============================================================================
class AsyncRateLimiter:
    def __init__(self):
        self.requests: Dict[str, List[float]] = {}

    def is_allowed(self, client_id: str) -> bool:
        now = time.time()
        if client_id not in self.requests:
            self.requests[client_id] = []
        
        self.requests[client_id] = [t for t in self.requests[client_id] if now - t < settings.RATE_LIMIT_WINDOW]
        
        if len(self.requests[client_id]) >= settings.RATE_LIMIT_MAX_REQ:
            return False
            
        self.requests[client_id].append(now)
        return True

rate_limiter = AsyncRateLimiter()

# ============================================================================
# 6. ULTRA LICENSE ENGINE (Business Logic)
# ============================================================================
class UltraLicenseEngine:
    def __init__(self, db: AsyncDatabaseEngine):
        self.db = db

    async def generate_key(self, owner_label: str, max_devices: int, duration_days: int) -> Dict[str, Any]:
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        while True:
            k = f"MAYA-{''.join(secrets.choice(alphabet) for _ in range(4))}-{''.join(secrets.choice(alphabet) for _ in range(4))}"
            if not await self.db.get_license(k):
                break
        
        now = int(time.time())
        await self.db.execute("""
            INSERT INTO licenses (license_key, owner_label, max_devices, duration_days, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (k, owner_label.strip()[:60], min(max_devices, 25), min(duration_days, 3650), now))
        
        return {"status": "SUCCESS", "license_key": k, "validity_days": duration_days}

    async def activate_device(self, license_key: str, hwid: str, device_model: str, server_api_key: str) -> Dict[str, Any]:
        # 1. Mandatory Server Key Check (No bypass)
        if not server_api_key or not hmac.compare_digest(server_api_key, settings.SERVER_API_KEY):
            return {"authenticated": False, "error": "INVALID_SERVER_KEY"}

        # 2. Rate Limit Check
        if not rate_limiter.is_allowed(hwid):
            return {"authenticated": False, "error": "RATE_LIMITED", "message": "Too many attempts."}

        # 3. Fetch License
        lic = await self.db.get_license(license_key)
        if not lic or lic["is_active"] == 0:
            return {"authenticated": False, "error": "AUTH_FAILED", "message": "Invalid or blocked license."}

        now = int(time.time())
        if lic["expires_at"] > 0 and now > lic["expires_at"]:
            return {"authenticated": False, "error": "EXPIRED", "message": "License expired."}

        # 4. HWID Binding
        bound_hwids = json.loads(lic["bound_hwids_json"])
        device_models = json.loads(lic["device_models_json"])

        if hwid not in bound_hwids:
            if len(bound_hwids) >= lic["max_devices"]:
                return {"authenticated": False, "error": "DEVICE_LIMIT", "message": "Max device limit reached."}
            bound_hwids.append(hwid)
            device_models.append(device_model[:40])

        # 5. Update State
        activated_at = lic["activated_at"]
        expires_at = lic["expires_at"]
        if activated_at == 0:
            activated_at = now
            expires_at = (now + lic["duration_days"] * 86400) if lic["duration_days"] > 0 else 0

        await self.db.execute("""
            UPDATE licenses 
            SET activated_at=?, expires_at=?, bound_hwids_json=?, device_models_json=?, last_seen_at=?
            WHERE license_key=?
        """, (activated_at, expires_at, json.dumps(bound_hwids), json.dumps(device_models), now, license_key))

        token = TokenManager.create_token(license_key, hwid)
        
        return {
            "authenticated": True,
            "session_token": token,
            "profile": {
                "user_name": lic["user_display_name"],
                "persona": lic["active_persona"],
                "voice": lic["active_voice"]
            }
        }

    async def admin_toggle_key(self, license_key: str, set_active: bool) -> bool:
        return await self.db.execute(
            "UPDATE licenses SET is_active = ? WHERE license_key = ?",
            (1 if set_active else 0, license_key)
        )

    async def admin_reset_hwid(self, license_key: str, new_max_devices: int) -> bool:
        return await self.db.execute(
            "UPDATE licenses SET bound_hwids_json = '[]', device_models_json = '[]', max_devices = ? WHERE license_key = ?",
            (new_max_devices, license_key)
        )

    async def admin_delete_key(self, license_key: str) -> bool:
        return await self.db.execute("DELETE FROM licenses WHERE license_key = ?", (license_key,))

# Instantiate Singletons
db_engine = AsyncDatabaseEngine(settings.DB_PATH)
license_engine = UltraLicenseEngine(db_engine)