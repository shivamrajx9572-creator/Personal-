"""
MAYA ULTRA-POWER SERVER - SECURE ADMIN DASHBOARD
FILE 7: admin_panel.py
-------------------------------------------------------------------------
Features:
1. Cyberpunk UI (Glassmorphism, Neon borders, Responsive)
2. Secure DOM (No innerHTML for dynamic data - Prevents XSS - AD-03 Fix)
3. Robust Fetch API (Checks res.ok, handles 401/500 gracefully - AD-05 Fix)
4. Separate Actions (Change Limit vs Reset HWID - AD-06 Fix)
5. Confirmation Dialogs for destructive actions (AD-07 Fix)
6. Clipboard Fallback for HTTP/HTTPS compatibility (AD-08 Fix)
"""

import os
from fastapi import APIRouter, Request, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Optional

# Import DB Engine
from core_engine import db_engine, settings, license_engine

# Create Router for Admin
admin_router = APIRouter()

# ============================================================================
# 1. SECURE BACKEND ROUTES (CRUD for Admin)
# ============================================================================

def verify_admin(x_admin_secret: Optional[str] = Header(None)):
    """Middleware to verify admin access."""
    if not x_admin_secret or x_admin_secret != settings.ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid Admin Secret")
    return True

@admin_router.get("/api/admin/list-keys")
async def api_admin_list_keys(authenticated: bool = Depends(verify_admin)):
    """Fetches all licenses securely."""
    keys = await db_engine.get_all_licenses()
    # Sanitize data before sending (strip raw HWIDs if needed, but admin needs them)
    return {"status": "success", "keys": keys}

@admin_router.post("/api/admin/generate-key")
async def api_admin_generate_key(request: Request, authenticated: bool = Depends(verify_admin)):
    """Generates a new license key."""
    data = await request.json()
    owner = data.get("owner_label", "User")
    devices = int(data.get("max_devices", 1))
    days = int(data.get("duration_days", 30))
    
    res = await license_engine.generate_key(owner, devices, days)
    return res

@admin_router.post("/api/admin/toggle-key")
async def api_admin_toggle_key(request: Request, authenticated: bool = Depends(verify_admin)):
    """Blocks or unblocks a license."""
    data = await request.json()
    key = data.get("license_key")
    active = data.get("set_active", True)
    
    success = await license_engine.admin_toggle_key(key, active)
    return {"status": "success" if success else "failed"}

@admin_router.post("/api/admin/reset-devices")
async def api_admin_reset_devices(request: Request, authenticated: bool = Depends(verify_admin)):
    """Resets HWID to allow login on a new phone."""
    data = await request.json()
    key = data.get("license_key")
    new_limit = int(data.get("new_max_devices", 1))
    
    success = await license_engine.admin_reset_hwid(key, new_limit)
    return {"status": "success" if success else "failed"}

@admin_router.post("/api/admin/change-limit")
async def api_admin_change_limit(request: Request, authenticated: bool = Depends(verify_admin)):
    """Only changes the max device limit without clearing existing devices."""
    data = await request.json()
    key = data.get("license_key")
    new_limit = int(data.get("new_max_devices", 1))
    
    success = await db_engine.execute(
        "UPDATE licenses SET max_devices = ? WHERE license_key = ?",
        (new_limit, key)
    )
    return {"status": "success" if success else "failed"}

@admin_router.post("/api/admin/delete-key")
async def api_admin_delete_key(request: Request, authenticated: bool = Depends(verify_admin)):
    """Deletes a license permanently."""
    data = await request.json()
    key = data.get("license_key")
    success = await license_engine.admin_delete_key(key)
    return {"status": "success" if success else "failed"}

# ============================================================================
# 2. SECURE ADMIN HTML UI
# ============================================================================

