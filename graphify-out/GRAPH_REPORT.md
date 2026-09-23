# Graph Report - .  (2026-09-23)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 128 nodes · 335 edges · 10 communities (9 shown, 1 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 7 edges (avg confidence: 0.63)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `2835834b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- bot.py
- get_user_session
- config.py
- AntigravityBridge
- ._rpc_call
- .get_transcript_path
- TestAntigravityBotBridge
- InlineKeyboardMarkup
- .list_chats
- .wait_for_response

## God Nodes (most connected - your core abstractions)
1. `AntigravityBridge` - 30 edges
2. `get_user_session()` - 23 edges
3. `is_user_allowed()` - 17 edges
4. `safe_edit_markdown()` - 11 edges
5. `get_main_menu_keyboard()` - 11 edges
6. `build_chat_pages()` - 10 edges
7. `_run_message_flow()` - 10 edges
8. `TestAntigravityBotBridge` - 10 edges
9. `get_chat_page_keyboard()` - 9 edges
10. `process_new_chat_prompt()` - 9 edges

## Surprising Connections (you probably didn't know these)
- `TestAntigravityBotBridge` --uses--> `AntigravityBridge`  [INFERRED]
  test_bot_bridge.py → bridge.py
- `BotStates` --uses--> `AntigravityBridge`  [INFERRED]
  bot.py → bridge.py
- `BotStates` --uses--> `ConversationWatcher`  [INFERRED]
  bot.py → watcher.py
- `is_user_allowed()` --calls--> `add_allowed_user_id()`  [EXTRACTED]
  bot.py → config.py
- `is_user_allowed()` --calls--> `get_allowed_user_ids()`  [EXTRACTED]
  bot.py → config.py

## Import Cycles
- None detected.

## Communities (10 total, 1 thin omitted)

### Community 0 - "bot.py"
Cohesion: 0.26
Nodes (23): build_chat_pages(), cb_all_chats(), cb_chat_page(), cb_dl_step(), cb_new_chat(), cb_open_chat(), cb_page_info(), cb_projects() (+15 more)

### Community 1 - "get_user_session"
Cohesion: 0.27
Nodes (20): cb_menu(), cmd_chats(), cmd_current(), cmd_help(), cmd_menu(), cmd_newchat_cmd(), cmd_projects(), cmd_start() (+12 more)

### Community 2 - "config.py"
Cohesion: 0.27
Nodes (13): start_bot(), add_allowed_user_id(), get_allowed_user_ids(), get_api_hash(), get_api_id(), get_bot_token(), get_phone(), get_session_path() (+5 more)

### Community 3 - "AntigravityBridge"
Cohesion: 0.23
Nodes (6): BotStates, AntigravityBridge, Inspect recent lines of language_server.log to diagnose failures. Returns a…, Create a new conversation using direct language_server agentapi., StatesGroup, ConversationWatcher

### Community 4 - "._rpc_call"
Cohesion: 0.18
Nodes (6): Call a Connect-RPC method on the local LanguageServerService., Auto-detect active HTTPS port and csrf token of running language_server.exe., Resolve a valid model configuration for Cascade. Ensures Antigravity never…, Send a user message directly to Antigravity conversation via…, Check if chat is RUNNING or IDLE., Force stop a running conversation in Antigravity.

### Community 5 - ".get_transcript_path"
Cohesion: 0.18
Nodes (6): Get the path to the transcript file. Prefers transcript_full.jsonl which…, Get best display title for conversation: DB summary -> first user prompt -> ID., Extract recent messages from transcript_full.jsonl (or transcript.jsonl).…, Get the total number of lines/steps currently in transcript., Save an uploaded image to native Antigravity brain storage:…, Path

### Community 6 - "TestAntigravityBotBridge"
Cohesion: 0.20
Nodes (3): TestAntigravityBotBridge, Split a message into chunks of maximum max_len characters. Handles long single…, split_telegram_message()

### Community 7 - "InlineKeyboardMarkup"
Cohesion: 0.25
Nodes (8): cb_logs(), cmd_logs(), get_projects_keyboard(), InlineKeyboardMarkup, get_recent_logs(), Read the last N lines from the log file., Configures console and file logging with UTF-8 support., setup_logging()

### Community 8 - ".list_chats"
Cohesion: 0.31
Nodes (5): Any, Return list of all configured projects from disk without chat-based recency…, Return list of all configured projects ordered by recency of usage (as in…, List all chats/conversations. If project_id is given, filter for that project.…, Build Antigravity ImageData dictionary for SendUserCascadeMessage RPC.

## Knowledge Gaps
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AntigravityBridge` connect `AntigravityBridge` to `bot.py`, `config.py`, `._rpc_call`, `.get_transcript_path`, `TestAntigravityBotBridge`, `.list_chats`?**
  _High betweenness centrality (0.539) - this node is a cross-community bridge._
- **Why does `TestAntigravityBotBridge` connect `TestAntigravityBotBridge` to `AntigravityBridge`?**
  _High betweenness centrality (0.089) - this node is a cross-community bridge._
- **Why does `ConversationWatcher` connect `AntigravityBridge` to `bot.py`, `.wait_for_response`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `AntigravityBridge` (e.g. with `BotStates` and `TestAntigravityBotBridge`) actually correct?**
  _`AntigravityBridge` has 3 INFERRED edges - model-reasoned connections that need verification._