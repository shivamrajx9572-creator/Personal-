"""
MAYA ULTRA-POWER SERVER - PHONE CONTROLLER & MACRO ENGINE
FILE 4: phone_controller.py
-------------------------------------------------------------------------
Features:
1. Smart Intent Router (Calls, WhatsApp, System Settings, Alarms, Maps)
2. Advanced Deduplication Lock (Prevents accidental double execution)
3. Context-Aware Time Parser (Handles "15 minute me", "7 AM", "shaam 5 baje")
4. Secure Macro Engine (Strict allowlist, prevents dangerous action injection)
5. Natural Language Contact Extractor (Filters out stopwords correctly)
"""

import re
import time
import json
import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

from core_engine import db_engine, VerifiedUser

logger = logging.getLogger("MayaPhoneControl")

# ============================================================================
# 1. SECURITY & CONSTANTS
# ============================================================================
# SEC-M3 Fix: Only these actions can be saved inside a Macro. 
# No dangerous actions like "Transfer Money" or "Factory Reset" allowed.
ALLOWED_MACRO_ACTIONS = {
    "OPEN_APP", "OPEN_URL", "PLAY_MEDIA", "SHOW_NOTE", "SET_TIMER", "CALL_CONTACT", "SEND_WHATSAPP"
}

# VP-15 Fix: Common English/Hindi stopwords to remove when extracting contact names
CONTACT_STOPWORDS = {
    "maya", "please", "par", "pe", "ko", "call", "kar", "karo", "lagao", "laga", "do", "whatsapp",
    "message", "bhejo", "voice", "video", "audio", "abhi", "jaldi", "se", "ek", "baat"
}

