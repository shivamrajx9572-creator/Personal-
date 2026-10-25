"""
MAYA ULTRA-POWER SERVER - WEBSOCKET & VOICE HUB
FILE 6: ws_hub.py
-------------------------------------------------------------------------
Features:
1. Multi-Device Support (Phone + Laptop can be connected simultaneously - VP-04 Fix)
2. Dual-Task Architecture (Reader & Worker loops run separately - MN-05 Fix)
3. Instant Barge-In (Cancels AI generation immediately on user voice - MN-05 Fix)
4. Background Job Tracking (Prevents asyncio.gather garbage collection - VP-05 Fix)
5. Heartbeat/Ping-Pong (Cleans up dead connections - VP-23 Fix)
6. Robust JSON Parsing (No server crashes on invalid packets - MN-09 Fix)
"""

import asyncio
import json
import time
import logging
import base64
import uuid
from typing import Dict, Set, Any, Optional, List
from fastapi import WebSocket, WebSocketDisconnect

# Import our engines
from core_engine import db_engine, TokenManager, VerifiedUser
from ai_brain import PersonaEngine, MemoryEngine, AsyncLLMRouter
from vision_engine import vision_engine
from phone_controller import phone_router, MacroSecurity
from studio_engine import GameStudio, WebsiteStudio, DocumentStudio, WhiteboardStudio

logger = logging.getLogger("MayaWSHub")

# ============================================================================
# 1. CONNECTION MANAGER (Multi-Device Support - VP-04 Fix)
# ============================================================================
class UltraConnectionManager:
    """
    Manages active WebSocket connections.
    Format: { "MAYA-XXXX": { websocket1, websocket2 } }
    """
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, license_key: str, websocket: WebSocket):
        if license_key not in self.active_connections:
            self.active_connections[license_key] = set()
        self.active_connections[license_key].add(websocket)
        logger.info(f"Client connected: {license_key}. Total devices: {len(self.active_connections[license_key])}")

    def disconnect(self, license_key: str, websocket: WebSocket):
        if license_key in self.active_connections:
            self.active_connections[license_key].discard(websocket)
            # Only remove the key if no devices are connected
            if not self.active_connections[license_key]:
                del self.active_connections[license_key]
        logger.info(f"Client disconnected: {license_key}")

    async def send_to_user(self, license_key: str, message: dict):
        """Sends a message to all devices connected to this license key."""
        if license_key in self.active_connections:
            # Broadcast to all devices (Phone + Laptop)
            disconnected = set()
            for ws in self.active_connections[license_key]:
                try:
                    await ws.send_json(message)
                except Exception:
                    disconnected.add(ws)
            
            # Cleanup dead sockets
            for ws in disconnected:
                self.disconnect(license_key, ws)

manager = UltraConnectionManager()

# ============================================================================
# 2. BACKGROUND JOB MANAGER (Prevents GC - VP-05 Fix)
# ============================================================================
class BackgroundJobManager:
    """
    Holds strong references to asyncio tasks so they don't get garbage collected.
    """
    def __init__(self):
        self.tasks: Set[asyncio.Task] = set()

    def create_task(self, coro, job_id: str, license_key: str):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        
        # Add exception handler to notify user on failure (VP-06 Fix)
        async def _notify_failure(t: asyncio.Task):
            try:
                await t
            except Exception as e:
                logger.error(f"Background job {job_id} failed: {e}")
                await manager.send_to_user(license_key, {
                    "type": "BACKGROUND_JOB_FAILED",
                    "job_id": job_id,
                    "message": "Sorry Boss, ye kaam fail ho gaya. Network ya API issue ho sakta hai."
                })
        
        asyncio.create_task(_notify_failure(task))
        return task

job_manager = BackgroundJobManager()

