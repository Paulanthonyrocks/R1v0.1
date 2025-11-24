## Refactor WebSocket JSON Decoding and Error Handling - (2025-11-20)

**Summary:**
Addressed recurring `json.JSONDecodeError` warnings in WebSocket connections by refactoring the message reception logic in `ws.py`. The changes ensure proper client disconnection and improved debugging information when malformed JSON messages are received.

**Key Activities:**
- Modified `backend/app/models/websocket.py` to add `__INTERNAL_PING` to `WebSocketMessageTypeEnum` for better schema completeness, although it was already skipped in processing.
- Refactored `websocket_endpoint` in `backend/app/routers/ws.py` to explicitly handle `json.JSONDecodeError`, `WebSocketDisconnect`, and `RuntimeError` during message reception.
- Introduced `websocket.receive_text()` followed by `json.loads()` to allow logging of raw malformed data for debugging.
- Ensured `connection_manager.disconnect(client_id)` is called explicitly upon JSON decoding failure or unexpected disconnections.

**Changes Made:**
- **Files Modified:**
    - `backend/app/models/websocket.py`: Added `__INTERNAL_PING` to `WebSocketMessageTypeEnum`.
    - `backend/app/routers/ws.py`: Refactored `websocket_endpoint` to improve JSON decoding error handling and client disconnection logic.

**Dependencies & Environment:**
- No new dependencies were added.

**Technical Decisions:**
- Switched from `websocket.receive_json()` to `websocket.receive_text()` followed by `json.loads()` to enable logging of raw message content upon JSON decoding errors. This provides crucial diagnostic information for identifying the source of malformed messages.
- Explicitly calling `connection_manager.disconnect(client_id)` in all disconnection paths ensures that the connection manager accurately reflects active connections, preventing orphaned entries.

**Current Status:**
- ✅ Changes implemented and verified locally.

**Next Steps:**
- Monitor backend logs for occurrences of `json.JSONDecodeError` to confirm the effectiveness of the logging improvements and to identify any client-side issues causing malformed JSON.

**Open Questions/Challenges:**
- The root cause of the malformed JSON messages from clients is still unknown. Further monitoring with the improved logging will be necessary to diagnose.

**Quality Assurance Checklist:**
- [ ] TypeScript compilation: ✅ (N/A for backend Python changes)
- [ ] Linting issues: 0 new violations (Ran `ruff check backend/` - 0 issues)
- [ ] Test coverage: N/A (No new tests written for this specific fix, as it's error handling for unexpected input. Existing tests should still pass.)
- [ ] Build time: N/A
- [ ] Bundle size: N/A
- [ ] Accessibility: N/A

**Performance/Quality Metrics:**
- Linting Issues: 0 resolved (No new issues introduced)