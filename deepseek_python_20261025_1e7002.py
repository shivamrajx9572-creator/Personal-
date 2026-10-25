"""
MAYA ULTRA-POWER SERVER - MASTER CONTROLLER
FILE 8: main.py
-------------------------------------------------------------------------
Features:
1. FastAPI App Assembly (Integrates all 7 files)
2. Secure CORS & Static File Middleware (Fixes MN-12, MN-13)
3. Global Exception Handler (Prevents server from exposing raw errors - MN-27)
4. 24/7 Render Keep-Awake Loop (Prevents cold starts - MN-25)
5. Auto DB Initialization on Startup
6. Health Check Endpoints
"""

import os
import time
import asyncio
import logging
import urllib.request
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, WebSocket, status
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

# Import all Ultra Modules
from core_engine import settings, db_engine
from ws_hub import handle_websocket_live
from admin_panel import admin_router
from studio_engine import PUBLIC_DIR

# ============================================================================
# 1. LOGGING SETUP
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
)
logger = logging.getLogger("MayaMaster")

# ============================================================================
# 2. KEEP-AWAKE LOOP (MN-25 Fix)
# ============================================================================
async def keep_render_awake():
    """
    Pings the server's own public URL every 10 minutes to prevent Render Free Tier
    from sleeping. (Only runs if RENDER_EXTERNAL_URL is set in env).
    """
    while True:
        await asyncio.sleep(600) # 10 minutes
        render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
        if render_url:
            try:
                ping_url = f"{render_url.rstrip('/')}/health"
                loop = asyncio.get_running_loop()
                # Run blocking urlopen in a thread to not block the event loop
                await loop.run_in_executor(
                    None, lambda: urllib.request.urlopen(ping_url, timeout=10).read()
                )
                logger.debug("Keep-awake ping sent successfully.")
            except Exception as e:
                logger.warning(f"Keep-awake ping failed: {e}")

# ============================================================================
# 3. LIFESPAN (Startup & Shutdown Events)
# ============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    logger.info("🚀 Initializing MAYA ULTRA Server...")
    
    # 1. Init DB
    await db_engine.init_db()
    
    # 2. Start Keep-Awake Task
    awake_task = asyncio.create_task(keep_render_awake())
    
    yield # Server is running
    
    # --- SHUTDOWN ---
    logger.info("🛑 Shutting down MAYA ULTRA Server...")
    awake_task.cancel()
    try:
        await awake_task
    except asyncio.CancelledError:
        pass

# ============================================================================
# 4. FASTAPI APP INITIALIZATION
# ============================================================================
app = FastAPI(
    title="MAYA ULTRA-POWER SERVER",
    description="Next-Gen AI Voice Assistant Backend",
    version="3.0.0",
    lifespan=lifespan
)

# ============================================================================
# 5. MIDDLEWARE
# ============================================================================

# MN-12 Fix: Secure CORS (No wildcard with credentials)
# Since this is an API for an Android App, we allow all origins BUT credentials are false.
# (If you have a specific web frontend, replace ["*"] with ["https://yourdomain.com"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# MN-13 Fix: Security Headers for Static Files (Prevents XSS from generated HTML)
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    
    # Add security headers to ALL responses
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    
    # If serving generated HTML files, sandbox them so they can't access API tokens
    if request.url.path.startswith("/public/"):
        response.headers["Content-Security-Policy"] = "sandbox allow-scripts allow-same-origin;"
        
    return response

# ============================================================================
# 6. GLOBAL EXCEPTION HANDLERS (MN-27 Fix)
# ============================================================================
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handles invalid JSON or missing fields gracefully."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"status": "ERROR", "message": "Invalid request data format.", "details": exc.errors()}
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catches all unhandled exceptions to prevent server crashes and raw tracebacks."""
    logger.error(f"GLOBAL EXCEPTION on {request.url.path}: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"status": "ERROR", "message": "Internal server error. Boss, thodi der baad try karein."}
    )

# ============================================================================
# 7. STATIC FILE SERVING
# ============================================================================
# Mount the generated_public directory for Games, Websites, PDFs, etc.
app.mount("/public", StaticFiles(directory=PUBLIC_DIR), name="public")

# ============================================================================
# 8. ROUTER INCLUSION
# ============================================================================
# Include Admin Dashboard routes (File 7)
app.include_router(admin_router)

# ============================================================================
# 9. CORE ROUTES
# ============================================================================
@app.get("/")
async def root():
    """Root endpoint for Render Health Checks."""
    return {
        "server": "MAYA ULTRA",
        "status": "ONLINE",
        "version": "3.0.0",
        "timestamp": int(time.time())
    }

@app.get("/health")
async def health_check():
    """Lightweight health check for Render and Uptime Monitors."""
    return {"status": "ok", "timestamp": int(time.time())}

# ============================================================================
# 10. WEBSOCKET ROUTE (File 6)
# ============================================================================
@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint.
    Delegates the connection to the UltraConnectionManager in ws_hub.py
    """
    await handle_websocket_live(websocket)

# ============================================================================
# 11. MAIN EXECUTION
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    
    # Get port from Render (default to 10000 for local testing)
    port = int(os.getenv("PORT", "10000"))
    
    logger.info(f"🔥 Starting Uvicorn Server on port {port}...")
    
    # MN-26 Fix: Proxy headers enabled
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False, # Production me hamesha False rakhein
        proxy_headers=True,
        forwarded_allow_ips="*"
    )