# kai-warroom-brain

FastAPI service that powers the Kai War Room avatar chat. Receives a user
message, runs it through Claude with the Kai system prompt, parses out
`<region>...</region>` tags into a pulse schedule, generates TTS audio,
and returns audio + regions to the frontend.

## Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in ANTHROPIC_API_KEY + OPENAI_API_KEY + BRAIN_TOKEN
uvicorn main:app --reload --port 7787
```

Smoke test:

```bash
curl -s http://localhost:7787/healthz | jq

curl -s -X POST http://localhost:7787/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(cat .env | grep BRAIN_TOKEN | cut -d= -f2)" \
  -d '{"message":"what is the hottest trade today?"}' | jq 'del(.audio_base64)'
```

## API

### `GET /healthz`

Returns `{ ok, claude_ready, tts_ready, model, tts_model }`.

### `POST /chat`

Headers: `Authorization: Bearer <BRAIN_TOKEN>`

Body:

```json
{ "message": "what's the hottest trade?" }
```

Response:

```json
{
  "text": "Tape's risk-on. NVDA broke eight fifty with volume...",
  "audio_base64": "<mp3 bytes b64>",
  "audio_mime": "audio/mpeg",
  "regions": [
    { "id": "market", "peak": 0.9, "decay_ms": 2200, "at_second": 0.0 },
    { "id": "technicals", "peak": 0.9, "decay_ms": 2200, "at_second": 0.9 }
  ],
  "duration_estimate_sec": 4.1
}
```

`regions[].at_second` is a frontend hint for when to fire each pulse during
playback; it's estimated from the character offset of each region tag in the
clean reply at ~14 chars/sec (OpenAI `tts-1` speaking rate).

## Roadmap

- [ ] Wire the 5 Phase-1 tools (`get_hottest_trades`, `analyze_stock`, etc.)
      into Claude tool-use calls against Supabase + Polygon.
- [ ] Stream the TTS chunks back via SSE instead of buffering the full mp3,
      so time-to-first-audio drops from ~2-3s to ~500ms.
- [ ] Swap OpenAI TTS for Voicebox (Kway voice profile) via Cloudflare Tunnel
      with OpenAI fallback when the tunnel is down.
- [ ] Deepgram STT endpoint for the mic record button on the frontend.
