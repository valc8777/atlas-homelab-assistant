# ATLAS Shortcuts Endpoint

Purpose: give iPhone Shortcuts and Apple Watch a small private entrypoint for short ATLAS queries without exposing raw `Ollama`.

## Access Model

- intended client: iPhone Shortcuts first
- follow-on client: Apple Watch triggering the same Shortcut
- authentication: `Authorization: Bearer <token>`
- scope: read-only ATLAS routing only
- network: private Tailscale path first

## Endpoint

Current working private URL:

- `http://100.64.1.10:8787/ask`

Preferred future HTTPS URL after the broader `ai-stack` Caddy plus Tailscale certificate path is cleaned up:

- `https://ai-stack.example-tailnet.ts.net/atlas/ask`

Current health check:

- `http://100.64.1.10:8787/health`

Future HTTPS health check:

- `https://ai-stack.example-tailnet.ts.net/atlas/health`

## Request

Method:

- `POST`

Headers:

- `Authorization: Bearer <your token from /opt/stacks/ai-stack/atlas-shortcuts/atlas-shortcuts.env>`
- `Content-Type: application/json`

Body:

```json
{
  "question": "Which host runs Open WebUI?"
}
```

Optional detailed body:

```json
{
  "question": "What's the weather in Portland?",
  "details": true
}
```

## Response

```json
{
  "ok": true,
  "question": "Which host runs Open WebUI?",
  "answer": "Open WebUI runs on ai-stack.",
  "route": "homelab",
  "tool": "openwebui-chat",
  "source": "Homelab Source of Truth",
  "speak_text": "Open WebUI runs on ai-stack."
}
```

## Shortcut Shape

Suggested first Shortcut flow:

1. `Dictate Text`
2. `Get Contents of URL`
3. URL: `http://100.64.1.10:8787/ask`
4. Method: `POST`
5. Headers:
   - `Authorization: Bearer ...`
   - `Content-Type: application/json`
6. JSON body:
   - `question`: dictated text
7. `Get Dictionary Value` -> `answer`
8. `Speak Text`

## Build It On iPhone

Create a new Shortcut named:

- `Ask ATLAS`

Add these actions in order:

1. `Dictate Text`
   - Prompt: `Ask ATLAS`
   - Language: your normal dictation language
   - Stop Listening: `After Pause`

2. `Text`
   - Set the text to:

```json
{"question":"[[DICTATION]]"}
```

Replace `[[DICTATION]]` by tapping into the field and inserting the `Dictated Text` variable from step 1.

3. `Get Contents of URL`
   - URL: `http://100.64.1.10:8787/ask`
   - Method: `POST`
   - Headers:
     - `Authorization` = `Bearer <your token>`
     - `Content-Type` = `application/json`
   - Request Body: `File`
   - File: the `Text` result from step 2

4. `Get Dictionary Value`
   - Dictionary: result from `Get Contents of URL`
   - Key: `answer`

5. `Speak Text`
   - Text: result from step 4

Optional step:

6. `Show Result`
   - Result: result from step 4

That gives you:

- spoken reply
- visible text reply
- watch-compatible trigger behavior

## Better Shortcut Variant

If you want one-tap typed fallback instead of dictation failure:

1. `Choose from Menu`
   - `Speak to ATLAS`
   - `Type to ATLAS`
2. For `Speak to ATLAS`
   - use `Dictate Text`
3. For `Type to ATLAS`
   - use `Ask for Text`
4. Feed either result into the same HTTP request body

## Apple Watch Version

After the iPhone Shortcut works:

1. Open the Shortcut details
2. Enable `Show on Apple Watch`
3. Add it to your watch face or Smart Stack

Best first watch behavior:

- tap Shortcut
- dictate question
- wait for returned text
- let iPhone or watch read it aloud

Do not start with a wake word. Push-to-talk is the right first interaction model here.

## Token Placement

Put the token directly in the Shortcut header value:

- `Authorization` = `Bearer <token>`

Do not store the token in Notes, Messages, or a plaintext repo file.

To read the token on `ai-stack`:

```sh
ssh cerebro@100.64.1.10 "sed -n 's/^ATLAS_SHORTCUTS_TOKEN=//p' /opt/stacks/ai-stack/atlas-shortcuts/atlas-shortcuts.env"
```

## First Test Questions

Use these first:

- `Which host runs Open WebUI?`
- `What is the weather in Portland?`
- `Tell me about the album The Downward Spiral`

That exercises:

- homelab retrieval
- live weather routing
- structured music lookup

## Known Current State

- The working client path today is the private Tailscale IP: `http://100.64.1.10:8787/ask`
- The private HTTPS vanity path through Caddy is still a cleanup item
- The endpoint is read-only
- Short answers work best for watch use

## Notes

- Let the phone or watch speak the returned `answer`; that is simpler than generating server-side audio first.
- The current endpoint is designed for short read-only questions.
- Keep the bearer token in the Shortcut, not in a public note or message.
- The current working path is the private Tailscale IP on port `8787`.
- The HTTPS path is still tied to the broader `ai-stack` Caddy plus Tailscale certificate setup and should be treated as a follow-up cleanup item.