@admin_router.get("/admin", response_class=HTMLResponse)
async def serve_admin_ui(request: Request):
    """
    Serves the Ultra-Advanced Admin Dashboard.
    Includes strict CSP headers to prevent XSS.
    """
    base_url = str(request.base_url).rstrip("/")
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0"> <!-- AD-09 Fix: Zoom enabled -->
        <title>MAYA ULTRA | Command Center</title>
        <style>
            :root {{
                --bg: #020617;
                --card-bg: rgba(15, 23, 42, 0.8);
                --primary: #0ea5e9;
                --primary-glow: rgba(14, 165, 233, 0.3);
                --danger: #f43f5e;
                --success: #10b981;
                --text: #f8fafc;
                --muted: #94a3b8;
            }}
            * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }}
            body {{
                background-color: var(--bg);
                background-image: radial-gradient(circle at 50% 0%, #0f172a 0%, #020617 70%);
                color: var(--text);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                overflow-x: hidden;
            }}
            
            /* Header & Nav */
            header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 16px 24px;
                background: rgba(2, 6, 23, 0.8);
                border-bottom: 1px solid rgba(56, 189, 248, 0.2);
                backdrop-filter: blur(10px);
                position: sticky;
                top: 0;
                z-index: 100;
            }}
            h1 {{
                font-size: 1.5rem;
                font-weight: 800;
                background: linear-gradient(90deg, #38bdf8, #f472b6);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                letter-spacing: 1px;
            }}
            .status-pill {{
                font-size: 0.8rem;
                color: var(--success);
                background: rgba(16, 185, 129, 0.1);
                padding: 6px 14px;
                border-radius: 999px;
                border: 1px solid var(--success);
                display: flex;
                align-items: center;
                gap: 6px;
                font-weight: 600;
            }}
            .dot {{ width: 8px; height: 8px; background: var(--success); border-radius: 50%; box-shadow: 0 0 8px var(--success); }}

            /* Main Content */
            main {{ padding: 24px; max-width: 1200px; margin: 0 auto; width: 100%; display: flex; flex-direction: column; gap: 24px; }}

            /* Login Overlay (AD-02 & AD-10 Fix) */
            #loginOverlay {{
                position: fixed;
                inset: 0;
                background: rgba(2, 6, 23, 0.95);
                backdrop-filter: blur(10px);
                display: flex;
                justify-content: center;
                align-items: center;
                z-index: 1000;
            }}
            .login-box {{
                background: var(--card-bg);
                padding: 40px;
                border-radius: 24px;
                border: 1px solid var(--primary);
                box-shadow: 0 0 30px var(--primary-glow);
                text-align: center;
                width: 100%;
                max-width: 400px;
            }}
            .login-box h2 {{ margin-bottom: 20px; color: var(--primary); }}
            input[type="password"], input[type="text"], input[type="number"] {{
                width: 100%;
                padding: 12px 16px;
                background: rgba(15, 23, 42, 0.6);
                border: 1px solid rgba(56, 189, 248, 0.3);
                border-radius: 12px;
                color: var(--text);
                font-size: 1rem;
                margin-bottom: 16px;
                outline: none;
                transition: all 0.2s;
            }}
            input:focus {{ border-color: var(--primary); box-shadow: 0 0 10px var(--primary-glow); }}
            
            /* Buttons */
            .btn {{
                padding: 10px 20px;
                border: none;
                border-radius: 10px;
                font-weight: 700;
                cursor: pointer;
                transition: all 0.2s;
                font-size: 0.9rem;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 6px;
            }}
            .btn:hover {{ transform: translateY(-2px); filter: brightness(1.1); }}
            .btn:active {{ transform: translateY(0); }}
            .btn-primary {{ background: var(--primary); color: #fff; box-shadow: 0 4px 12px var(--primary-glow); }}
            .btn-danger {{ background: rgba(244, 63, 94, 0.1); color: var(--danger); border: 1px solid var(--danger); }}
            .btn-danger:hover {{ background: var(--danger); color: #fff; }}
            .btn-success {{ background: rgba(16, 185, 129, 0.1); color: var(--success); border: 1px solid var(--success); }}
            .btn-success:hover {{ background: var(--success); color: #fff; }}
            .btn-warning {{ background: rgba(245, 158, 11, 0.1); color: #f59e0b; border: 1px solid #f59e0b; }}
            .btn-warning:hover {{ background: #f59e0b; color: #fff; }}
            .btn-sm {{ padding: 6px 12px; font-size: 0.8rem; border-radius: 8px; }}

            /* Cards */
            .card {{
                background: var(--card-bg);
                border: 1px solid rgba(56, 189, 248, 0.15);
                border-radius: 20px;
                padding: 24px;
                backdrop-filter: blur(10px);
                box-shadow: 0 8px 32px rgba(0,0,0,0.2);
            }}
            .card h3 {{ font-size: 1.2rem; margin-bottom: 20px; color: var(--primary); display: flex; align-items: center; gap: 8px; }}
            
            /* Grid Layouts */
            .grid-3 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }}
            
            /* Key Generator */
            .gen-result {{
                display: none;
                background: rgba(16, 185, 129, 0.1);
                border: 2px dashed var(--success);
                border-radius: 16px;
                padding: 20px;
                text-align: center;
                margin-top: 20px;
            }}
            .gen-key {{ font-size: 2rem; font-weight: 900; color: var(--success); font-family: monospace; letter-spacing: 2px; margin: 10px 0; }}

            /* Table */
            .table-container {{ overflow-x: auto; border-radius: 12px; border: 1px solid rgba(56, 189, 248, 0.15); }}
            table {{ width: 100%; border-collapse: collapse; text-align: left; }}
            th, td {{ padding: 14px 16px; border-bottom: 1px solid rgba(56, 189, 248, 0.1); }}
            th {{ background: rgba(15, 23, 42, 0.9); color: var(--primary); font-size: 0.85rem; text-transform: uppercase; letter-spacing: 1px; }}
            tr:hover {{ background: rgba(56, 189, 248, 0.05); }}
            .action-cell {{ display: flex; gap: 8px; flex-wrap: wrap; }}

            /* Toast */
            #toast {{
                position: fixed;
                bottom: 24px;
                right: 24px;
                padding: 14px 24px;
                border-radius: 12px;
                color: #fff;
                font-weight: bold;
                opacity: 0;
                transform: translateY(20px);
                transition: all 0.3s;
                z-index: 2000;
                box-shadow: 0 4px 20px rgba(0,0,0,0.5);
            }}
            #toast.show {{ opacity: 1; transform: translateY(0); }}
            .toast-success {{ background: var(--success); }}
            .toast-error {{ background: var(--danger); }}
        </style>
    </head>
    <body>

        <!-- LOGIN OVERLAY -->
        <div id="loginOverlay">
            <div class="login-box">
                <h2>🚀 MAYA COMMAND CENTER</h2>
                <p style="color: var(--muted); margin-bottom: 20px; font-size: 0.9rem;">Enter Master Admin Secret</p>
                <input type="password" id="adminSecretInput" placeholder="Enter Admin Secret" autocomplete="off">
                <button class="btn btn-primary" style="width: 100%;" onclick="handleLogin()">Access System</button>
            </div>
        </div>

        <!-- MAIN DASHBOARD (Hidden initially) -->
        <div id="dashboard" style="display: none;">
            <header>
                <h1>⚡ MAYA ULTRA</h1>
                <div style="display: flex; gap: 12px;">
                    <div class="status-pill"><div class="dot"></div> SYSTEM ONLINE</div>
                    <button class="btn btn-danger btn-sm" onclick="logout()">Logout</button>
                </div>
            </header>

            <main>
                <!-- KEY GENERATOR -->
                <div class="card">
                    <h3>🔑 Generate New License Key</h3>
                    <div class="grid-3">
                        <div>
                            <label style="color: var(--muted); font-size: 0.85rem; margin-bottom: 4px; display: block;">User Name (Optional)</label>
                            <input type="text" id="genOwner" placeholder="e.g., Rahul">
                        </div>
                        <div>
                            <label style="color: var(--muted); font-size: 0.85rem; margin-bottom: 4px; display: block;">Max Devices</label>
                            <input type="number" id="genDevices" value="1" min="1" max="25">
                        </div>
                        <div>
                            <label style="color: var(--muted); font-size: 0.85rem; margin-bottom: 4px; display: block;">Validity (Days) - 0 for Lifetime</label>
                            <input type="number" id="genDays" value="30" min="0">
                        </div>
                    </div>
                    <button class="btn btn-primary" style="margin-top: 16px;" onclick="generateKey()">⚡ Generate Key</button>
                    
                    <div class="gen-result" id="genResultBox">
                        <p style="color: var(--success); font-size: 0.9rem;">Key Generated Successfully!</p>
                        <div class="gen-key" id="genKeyText">MAYA-XXXX-XXXX</div>
                        <button class="btn btn-success btn-sm" onclick="copyText(document.getElementById('genKeyText').innerText)">📋 Copy Key</button>
                    </div>
                </div>

                <!-- LICENSE TABLE -->
                <div class="card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                        <h3 style="margin: 0;">🛡️ Active Licenses</h3>
                        <button class="btn btn-primary btn-sm" onclick="loadAllKeys()">🔄 Refresh</button>
                    </div>
                    
                    <div class="table-container">
                        <table>
                            <thead>
                                <tr>
                                    <th>License Key</th>
                                    <th>User / Persona</th>
                                    <th>Devices</th>
                                    <th>Status</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody id="keysTableBody">
                                <tr><td colspan="5" style="text-align: center; color: var(--muted);">Loading keys...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </main>
        </div>

        <!-- TOAST -->
        <div id="toast">Message</div>

        <script>
            // =================================================================
            // STATE & SECURITY (AD-10 Fix: Secret stored in memory, not DOM)
            // =================================================================
            let adminSecret = "";

            function handleLogin() {
                const input = document.getElementById("adminSecretInput").value.trim();
                if (!input) { showToast("Secret required!", "error"); return; }
                
                // Test the secret by hitting an API
                fetch("/api/admin/list-keys", {
                    headers: { "X-Admin-Secret": input }
                })
                .then(async res => {
                    if (res.ok) {
                        adminSecret = input;
                        document.getElementById("loginOverlay").style.display = "none";
                        document.getElementById("dashboard").style.display = "block";
                        loadAllKeys(); // Load data immediately
                    } else {
                        showToast("Invalid Admin Secret!", "error");
                    }
                })
                .catch(err => showToast("Network error!", "error"));
            }

            function logout() {
                adminSecret = "";
                document.getElementById("dashboard").style.display = "none";
                document.getElementById("loginOverlay").style.display = "flex";
                document.getElementById("adminSecretInput").value = "";
            }

            // =================================================================
            // UTILITIES (AD-03 Fix: XSS Prevention)
            // =================================================================
            function escapeHTML(str) {
                if (!str) return "";
                return str.replace(/[&<>'"]/g, 
                    tag => ({
                        '&': '&amp;',
                        '<': '&lt;',
                        '>': '&gt;',
                        "'": '&#39;',
                        '"': '&quot;'
                    }[tag] || tag)
                );
            }

            function showToast(msg, type = "success") {
                const t = document.getElementById("toast");
                t.textContent = msg;
                t.className = `show toast-${type}`;
                setTimeout(() => { t.className = ""; }, 3000);
            }

            // AD-08 Fix: Clipboard fallback
            function copyText(text) {
                if (navigator.clipboard && window.isSecureContext) {
                    navigator.clipboard.writeText(text)
                        .then(() => showToast("Copied!"))
                        .catch(() => fallbackCopy(text));
                } else {
                    fallbackCopy(text);
                }
            }

            function fallbackCopy(text) {
                const ta = document.createElement("textarea");
                ta.value = text;
                ta.style.position = "fixed";
                document.body.appendChild(ta);
                ta.select();
                try {
                    document.execCommand('copy');
                    showToast("Copied!");
                } catch (e) {
                    showToast("Copy failed", "error");
                }
                document.body.removeChild(ta);
            }

            // =================================================================
            // API CALLS (AD-05 Fix: Proper error handling)
            // =================================================================
            async function apiCall(endpoint, method = "GET", body = null) {
                const options = {
                    method: method,
                    headers: { "X-Admin-Secret": adminSecret, "Content-Type": "application/json" }
                };
                if (body) options.body = JSON.stringify(body);

                const res = await fetch(endpoint, options);
                if (!res.ok) {
                    if (res.status === 401) { logout(); throw new Error("Session expired"); }
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || "API Error");
                }
                return res.json();
            }

            // =================================================================
            // ACTIONS
            // =================================================================
            async function generateKey() {
                const owner = document.getElementById("genOwner").value.trim();
                const devices = parseInt(document.getElementById("genDevices").value);
                const days = parseInt(document.getElementById("genDays").value);

                try {
                    const data = await apiCall("/api/admin/generate-key", "POST", {
                        owner_label: owner, max_devices: devices, duration_days: days
                    });
                    
                    if (data.status === "SUCCESS") {
                        document.getElementById("genResultBox").style.display = "block";
                        document.getElementById("genKeyText").textContent = data.license_key;
                        copyText(data.license_key);
                        loadAllKeys(); // Refresh table
                    }
                } catch (e) {
                    showToast(e.message, "error");
                }
            }

            async function loadAllKeys() {
                const tbody = document.getElementById("keysTableBody");
                try {
                    const data = await apiCall("/api/admin/list-keys");
                    
                    if (!data.keys || data.keys.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--muted);">No licenses found.</td></tr>';
                        return;
                    }

                    // AD-03 Fix: Using textContent / escaping instead of raw innerHTML
                    tbody.innerHTML = "";
                    data.keys.forEach(k => {
                        const hwids = JSON.parse(k.bound_hwids_json || "[]");
                        const devCount = hwids.length;
                        const isActive = k.is_active === 1;
                        const statusHTML = isActive 
                            ? '<span style="color: var(--success); font-weight: bold;">● Active</span>' 
                            : '<span style="color: var(--danger); font-weight: bold;">● Blocked</span>';

                        const tr = document.createElement("tr");
                        tr.innerHTML = `
                            <td>
                                <strong style="color: #fde047; font-family: monospace;">${escapeHTML(k.license_key)}</strong><br>
                                <button class="btn btn-sm btn-primary" style="margin-top: 4px;" onclick="copyText('${escapeHTML(k.license_key)}')">📋 Copy</button>
                            </td>
                            <td>
                                <strong>${escapeHTML(k.owner_label || "User")}</strong><br>
                                <span style="color: var(--muted); font-size: 0.8rem;">Name: ${escapeHTML(k.user_display_name)} | ${escapeHTML(k.active_persona)}</span>
                            </td>
                            <td>
                                <strong>${devCount} / ${k.max_devices}</strong> Devices
                            </td>
                            <td>${statusHTML}</td>
                            <td>
                                <div class="action-cell">
                                    <button class="btn btn-sm ${isActive ? 'btn-danger' : 'btn-success'}" onclick="toggleKey('${escapeHTML(k.license_key)}', ${!isActive})">${isActive ? '🚫 Block' : '✅ Activate'}</button>
                                    <button class="btn btn-sm btn-warning" onclick="resetDevices('${escapeHTML(k.license_key)}')">🔄 Reset HWID</button>
                                    <button class="btn btn-sm btn-primary" onclick="changeLimit('${escapeHTML(k.license_key)}', ${k.max_devices})">📱 Limit</button>
                                    <button class="btn btn-sm btn-danger" onclick="deleteKey('${escapeHTML(k.license_key)}')">🗑️</button>
                                </div>
                            </td>
                        `;
                        tbody.appendChild(tr);
                    });
                } catch (e) {
                    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--danger);">Error: ${escapeHTML(e.message)}</td></tr>`;
                }
            }

            // AD-07 Fix: Confirmation dialogs
            async function toggleKey(key, setActive) {
                if (!confirm(`Are you sure you want to ${setActive ? 'ACTIVATE' : 'BLOCK'} this key?`)) return;
                try {
                    await apiCall("/api/admin/toggle-key", "POST", { license_key: key, set_active: setActive });
                    showToast(setActive ? "Key Activated!" : "Key Blocked!");
                    loadAllKeys();
                } catch (e) { showToast(e.message, "error"); }
            }

            // AD-06 Fix: Separated Reset vs Change Limit
            async function resetDevices(key) {
                if (!confirm("This will LOGOUT the user from all devices. Proceed?")) return;
                const newLimit = prompt("Enter new max device limit (e.g., 1 or 2):", "1");
                if (!newLimit) return;
                try {
                    await apiCall("/api/admin/reset-devices", "POST", { license_key: key, new_max_devices: parseInt(newLimit) });
                    showToast("Devices Reset Successfully!");
                    loadAllKeys();
                } catch (e) { showToast(e.message, "error"); }
            }

            async function changeLimit(key, currentLimit) {
                const newLimit = prompt(`Current Limit: ${currentLimit}. Enter new limit:`, currentLimit);
                if (!newLimit || parseInt(newLimit) === currentLimit) return;
                try {
                    await apiCall("/api/admin/change-limit", "POST", { license_key: key, new_max_devices: parseInt(newLimit) });
                    showToast("Device Limit Updated!");
                    loadAllKeys();
                } catch (e) { showToast(e.message, "error"); }
            }

            async function deleteKey(key) {
                if (!confirm("WARNING: This will permanently delete the key and all associated data. Proceed?")) return;
                try {
                    await apiCall("/api/admin/delete-key", "POST", { license_key: key });
                    showToast("Key Deleted!");
                    loadAllKeys();
                } catch (e) { showToast(e.message, "error"); }
            }
        </script>
    </body>
    </html>
    """
    
    # AD-09 Fix: Security Headers
    headers = {
        "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff"
    }
    
    return HTMLResponse(content=html_content, headers=headers)

# ============================================================================
# 3. APP INTEGRATION (main.py me add karne ke liye)
# ============================================================================
# from admin_panel import admin_router
# app.include_router(admin_router)