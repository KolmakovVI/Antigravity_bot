import os
import re
import json
import ssl
import time
import base64
import logging
import urllib.request
import urllib.error
import urllib.parse
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, Any

from config import (
    ANTIGRAVITY_APP_DATA,
    ANTIGRAVITY_CONFIG_DIR,
    ANTIGRAVITY_PROJECTS_DIR,
    ANTIGRAVITY_CONVERSATIONS_DIR,
    ANTIGRAVITY_BRAIN_DIR,
    ANTIGRAVITY_LOGS_DIR,
)

logger = logging.getLogger("AntigravityBridge")

class AntigravityBridge:
    def __init__(self):
        self.port: Optional[int] = None
        self.csrf_token: Optional[str] = None
        self._last_detect_time: float = 0
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def detect_connection(self, force: bool = False) -> bool:
        """Auto-detect active HTTPS port and csrf token of running language_server.exe."""
        now = time.time()
        if not force and self.port and self.csrf_token and (now - self._last_detect_time < 60):
            if self._test_connection():
                return True

        logger.info("Detecting Antigravity language_server process and connection parameters...")

        # 1. Query running language_server.exe via PowerShell to get csrf_token
        ps_cmd = 'Get-CimInstance Win32_Process -Filter "name = \'language_server.exe\'" | Select-Object ProcessId, CommandLine | ConvertTo-Json'
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        out = res.stdout.strip()
        if not out:
            logger.warning("language_server.exe process was not found in process list.")
            return False

        try:
            proc_info = json.loads(out)
            if isinstance(proc_info, list):
                proc_info = proc_info[0]
            cmdline = proc_info.get("CommandLine", "")
            pid = proc_info.get("ProcessId")
            logger.info(f"Found language_server.exe (PID: {pid})")
        except Exception as e:
            logger.error(f"Failed to parse process info: {e}")
            return False

        csrf_match = re.search(r'--csrf_token\s+([a-f0-9\-]+)', cmdline)
        if not csrf_match:
            logger.warning("Could not find --csrf_token in command line arguments.")
            return False
        csrf_token = csrf_match.group(1)

        # 2. Get active HTTPS port from language_server.log or main.log
        port = None
        log_files = [
            ANTIGRAVITY_LOGS_DIR / "language_server.log",
            ANTIGRAVITY_LOGS_DIR / "main.log"
        ]
        for log_file in log_files:
            if log_file.exists():
                try:
                    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                        for line in reversed(lines):
                            m = re.search(r'listening on random port at (\d+) for HTTPS', line)
                            if not m:
                                m = re.search(r'https://127\.0\.0\.1:(\d+)/', line)
                            if m:
                                port = int(m.group(1))
                                break
                except Exception as e:
                    logger.warning(f"Error reading log file {log_file}: {e}")
            if port:
                break

        if not port:
            logger.warning("Could not extract HTTPS port from logs.")
            return False

        self.port = port
        self.csrf_token = csrf_token
        self._last_detect_time = now

        connected = self._test_connection()
        if connected:
            logger.info(f"Successfully connected to Antigravity on port {self.port} (CSRF: {self.csrf_token[:8]}...)")
        else:
            logger.warning(f"Failed health check to port {self.port}")
        return connected

    def _test_connection(self) -> bool:
        if not self.port or not self.csrf_token:
            return False
        try:
            resp = self._rpc_call("GetStatus", {})
            return resp is not None
        except Exception:
            return False

    def _rpc_call(self, method: str, payload: dict) -> dict:
        """Call a Connect-RPC method on the local LanguageServerService."""
        if not self.port or not self.csrf_token:
            if not self.detect_connection():
                raise RuntimeError("Antigravity language server is not running or unreachable.")

        url = f"https://127.0.0.1:{self.port}/exa.language_server_pb.LanguageServerService/{method}"
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": self.csrf_token,
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                logger.warning("401 Unauthorized received, redetecting connection parameters...")
                if self.detect_connection(force=True):
                    headers["x-codeium-csrf-token"] = self.csrf_token
                    req2 = urllib.request.Request(url, data=data, headers=headers, method="POST")
                    with urllib.request.urlopen(req2, context=self._ssl_ctx, timeout=15) as resp2:
                        raw = resp2.read().decode("utf-8")
                        return json.loads(raw) if raw.strip() else {}
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.error(f"Connect-RPC error {e.code} for method {method}: {err_body}")
            raise RuntimeError(f"Connect-RPC error {e.code}: {err_body}")

    def _load_projects_raw(self) -> List[Dict[str, Any]]:
        """Return list of all configured projects from disk without chat-based recency sorting."""
        projects = []
        if not ANTIGRAVITY_PROJECTS_DIR.exists():
            return projects

        for pfile in ANTIGRAVITY_PROJECTS_DIR.glob("*.json"):
            try:
                with open(pfile, "r", encoding="utf-8-sig") as f:
                    pdata = json.load(f)
                    pid = pdata.get("id") or pfile.stem
                    name = pdata.get("name", pid)
                    folders = []
                    normalized_folders = []
                    for r in pdata.get("projectResources", {}).get("resources", []):
                        uri = r.get("folderUri") or r.get("gitFolder", {}).get("folderUri")
                        if uri:
                            folders.append(uri)
                            unquoted = urllib.parse.unquote(uri).lower().rstrip('/')
                            normalized_folders.append(unquoted)

                    mtime = pfile.stat().st_mtime
                    projects.append({
                        "id": pid,
                        "name": name,
                        "folders": folders,
                        "normalized_folders": normalized_folders,
                        "file": str(pfile),
                        "file_mtime": mtime,
                        "last_active": mtime,
                        "chat_count": 0
                    })
            except Exception as e:
                logger.error(f"Error reading project file {pfile}: {e}")

        # Add Outside of Project fallback
        if not any(p["id"] == "outside-of-project" for p in projects):
            outside_file = ANTIGRAVITY_PROJECTS_DIR / "outside-of-project.json"
            mtime = outside_file.stat().st_mtime if outside_file.exists() else 0
            projects.append({
                "id": "outside-of-project",
                "name": "Outside of Project (Вне проектов)",
                "folders": [],
                "normalized_folders": [],
                "file": str(outside_file) if outside_file.exists() else "",
                "file_mtime": mtime,
                "last_active": mtime,
                "chat_count": 0
            })

        return projects

    def list_projects(self) -> List[Dict[str, Any]]:
        """Return list of all configured projects ordered by recency of usage (as in Antigravity)."""
        projects = self._load_projects_raw()

        # Update last_active timestamp and chat_count from chats
        try:
            chats = self.list_chats()
            proj_last_chat = {}
            proj_chat_count = {}
            for c in chats:
                pid = c.get("project_id")
                upd = c.get("updated_at", 0)
                if pid:
                    if upd > proj_last_chat.get(pid, 0):
                        proj_last_chat[pid] = upd
                    proj_chat_count[pid] = proj_chat_count.get(pid, 0) + 1

            for p in projects:
                pid = p["id"]
                chat_upd = proj_last_chat.get(pid, 0)
                p["last_active"] = max(p.get("file_mtime", 0), chat_upd)
                p["chat_count"] = proj_chat_count.get(pid, 0)
        except Exception as e:
            logger.debug(f"Could not compute chat recency for projects: {e}")

        # Sort projects by most recently active first (descending)
        projects.sort(key=lambda x: x.get("last_active", 0), reverse=True)
        return projects

    def get_transcript_path(self, conversation_id: str, prefer_full: bool = True) -> Path:
        """
        Get the path to the transcript file.
        Prefers transcript_full.jsonl which contains 100% untruncated content,
        falling back to transcript.jsonl if full transcript is not available.
        """
        logs_dir = ANTIGRAVITY_BRAIN_DIR / conversation_id / ".system_generated" / "logs"
        if prefer_full:
            full_path = logs_dir / "transcript_full.jsonl"
            if full_path.exists():
                return full_path
        return logs_dir / "transcript.jsonl"

    def list_chats(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all chats/conversations. If project_id is given, filter for that project.
        Extracts title, status, step_count, last_modified_time.
        """
        all_projects = self._load_projects_raw()
        projects_by_id = {p["id"]: p for p in all_projects}

        # 1. Fetch active summaries from RPC
        rpc_summaries = {}
        try:
            rpc_data = self._rpc_call("GetAllCascadeTrajectories", {})
            rpc_summaries = rpc_data.get("trajectorySummaries", {})
        except Exception as e:
            logger.debug(f"GetAllCascadeTrajectories RPC failed or unavailable: {e}")

        # 2. Scan all .db files
        chats = []
        if not ANTIGRAVITY_CONVERSATIONS_DIR.exists():
            return chats

        for db_file in ANTIGRAVITY_CONVERSATIONS_DIR.glob("*.db"):
            cid = db_file.stem
            rpc_info = rpc_summaries.get(cid, {})

            conv_project_id = None
            conv_workspace = None

            # Check RPC workspaces
            for ws in rpc_info.get("workspaces", []):
                uri = ws.get("workspaceFolderAbsoluteUri")
                if uri:
                    conv_workspace = urllib.parse.unquote(uri)

            # Extract from DB trajectory_metadata_blob
            conn = None
            try:
                import sqlite3
                conn = sqlite3.connect(db_file)
                row = conn.execute("SELECT data FROM trajectory_metadata_blob WHERE id='main'").fetchone()
                if row and row[0]:
                    blob = row[0]
                    # Search for workspace URI
                    m_ws = re.search(rb'file:///[^\x00\x1a\x12\n\r]+', blob)
                    if m_ws and not conv_workspace:
                        conv_workspace = urllib.parse.unquote(m_ws.group(0).decode("utf-8", errors="ignore"))

                    # Search for UUIDs in metadata blob
                    # The project ID is one of the UUIDs stored in the metadata blob
                    uuids = [u.decode() for u in re.findall(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', blob)]
                    for u in uuids:
                        if u in projects_by_id:
                            conv_project_id = u
                            break
            except Exception as e:
                logger.debug(f"Error reading DB metadata for {cid}: {e}")
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

            # If project wasn't matched by UUID, try matching by workspace URI
            if not conv_project_id and conv_workspace:
                norm_ws = conv_workspace.lower().rstrip('/')
                for pid, pdata in projects_by_id.items():
                    for f in pdata.get("normalized_folders", []):
                        if f in norm_ws or norm_ws in f:
                            conv_project_id = pid
                            break
                    if conv_project_id:
                        break

            # Fallback if unassigned
            if not conv_project_id:
                conv_project_id = "outside-of-project"

            # Title from RPC summary
            title = (rpc_info.get("summary") or "").strip()
            status = rpc_info.get("status", "CASCADE_RUN_STATUS_IDLE")
            step_count = rpc_info.get("stepCount", 0)
            updated_at = db_file.stat().st_mtime

            # If title is missing, inspect transcript for the initial prompt
            transcript_path = self.get_transcript_path(cid)
            last_message_snippet = ""
            first_user_prompt = ""
            if transcript_path.exists():
                try:
                    with open(transcript_path, "r", encoding="utf-8", errors="replace") as tf:
                        lines = [json.loads(l) for l in tf if l.strip()]
                        if not step_count:
                            step_count = len(lines)
                        if lines:
                            updated_at = transcript_path.stat().st_mtime
                        for l in lines:
                            if l.get("type") == "USER_INPUT" and not first_user_prompt:
                                c = l.get("content", "")
                                m = re.search(r'<USER_REQUEST>(.*?)</USER_REQUEST>', c, re.DOTALL)
                                prompt_c = m.group(1).strip() if m else c.strip()
                                if prompt_c and not prompt_c.startswith("This summary was generated"):
                                    first_line = prompt_c.splitlines()[0].strip()
                                    if first_line:
                                        first_user_prompt = first_line[:40] + ("..." if len(first_line) > 40 else "")

                            if l.get("type") == "PLANNER_RESPONSE":
                                c = l.get("content", "")
                                if c and c.strip():
                                    first_line = c.strip().splitlines()[0].strip()
                                    last_message_snippet = first_line[:50] + ("..." if len(first_line) > 50 else "")
                except Exception as e:
                    logger.debug(f"Error parsing transcript for {cid}: {e}")

            # Assign best available title
            clean_title = title
            if not clean_title or clean_title == "New Conversation":
                clean_title = first_user_prompt or f"Р§Р°С‚ {cid[:8]}"

            # Determine project matching
            target_pid = conv_project_id
            if not target_pid and conv_workspace:
                normalized_ws = conv_workspace.lower().rstrip('/')
                for p in all_projects:
                    for pf in p.get("normalized_folders", []):
                        if normalized_ws == pf or normalized_ws.startswith(pf + "/"):
                            target_pid = p["id"]
                            break
                    if target_pid:
                        break

            if not target_pid:
                target_pid = "outside-of-project"

            # Filter by project_id if requested
            if project_id and target_pid != project_id:
                continue

            status_clean = "RUNNING" if "RUNNING" in status else "IDLE"

            chats.append({
                "id": cid,
                "title": clean_title,
                "project_id": target_pid,
                "workspace": conv_workspace,
                "status": status_clean,
                "step_count": step_count,
                "last_snippet": last_message_snippet,
                "updated_at": updated_at
            })

        # Sort chats by most recently updated
        chats.sort(key=lambda x: x["updated_at"], reverse=True)
        logger.info(f"list_chats(project_id={project_id}): found {len(chats)} chats")
        return chats

    def get_chat_title(self, conversation_id: str) -> str:
        """Get best display title for conversation: DB summary -> first user prompt -> ID."""
        try:
            rpc_data = self._rpc_call("GetAllCascadeTrajectories", {})
            sum_text = rpc_data.get("trajectorySummaries", {}).get(conversation_id, {}).get("summary", "").strip()
            if sum_text and sum_text != "New Conversation":
                return sum_text
        except Exception:
            pass

        try:
            import sqlite3
            db_path = ANTIGRAVITY_CONVERSATIONS_DIR / f"{conversation_id}.db"
            if db_path.exists():
                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT summary FROM trajectory_metadata WHERE id='main'").fetchone()
                conn.close()
                if row and row[0]:
                    s = row[0].strip()
                    if s and s != "New Conversation":
                        return s
        except Exception:
            pass

        transcript_path = self.get_transcript_path(conversation_id)
        if transcript_path.exists():
            try:
                with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        if item.get("type") == "USER_INPUT":
                            c = item.get("content", "")
                            m = re.search(r'<USER_REQUEST>(.*?)</USER_REQUEST>', c, re.DOTALL)
                            p = m.group(1).strip() if m else c.strip()
                            if p and not p.startswith("This summary was generated"):
                                first_line = p.splitlines()[0].strip()
                                if first_line:
                                    return first_line[:50] + ("..." if len(first_line) > 50 else "")
            except Exception:
                pass
        return f"Р§Р°С‚ {conversation_id[:8]}"

    def get_chat_history(self, conversation_id: str, limit: int = 15, detailed: bool = False) -> List[Dict[str, Any]]:
        """
        Extract recent messages from transcript_full.jsonl (or transcript.jsonl).
        Returns clean user requests and 100% full assistant replies, plus optional tool summaries.
        """
        transcript_path = self.get_transcript_path(conversation_id)
        if not transcript_path.exists():
            logger.warning(f"Transcript not found for conversation {conversation_id}")
            return []

        messages = []
        try:
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    t = item.get("type")
                    step_idx = item.get("step_index", 0)
                    created_at = item.get("created_at", "")
                    content = item.get("content", "")

                    if t == "USER_INPUT":
                        m_req = re.search(r'<USER_REQUEST>(.*?)</USER_REQUEST>', content, re.DOTALL)
                        if m_req:
                            clean_text = m_req.group(1).strip()
                        else:
                            clean_text = re.sub(r'<(?:USER_REQUEST|ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE|CONTEXT_SUMMARY)>.*?</(?:USER_REQUEST|ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE|CONTEXT_SUMMARY)>', '', content, flags=re.DOTALL)
                            clean_text = re.sub(r'<[^>]+>', '', clean_text).strip()
                        if clean_text:
                            messages.append({
                                "role": "user",
                                "text": clean_text,
                                "step_index": step_idx,
                                "created_at": created_at
                            })

                    elif t == "PLANNER_RESPONSE":
                        if content and content.strip():
                            messages.append({
                                "role": "assistant",
                                "text": content.strip(),
                                "step_index": step_idx,
                                "created_at": created_at
                            })

                    elif detailed and t in ("GENERIC", "CHECKPOINT"):
                        tool_calls = item.get("tool_calls", [])
                        if tool_calls:
                            tools_str = ", ".join(f"`{tc.get('name')}`" for tc in tool_calls if tc.get('name'))
                            messages.append({
                                "role": "tool",
                                "text": f"рџ›  РРЅСЃС‚СЂСѓРјРµРЅС‚С‹: {tools_str}",
                                "step_index": step_idx,
                                "created_at": created_at
                            })
                        elif content and ("Created At" in content or "Completed At" in content):
                            first_line = content.splitlines()[0]
                            messages.append({
                                "role": "tool",
                                "text": f"вљ™пёЏ {first_line[:100]}",
                                "step_index": step_idx,
                                "created_at": created_at
                            })
        except Exception as e:
            logger.error(f"Error reading transcript for {conversation_id}: {e}")

        logger.info(f"get_chat_history({conversation_id[:8]}...): loaded {len(messages)} messages (limit={limit})")
        return messages[-limit:] if limit > 0 else messages

    def get_transcript_step_count(self, conversation_id: str) -> int:
        """Get the total number of lines/steps currently in transcript."""
        transcript_path = self.get_transcript_path(conversation_id)
        if not transcript_path.exists():
            return 0
        try:
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    def _resolve_model_config(self, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Resolve a valid model configuration for Cascade.
        Ensures Antigravity never fails with 'neither PlanModel nor RequestedModel specified'
        even if the UI model selector was set to None.
        """
        # 1. Check existing conversation's last model if available
        if conversation_id:
            try:
                meta = self._rpc_call("GetCascadeTrajectoryGeneratorMetadata", {"cascadeId": conversation_id})
                gen_list = meta.get("generatorMetadata", [])
                if gen_list:
                    last_model = gen_list[-1].get("chatModel", {}).get("model")
                    if last_model:
                        return {"model": last_model}
            except Exception as e:
                logger.debug(f"Could not get model from generator metadata: {e}")

        # 2. Check GetCascadeModelConfigData for default or first recommended
        try:
            cfg = self._rpc_call("GetCascadeModelConfigData", {})
            default_model = cfg.get("defaultOverrideModelConfig", {}).get("modelOrAlias")
            if default_model and default_model.get("model"):
                return default_model
            configs = cfg.get("clientModelConfigs", [])
            for c in configs:
                m = c.get("modelOrAlias")
                if m and m.get("model"):
                    return m
        except Exception as e:
            logger.debug(f"Could not get model from config data: {e}")

        # 3. Fallback to Gemini 3.8 Flash High
        return {"model": "MODEL_PLACEHOLDER_M318"}

    def get_recent_antigravity_error(self) -> Optional[str]:
        """
        Inspect recent lines of language_server.log to diagnose failures.
        Returns a friendly Russian description of the root cause if detected.
        """
        log_path = Path.home() / "AppData" / "Roaming" / "Antigravity" / "logs" / "language_server.log"
        if not log_path.exists():
            return None
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in reversed(lines[-80:]):
                if "User location is not supported" in line:
                    return "рџЊЌ Р РµРіРёРѕРЅР°Р»СЊРЅРѕРµ РѕРіСЂР°РЅРёС‡РµРЅРёРµ Google Cloud (`User location is not supported`). РџСЂРѕРІРµСЂСЊС‚Рµ VPN/РїСЂРѕРєСЃРё РЅР° РєРѕРјРїСЊСЋС‚РµСЂРµ."
                if "neither PlanModel nor RequestedModel specified" in line:
                    return "рџ¤– Р’ РґРёР°Р»РѕРіРµ РЅРµ Р±С‹Р»Р° Р·Р°РґР°РЅР° РјРѕРґРµР»СЊ (PlanModel/RequestedModel)."
                if "failed to construct executor" in line:
                    return f"вљ™пёЏ РЎР±РѕР№ РєРѕРЅСЃС‚СЂСѓРєС‚РѕСЂР° Р°РіРµРЅС‚Р°: {line.strip()[-120:]}"
                if "Agent execution terminated due to error" in line:
                    return "вљ пёЏ Р’С‹РїРѕР»РЅРµРЅРёРµ Р°РіРµРЅС‚Р° Р°РІР°СЂРёР№РЅРѕ РїСЂРµСЂРІР°РЅРѕ Antigravity."
        except Exception as e:
            logger.debug(f"Error checking language_server.log: {e}")
        return None

    def save_uploaded_image(
        self,
        conversation_id: str,
        image_bytes: bytes,
        filename: Optional[str] = None,
        ext: str = ".png"
    ) -> Path:
        """
        Save an uploaded image to native Antigravity brain storage:
        <ANTIGRAVITY_BRAIN_DIR>/<conversation_id>/.user_uploaded/media_<timestamp><ext>
        """
        target_dir = ANTIGRAVITY_BRAIN_DIR / conversation_id / ".user_uploaded"
        target_dir.mkdir(parents=True, exist_ok=True)
        if not filename:
            timestamp_ms = int(time.time() * 1000)
            if not ext.startswith("."):
                ext = f".{ext}"
            filename = f"media_{timestamp_ms}{ext}"
        target_path = target_dir / filename
        with open(target_path, "wb") as f:
            f.write(image_bytes)
        logger.info(f"Saved uploaded image for chat {conversation_id[:8]} at {target_path}")
        return target_path

    def build_image_payload(
        self,
        image_path: Path,
        image_bytes: bytes,
        mime_type: str = "image/png"
    ) -> Dict[str, Any]:
        """
        Build Antigravity ImageData dictionary for SendUserCascadeMessage RPC.
        """
        return {
            "base64Data": base64.b64encode(image_bytes).decode("ascii"),
            "mimeType": mime_type,
            "uri": image_path.as_uri()
        }

    def send_user_message(
        self,
        conversation_id: str,
        text: str,
        images: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """
        Send a user message directly to Antigravity conversation via SendUserCascadeMessage RPC.
        Supports text and optional list of images (ImageData).
        Antigravity will wake up, process the message, and run its agent.
        """
        logger.info(f"Sending message to conversation {conversation_id[:8]}...: {text[:60]} (images={len(images) if images else 0})")
        model_obj = self._resolve_model_config(conversation_id)
        payload = {
            "metadata": {
                "ideName": "antigravity",
                "ideVersion": "2.10.0",
                "locale": "ru"
            },
            "cascadeId": conversation_id,
            "items": [
                {"text": text}
            ],
            "cascadeConfig": {
                "plannerConfig": {
                    "requestedModel": model_obj
                },
                "applyModelDefaultOverride": True
            }
        }
        if images:
            payload["images"] = images
        res = self._rpc_call("SendUserCascadeMessage", payload)
        logger.info(f"Message sent successfully to {conversation_id[:8]}...")
        return res is not None

    def create_new_chat(self, prompt: str, title: Optional[str] = None, model: str = "flash") -> str:
        """Create a new conversation using direct language_server agentapi."""
        logger.info(f"Creating new conversation: prompt={prompt[:50]}, model={model}, title={title}")
        
        # Locate language_server.exe directly to prevent WinError 2 on Windows
        ls_exe = Path.home() / "AppData" / "Local" / "Programs" / "antigravity" / "resources" / "bin" / "language_server.exe"
        if ls_exe.exists():
            cmd = [str(ls_exe), "agentapi", "new-conversation", f"--model={model}"]
            shell_use = False
        else:
            cmd = ["agentapi.bat", "new-conversation", f"--model={model}"]
            shell_use = True

        if title:
            cmd.append(f"--title={title}")
        cmd.append(prompt)

        logger.info(f"Running agentapi command: {cmd}")
        res = subprocess.run(cmd, capture_output=True, text=True, shell=shell_use)
        
        if res.returncode != 0:
            err_msg = res.stderr or res.stdout
            logger.error(f"Failed to create conversation (exit code {res.returncode}): {err_msg}")
            raise RuntimeError(f"Ошибка создания диалога: {err_msg}")

        try:
            data = json.loads(res.stdout)
            cid = data.get("response", {}).get("newConversation", {}).get("conversationId")
            if cid:
                logger.info(f"New conversation created with ID: {cid}")
                return cid
        except Exception as e:
            logger.warning(f"Could not parse agentapi JSON response: {e}. Output: {res.stdout}")

        # Fallback: find newly created DB file in conversations dir
        time.sleep(1)
        dbs = sorted(ANTIGRAVITY_CONVERSATIONS_DIR.glob("*.db"), key=lambda f: f.stat().st_mtime, reverse=True)
        if dbs:
            cid = dbs[0].stem
            logger.info(f"Determined new conversation ID from latest DB: {cid}")
            return cid

        raise RuntimeError("Диалог создан, но не удалось получить его ID.")

    def get_chat_status(self, conversation_id: str) -> str:
        """Check if chat is RUNNING or IDLE."""
        try:
            rpc_data = self._rpc_call("GetAllCascadeTrajectories", {})
            summaries = rpc_data.get("trajectorySummaries", {})
            info = summaries.get(conversation_id)
            if info:
                status = info.get("status", "")
                if status == "CASCADE_RUN_STATUS_RUNNING":
                    return "RUNNING"
                return "IDLE"
        except Exception:
            pass
        return "IDLE"

    def stop_chat(self, conversation_id: str) -> bool:
        """Force stop a running conversation in Antigravity."""
        try:
            resp = self._rpc_call("ForceStopCascadeTree", {"conversation_id": conversation_id})
            stopped = resp.get("stoppedConversationIds", [])
            logger.info(f"ForceStopCascadeTree for {conversation_id[:8]}... stopped: {stopped}")
            return bool(stopped)
        except Exception as e:
            logger.error(f"Failed to stop chat {conversation_id}: {e}")
            return False
