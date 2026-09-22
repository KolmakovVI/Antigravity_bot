import asyncio
import json
import time
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Awaitable
from config import ANTIGRAVITY_BRAIN_DIR
from bridge import AntigravityBridge

def split_telegram_message(text: str, max_len: int = 3900) -> List[str]:
    """
    Split a message into chunks of maximum max_len characters.
    Handles long single lines and preserves markdown code fences cleanly.
    """
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    # Pre-split lines that are themselves longer than max_len
    raw_lines = text.split("\n")
    lines = []
    for line in raw_lines:
        if len(line) <= max_len:
            lines.append(line)
        else:
            # Chunk long line
            for i in range(0, len(line), max_len):
                lines.append(line[i:i + max_len])

    chunks = []
    current_chunk = []
    current_len = 0
    in_code_block = False
    code_fence_lang = ""

    for line in lines:
        line_strip = line.strip()
        is_fence = line_strip.startswith("```")

        # Determine if adding this line exceeds max_len (including closing fence if in code block)
        extra_len = len(line) + 1
        closing_reserve = 4 if in_code_block else 0
        if current_len + extra_len + closing_reserve > max_len and current_chunk:
            if in_code_block:
                current_chunk.append("```")
            chunks.append("\n".join(current_chunk))

            current_chunk = []
            if in_code_block:
                current_chunk.append(f"```{code_fence_lang}")
                current_len = len(current_chunk[0]) + 1
            else:
                current_len = 0

        # Update code block state AFTER determining chunk boundary
        if is_fence:
            if not in_code_block:
                in_code_block = True
                code_fence_lang = line_strip[3:].strip()
            else:
                in_code_block = False
                code_fence_lang = ""

        current_chunk.append(line)
        current_len += len(line) + 1

    if current_chunk:
        if in_code_block:
            current_chunk.append("```")
        chunks.append("\n".join(current_chunk))

    return chunks

class ConversationWatcher:
    def __init__(self, bridge: AntigravityBridge):
        self.bridge = bridge

    async def wait_for_response(
        self,
        conversation_id: str,
        initial_step_count: int,
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
        timeout: int = 300,
        poll_interval: float = 0.8
    ) -> Optional[str]:
        """
        Poll transcript.jsonl and Antigravity status until the agent finishes responding.
        Returns the final PLANNER_RESPONSE text or None if execution failed.
        """
        transcript_path = self.bridge.get_transcript_path(conversation_id)
        start_time = time.time()
        last_reported_tool = None
        last_response_text = None
        saw_running = False

        while time.time() - start_time < timeout:
            elapsed = time.time() - start_time
            status = self.bridge.get_chat_status(conversation_id)
            if status == "RUNNING":
                saw_running = True
            
            if transcript_path.exists():
                try:
                    with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = [json.loads(line) for line in f if line.strip()]

                    current_steps = len(lines)
                    if current_steps > initial_step_count:
                        # Inspect ONLY newly appended steps after initial_step_count
                        for item in lines[initial_step_count:]:
                            t = item.get("type")
                            content = item.get("content", "")
                            
                            if t == "PLANNER_RESPONSE" and content.strip():
                                last_response_text = content.strip()

                            elif t in ("GENERIC", "CHECKPOINT") and on_progress:
                                tool_calls = item.get("tool_calls", [])
                                if tool_calls:
                                    t_names = ", ".join(f"`{tc.get('name')}`" for tc in tool_calls if tc.get('name'))
                                    progress_msg = f"🛠 Выполняю инструмент: {t_names}..."
                                    if progress_msg != last_reported_tool:
                                        last_reported_tool = progress_msg
                                        await on_progress(progress_msg)
                except Exception as e:
                    pass

            # Check termination conditions
            if status == "IDLE":
                if last_response_text:
                    return last_response_text
                # Agent ran and stopped without writing a PLANNER_RESPONSE
                if saw_running and elapsed > 2.0:
                    break
                # If 5+ seconds have passed and agent was never seen running and no response produced
                if elapsed > 5.0 and not saw_running:
                    break

            await asyncio.sleep(poll_interval)

        return last_response_text