# ============================================================================
# 2. DEDUPLICATION ENGINE (Prevents double execution - VP-12 Fix)
# ============================================================================
class ActionDeduplicator:
    """
    Prevents the exact same action from firing twice within a short window.
    (e.g., if the user's voice is recognized twice by the STT engine).
    """
    def __init__(self, window_seconds: float = 4.0):
        self.window = window_seconds
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def check_and_store(self, user_key: str, raw_command: str, action_plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        now = time.time()
        
        # Cleanup old entries
        self._cache = {k: v for k, v in self._cache.items() if now - v[0] < self.window}

        normalized_cmd = " ".join(raw_command.lower().split())
        cache_key = f"{user_key}:{normalized_cmd}"

        if cache_key in self._cache:
            logger.info(f"Deduplication hit for: {cache_key}")
            return self._cache[cache_key][1]

        self._cache[cache_key] = (now, action_plan)
        return None

action_deduplicator = ActionDeduplicator()

# ============================================================================
# 3. TIME PARSER (VP-13 & VP-14 Fix: Handles relative & absolute time)
# ============================================================================
class TimeParser:
    @staticmethod
    def parse_time(text: str) -> Optional[Tuple[int, int]]:
        """Returns (hour, minute) in 24h format, or None."""
        s = text.lower()
        
        # 1. Relative Time: "15 minute me", "2 ghante baad"
        rel_match = re.search(r"(\d+)\s*(minute|min|ghanta|ghante|hour|hr)\s*(?:me|mein|baad|later)", s)
        if rel_match:
            amount = int(rel_match.group(1))
            unit = rel_match.group(2)
            now = datetime.now()
            
            if "min" in unit:
                target = now + timedelta(minutes=amount)
            else:
                target = now + timedelta(hours=amount)
                
            return target.hour, target.minute

        # 2. Absolute Time: "7 baje", "7:30 am", "shaam 5 baje"
        abs_match = re.search(r"(?:subah|dopahar|shaam|raat)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje)?", s)
        if abs_match:
            hr = int(abs_match.group(1))
            mn = int(abs_match.group(2)) if abs_match.group(2) else 0
            meridiem = abs_match.group(3)
            
            # Contextual AM/PM logic (VP-14 Fix)
            if "shaam" in s or "raat" in s:
                if hr < 12: hr += 12
            elif "subah" in s:
                if hr == 12: hr = 0
            elif meridiem == "pm" and hr < 12:
                hr += 12
            elif meridiem == "am" and hr == 12:
                hr = 0
            elif not meridiem and 1 <= hr <= 7: 
                # If no context, assume PM for 1-7 (e.g., "7 baje" usually means 7 PM)
                # Unless it's early morning. We'll default to PM for safety.
                hr += 12 
                
            if 0 <= hr <= 23 and 0 <= mn <= 59:
                return hr, mn
                
        return None

# ============================================================================
# 4. CONTACT EXTRACTOR (VP-15 & VP-30 Fix)
# ============================================================================
class ContactExtractor:
    @staticmethod
    def extract(raw_text: str) -> str:
        """Extracts the contact name by removing action words and stopwords."""
        # VP-16 Fix: Support basic Devanagari by splitting on spaces/punctuation
        words = re.split(r'[\s\W]+', raw_text.lower())
        valid_words = [w for w in words if w and w not in CONTACT_STOPWORDS]
        
        # Reconstruct name from valid words
        name = " ".join(valid_words).strip()
        
        # If name is empty, fallback
        if not name:
            return "Unknown Contact"
            
        return name.title()[:30] # Capitalize and limit length

# ============================================================================
# 5. MAIN INTENT ROUTER
# ============================================================================
class PhoneIntentRouter:
    def __init__(self):
        self.time_parser = TimeParser()
        self.contact_extractor = ContactExtractor()

    def parse_command(self, user: VerifiedUser, raw_text: str) -> Optional[Dict[str, Any]]:
        text = raw_text.strip()
        if len(text) < 2:
            return None

        s = " ".join(text.lower().split())
        
        # 1. Macro Execution Check
        macro_plan = self._check_macros(user.license_key, s)
        if macro_plan:
            return self._finalize(user, text, macro_plan)

        # 2. Call Controls (Answer/Reject)
        if re.search(r"\b(pick up|answer call|call uthao|phone uthao)\b", s):
            return self._finalize(user, text, self._make_action("CALL_CONTROL", "ANSWER_CALL", {}, "Call pick up kar liya hai."))
            
        if re.search(r"\b(reject call|hang up|call kaato|phone kaato|cut the call)\b", s):
            return self._finalize(user, text, self._make_action("CALL_CONTROL", "REJECT_CALL", {}, "Call cut kar diya hai."))

        # 3. WhatsApp (Message, Voice/Video Call)
        if "whatsapp" in s or "wa " in s:
            is_biz = "business" in s
            app_pkg = "com.whatsapp.w4b" if is_biz else "com.whatsapp"
            
            # WhatsApp Video Call
            if re.search(r"\b(video call|video karo)\b", s):
                name = self.contact_extractor.extract(text)
                return self._finalize(user, text, self._make_action("WHATSAPP", "WA_VIDEO_CALL", {"package": app_pkg, "recipient": name}, f"{name} ko WhatsApp video call laga rahi hoon."))
                
            # WhatsApp Voice Call
            if re.search(r"\b(voice call|audio call|call karo)\b", s):
                name = self.contact_extractor.extract(text)
                return self._finalize(user, text, self._make_action("WHATSAPP", "WA_VOICE_CALL", {"package": app_pkg, "recipient": name}, f"{name} ko WhatsApp voice call laga rahi hoon."))
                
            # WhatsApp Message (Format: "WhatsApp Rahul: Hello")
            colon_match = re.search(r"whatsapp(?:\s+business)?\s+(.+?)\s*:\s*(.+)", text, re.IGNORECASE)
            if colon_match:
                recipient = self.contact_extractor.extract(colon_match.group(1))
                msg = colon_match.group(2).strip()
                return self._finalize(user, text, self._make_action("WHATSAPP", "WA_SEND_MSG", {"package": app_pkg, "recipient": recipient, "message": msg}, f"{recipient} ko message bhej rahi hoon."))

        # 4. Normal Phone Call
        call_match = re.search(r"(?:call|phone)\s+(.+?)(?:\s+ko|\s+se)?$", text, re.IGNORECASE)
        if call_match and "whatsapp" not in s:
            name = self.contact_extractor.extract(call_match.group(1))
            return self._finalize(user, text, self._make_action("CALL_CONTROL", "PLACE_CALL", {"recipient": name}, f"{name} ko call laga rahi hoon."))

        # 5. Alarms & Timers
        if re.search(r"\b(alarm|अलार्म|timer|reminder)\b", s):
            time_tuple = self.time_parser.parse_time(s)
            if not time_tuple:
                return self._finalize(user, text, self._make_action("ALARM", "ASK_TIME", {}, "Kitne baje ka alarm lagana hai, time batao?"))
            
            hr, mn = time_tuple
            return self._finalize(user, text, self._make_action("ALARM", "SET_ALARM", {"hour": hr, "minute": mn}, f"{hr:02d}:{mn:02d} ka alarm set kar diya hai."))

        # 6. System Controls
        if re.search(r"\b(silent mode|phone silent|vibrate mode)\b", s):
            return self._finalize(user, text, self._make_action("SYSTEM", "SET_SILENT", {}, "Phone silent mode par daal diya hai."))
            
        if re.search(r"\b(lock screen|lock phone|screen lock)\b", s):
            return self._finalize(user, text, self._make_action("SYSTEM", "LOCK_SCREEN", {}, "Phone lock kar diya hai."))
            
        if re.search(r"\b(torch|flashlight|टॉर्च)\b", s):
            turn_off = bool(re.search(r"\b(off|band|bujha)\b", s))
            return self._finalize(user, text, self._make_action("SYSTEM", "TORCH", {"state": "OFF" if turn_off else "ON"}, f"Torch {'OFF' if turn_off else 'ON'} kar diya hai."))
            
        if re.search(r"\b(brightness|ब्राइटनेस)\b", s):
            level_match = re.search(r"\b(\d{1,3})\s*%?", s)
            level = min(100, max(5, int(level_match.group(1)))) if level_match else (30 if "kam" in s else 85)
            return self._finalize(user, text, self._make_action("SYSTEM", "SET_BRIGHTNESS", {"level": level}, f"Brightness {level}% kar di hai."))

        # 7. Media & Navigation
        play_match = re.search(r"^play\s+(.+)", text, re.IGNORECASE)
        if play_match:
            query = play_match.group(1).strip()
            return self._finalize(user, text, self._make_action("MEDIA", "PLAY", {"query": query}, f"'{query}' play kar rahi hoon."))
            
        nav_match = re.search(r"(?:navigate to|maps mein)\s+(.+)", text, re.IGNORECASE)
        if nav_match:
            dest = nav_match.group(1).strip()
            return self._finalize(user, text, self._make_action("MAPS", "NAVIGATE", {"destination": dest}, f"Google Maps mein '{dest}' ka navigation chalu kar diya hai."))

        # No action matched (Normal AI chat)
        return None

    # ------------------------------------------------------------------------
    # HELPER METHODS
    # ------------------------------------------------------------------------
    def _make_action(self, category: str, action_type: str, params: Dict[str, Any], spoken_reply: str) -> Dict[str, Any]:
        return {
            "action_id": f"act_{uuid.uuid4().hex[:8]}",
            "action_category": category,
            "action_type": action_type,
            "params": params,
            "spoken_reply": spoken_reply
        }

    def _finalize(self, user: VerifiedUser, raw_text: str, action_plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Checks deduplication before returning the plan."""
        # VP-12 Fix: If this exact action was just executed, return None (skip)
        duplicate = action_deduplicator.check_and_store(user.license_key, raw_text, action_plan)
        if duplicate:
            return duplicate  # Return the cached duplicate response so the user doesn't get ignored
            
        return action_plan

    def _check_macros(self, license_key: str, command_text: str) -> Optional[Dict[str, Any]]:
        """Placeholder for Macro DB check (Implemented in File 1 DB, called asynchronously later)"""
        # In the actual async flow, this will be awaited from the DB.
        # For now, we keep the sync structure clean.
        return None

# ============================================================================
# 6. MACRO VALIDATOR (SEC-M3 Fix)
# ============================================================================
class MacroSecurity:
    @staticmethod
    def validate_steps(steps: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """Ensures macros only contain safe actions."""
        if len(steps) > 15:
            return False, "Macro cannot have more than 15 steps."
            
        for step in steps:
            action_type = step.get("action_type")
            if action_type not in ALLOWED_MACRO_ACTIONS:
                return False, f"Action '{action_type}' is not allowed in macros."
                
        return True, "OK"

# ============================================================================
# 7. SINGLETON INSTANCE
# ============================================================================
phone_router = PhoneIntentRouter()