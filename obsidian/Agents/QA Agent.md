# QA Agent

## Testing

No automated test suite exists. All testing is manual.

## Manual Test Checklist

### Agent Testing
- [ ] STT: Verify Deepgram Nova-3 recognizes speech
- [ ] LLM: Verify responses in all 3 models (OpenAI, Gemini, DeepSeek)
- [ ] TTS: Verify sonic-3 voice output is clear and natural
- [ ] Bilingual: Verify English → Hindi switching
- [ ] end_call: Verify LLM calls end_call and disconnects room
- [ ] search_knowledge_base: Verify KB search returns relevant content
- [ ] Inactivity monitor: Verify 5s prompt → 10s disconnect
- [ ] Farewell safety net: Verify goodbye detection → force disconnect
- [ ] Call limiter: Verify 2m30s farewell → 3m hard kill

### Webhook Testing
- [ ] Valid payload: Verify agent dispatch + SIP call initiation
- [ ] Missing phone: Verify appropriate error response
- [ ] Invalid trunk: Verify error handling
- [ ] SIP failure: Verify 503 response and room cleanup
- [ ] Duplicate webhook: Verify repeated `call_id` is processed immediately (no dedup lock rejection)
- [ ] Per-provider capacity: Verify 503 when Plivo/Zadarma/VoiceLink/Twilio at limit
- [ ] Global capacity: Verify 503 when total calls = MAX_CONCURRENCY

### SIP Trunk Testing
- [ ] Create outbound trunks for all providers
- [ ] Verify trunk appears in LiveKit dashboard
- [ ] List trunks returns correct data
- [ ] Delete trunk removes configuration
- [ ] Create inbound trunk + dispatch rule via `/v1/sip/inbound/setup`

### Dashboard Testing
- [ ] Login with valid credentials → JWT token
- [ ] Login with invalid credentials → 401
- [ ] Metrics display correct values
- [ ] Active calls update in real time via SSE
- [ ] Call history paginates correctly

### Post-Call Testing
- [ ] Recording uploads to S3 and URL is accessible
- [ ] Transcript is captured correctly
- [ ] Webhook delivers to backend
- [ ] Call log saves to PostgreSQL
- [ ] TOS telemetry posts successfully

### KB Testing
- [ ] File ingestion: PDF, TXT, MD
- [ ] Text ingestion via API
- [ ] URL ingestion extracts content
- [ ] KB chat endpoint returns relevant results
- [ ] Document deletion removes all chunks
- [ ] Multi-KB collection lookup returns correct collections for org

### MCP Server Testing
- [ ] `get_patient_info` returns correct patient
- [ ] `get_hospitals` lists locations
- [ ] `get_doctors` lists doctors
- [ ] `create_appointment` books successfully
- [ ] `get_appointments` returns correct results
- [ ] `get_call_history` returns call records
- [ ] `get_db_status` reports connection + table stats

## Future Testing

- [ ] pytest for utility functions (`utils.py`, `knowledge_base.py`)
- [ ] Integration test: full call flow with LiveKit test rooms
- [ ] Mock external services for deterministic testing