# ============================================================================
# 3. MAIN WEBSOCKET ENDPOINT
# ============================================================================
async def handle_websocket_live(websocket: WebSocket):
    """
    Entry point for the WebSocket connection.
    Handles auth, starts reader/worker loops, and manages heartbeat.
    """
    # 1. AUTHENTICATION (MN-08 Fix: Verify before accept)
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4003)
        return

    is_valid, err, payload = TokenManager.verify_token(token)
    if not is_valid:
        await websocket.close(code=4003)
        return

    license_key = payload.get("lic")
    
    # Verify live DB state (Admin block check)
    lic_data = await db_engine.get_license(license_key)
    if not lic_data or not lic_data["is_active"]:
        await websocket.close(code=4003)
        return

    # Construct VerifiedUser
    user = VerifiedUser(
        license_key=license_key,
        hwid=payload.get("hwid"),
        persona=lic_data["active_persona"],
        user_name=lic_data["user_display_name"]
    )

    # 2. ACCEPT CONNECTION
    await websocket.accept()
    await manager.connect(license_key, websocket)
    
    # 3. SETUP DUAL-TASK ARCHITECTURE (MN-05 Fix)
    packet_queue = asyncio.Queue()
    
    # Start Reader & Worker tasks
    reader_task = asyncio.create_task(reader_loop(websocket, packet_queue))
    worker_task = asyncio.create_task(worker_loop(user, websocket, packet_queue))
    heartbeat_task = asyncio.create_task(heartbeat_loop(websocket))

    # 4. WAIT FOR DISCONNECT
    try:
        done, pending = await asyncio.wait(
            [reader_task, worker_task, heartbeat_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    except Exception as e:
        logger.error(f"WS Connection Error: {e}")
    finally:
        manager.disconnect(license_key, websocket)
        vision_engine.cleanup_session(license_key)

# ============================================================================
# 4. READER LOOP (Receives packets fast, never blocks)
# ============================================================================
async def reader_loop(websocket: WebSocket, queue: asyncio.Queue):
    """
    Constantly listens to incoming packets and pushes them to the queue.
    This keeps the socket buffer empty and prevents event loop blocking.
    """
    while True:
        try:
            # Max message size check (VP-22 Fix)
            message = await websocket.receive_text()
            if len(message) > 10_000_000: # 10MB limit (for base64 images)
                logger.warning("Packet too large. Dropping.")
                continue
                
            queue.put_nowait(message)
        except WebSocketDisconnect:
            break
        except Exception as e:
            logger.error(f"Reader Loop Error: {e}")
            break

# ============================================================================
# 5. WORKER LOOP (Processes AI logic, handles Barge-in)
# ============================================================================
async def worker_loop(user: VerifiedUser, websocket: WebSocket, queue: asyncio.Queue):
    """
    Processes incoming packets sequentially.
    Manages current AI generation task and handles interrupts.
    """
    current_ai_task: Optional[asyncio.Task] = None

    while True:
        try:
            raw_msg = await queue.get()
            
            # Robust JSON Parsing (MN-09 Fix)
            try:
                packet = json.loads(raw_msg)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from {user.license_key}")
                continue

            pkt_type = packet.get("type", "").upper()

            # --- BARGE-IN HANDLING ---
            if pkt_type == "BARGE_IN_INTERRUPT":
                if current_ai_task and not current_ai_task.done():
                    logger.info(f"Barge-in! Cancelling current AI task for {user.license_key}")
                    current_ai_task.cancel()
                await websocket.send_json({"type": "STOP_TTS_PLAYBACK"})
                continue

            # --- HEARTBEAT RESPONSE ---
            if pkt_type == "PONG":
                continue

            # --- PING ---
            if pkt_type == "PING":
                await websocket.send_json({"type": "PONG", "timestamp": int(time.time())})
                continue

            # --- TEXT/VOICE COMMAND PROCESSING ---
            user_text = packet.get("text", "").strip()
            if not user_text:
                continue

            # Cancel previous task if still running
            if current_ai_task and not current_ai_task.done():
                current_ai_task.cancel()

            # Create new task for processing
            current_ai_task = asyncio.create_task(
                process_incoming_turn(user, websocket, packet, user_text)
            )
            
            # We don't await here, so the worker loop is free to process the next packet (Barge-in/Ping)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Worker Loop Error: {e}")
            break

# ============================================================================
# 6. TURN PROCESSOR (Routes to Vision, Studio, Phone, or AI)
# ============================================================================
async def process_incoming_turn(user: VerifiedUser, websocket: WebSocket, packet: dict, user_text: str):
    """
    Determines what the user wants (Call, Game, Website, Chat) and processes it.
    """
    turn_id = packet.get("turn_id", str(uuid.uuid4())[:8])
    
    try:
        # 1. Persona / Voice Switch Check
        switch_cmd = PersonaEngine.detect_switch(user_text)
        if switch_cmd:
            await handle_persona_switch(user, websocket, switch_cmd)
            return

        # 2. Phone Control Check (Calls, WhatsApp, Alarms)
        device_action = phone_router.parse_command(user, user_text)
        if device_action:
            await websocket.send_json({
                "type": "DEVICE_ACTION_COMMAND",
                "turn_id": turn_id,
                **device_action
            })
            return

        # 3. Creation Checks (Game, Website, Document)
        s_low = user_text.lower()
        api_bundle = packet.get("user_api_bundle", {})
        base_url = packet.get("base_url", "http://localhost:8000")
        
        if re.search(r"\b(game|tic tac toe|snake)\b", s_low) and re.search(r"\b(banao|create|make)\b", s_low):
            await manager.send_to_user(user.license_key, {"type": "BACKGROUND_JOB_STARTED", "spoken_reply": "Game bana rahi hoon, bante hi Chrome me khol dungi!"})
            job_manager.create_task(GameStudio.create_game(user, user_text, api_bundle, base_url), "game_job", user.license_key)
            return
            
        if re.search(r"\b(website|site)\b", s_low) and re.search(r"\b(banao|create|make)\b", s_low):
            await manager.send_to_user(user.license_key, {"type": "BACKGROUND_JOB_STARTED", "spoken_reply": "Website ka code likh rahi hoon, bante hi bataungi!"})
            job_manager.create_task(WebsiteStudio.create_website(user, user_text, api_bundle, base_url), "web_job", user.license_key)
            return
            
        doc_match = re.search(r"\b(pdf|excel|word)\b", s_low)
        if doc_match and re.search(r"\b(banao|create|make)\b", s_low):
            doc_type = doc_match.group(1).upper()
            await manager.send_to_user(user.license_key, {"type": "BACKGROUND_JOB_STARTED", "spoken_reply": f"{doc_type} taiyaar kar rahi hoon, bas ek minute!"})
            job_manager.create_task(DocumentStudio.generate_document(user, doc_type, user_text, api_bundle, base_url), f"{doc_type}_job", user.license_key)
            return

        # 4. Vision Check (If image is attached)
        image_b64 = packet.get("image_b64")
        if image_b64:
            vision_mode = packet.get("vision_mode", "SCREEN_SHARE")
            vision_res = await vision_engine.analyze_frame(
                user=user, image_base64=image_b64, vision_mode=vision_mode,
                user_query=user_text, ui_dump=packet.get("ui_dump", ""), api_bundle=api_bundle
            )
            if vision_res.get("spoken_reply"):
                await websocket.send_json({"type": "ASSISTANT_REPLY", "turn_id": turn_id, "spoken_reply": vision_res["spoken_reply"]})
            return

        # 5. Standard AI Chat
        emotion, intensity, visual = PersonaEngine.evaluate_emotion(user.persona, user_text)
        memories = await MemoryEngine.get_memories(user.license_key)
        sys_prompt = PersonaEngine.build_system_prompt(user, emotion, intensity, memories, [], "") # clock_str empty for now
        
        ai_res = await AsyncLLMRouter.generate_with_failover(
            user=user, system_prompt=sys_prompt, user_text=user_text, api_bundle=api_bundle
        )
        
        raw_reply = ai_res.get("reply", "Network issue hai Boss.")
        await MemoryEngine.extract_and_save(user, user_text, raw_reply)
        
        # Clean memory tags before sending to client
        clean_reply = re.sub(r"\[\[MEMORY:.+?\]\]", "", raw_reply).strip()

        await websocket.send_json({
            "type": "ASSISTANT_REPLY",
            "turn_id": turn_id,
            "spoken_reply": clean_reply,
            "emotion": emotion,
            "intensity": intensity,
            "avatar_visual": visual,
            "provider": ai_res.get("provider")
        })

    except asyncio.CancelledError:
        logger.info(f"Turn {turn_id} cancelled by barge-in.")
        raise
    except Exception as e:
        logger.error(f"Turn processing error: {e}")
        await websocket.send_json({"type": "ERROR", "spoken_reply": "Boss, kuch technical issue aa gaya."})

# ============================================================================
# 7. PERSONA SWITCH HANDLER
# ============================================================================
async def handle_persona_switch(user: VerifiedUser, websocket: WebSocket, cmd: dict):
    if cmd["command"] == "SET_PERSONA":
        new_p = cmd["persona"]
        await db_engine.execute("UPDATE licenses SET active_persona = ? WHERE license_key = ?", (new_p, user.license_key))
        
        await websocket.send_json({
            "type": "PERSONA_UPDATED",
            "active_persona": new_p,
            "spoken_reply": f"Mode badal gaya hai Boss! Ab main {new_p} avatar me hoon."
        })
    elif cmd["command"] == "SLEEP":
        await websocket.send_json({"type": "SLEEP_MODE", "is_sleeping": True, "spoken_reply": "Goodnight Boss, so rahi hoon main."})

# ============================================================================
# 8. HEARTBEAT LOOP (VP-23 Fix)
# ============================================================================
async def heartbeat_loop(websocket: WebSocket):
    """Sends PING every 20 seconds to keep the connection alive."""
    while True:
        await asyncio.sleep(20)
        try:
            await websocket.send_json({"type": "SERVER_PING", "timestamp": int(time.time())})
        except Exception:
            break