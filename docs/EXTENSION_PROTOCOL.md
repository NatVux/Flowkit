# Extension <-> Python Protocol

The Manifest V3 service worker connects to `ws://127.0.0.1:9222`. The Python agent sends commands; the extension executes Flow work in the signed-in Flow tab and returns responses through the authenticated local callback endpoint when a request id is present.

## Endpoints and authentication

- WebSocket URL `ws://127.0.0.1:9222` (`AGENT_WS_URL`) and callback URL `http://127.0.0.1:8100/api/ext/callback` are hard-coded in `extension/background.js`; `manifest.json` grants host permission for `http://127.0.0.1:8100/*` only. The agent's `WS_PORT` / `API_PORT` must stay at their defaults unless these are edited.
- On each WebSocket connect the agent sends `{"type":"callback_secret","secret":"…"}`. The secret is generated once per agent process. The extension stores it and sends it as `X-Callback-Secret` on every callback; a wrong or missing secret gets HTTP 401. A callback for an id the agent is no longer waiting for returns `{"ok": false, "reason": "no matching pending request"}`.
- The agent waits up to 300 s per command by default. If no extension is connected, the call returns `{"error": "Extension not connected"}` without being sent.
- The service worker keeps itself alive with a `keepAlive` alarm (every 0.4 min) and reconnects through a `reconnect` alarm about 5 s after an unexpected close.

## Command envelope

```json
{"id":"uuid-v4","method":"batch_rpc","params":{"rpcid":"...","freq":"..."}}
```

Supported methods are `batch_rpc`, `api_request` (legacy), `trpc_request` (legacy), `solve_captcha`, and `get_status`. Request ids must be UUIDs. The extension rejects unknown methods, malformed params, duplicate active ids, and recently completed ids.

## Control messages

The extension sends:

- `extension_ready`: version, Flow URL support, token age, diagnostic status.
- `extension_status`: status, `flowTabAvailable`, `flowTabId`, and `busy`.
- `token_captured`: internal session signal; token values are never logged.
- `media_urls_refresh`: validated media URL observations from the legacy labs page.
- `ping`/`pong`: local keepalive.

Diagnostic status is one of:

- `disconnected`: the local WebSocket is not open.
- `flow_tab_unavailable`: connected to Python, but no usable Flow tab exists.
- `flow_tab_available`: connected and a non-discarded Flow tab is available.
- `busy`: a Flow operation is currently executing.

The extension reports tab closure, navigation, reload, and discarded-tab changes through `extension_status`. It reconnects through a Chrome alarm after unexpected WebSocket closure. A manual disconnect suppresses automatic reconnect until the user selects reconnect.

## Response envelope

```json
{"id":"uuid-v4","status":200,"data":"..."}
```

Errors use the same id with `status` and `error`. Responses are delivered only to the WebSocket connection that owns the request. Unknown ids, malformed responses, duplicate responses, and responses from another connection are ignored.

The bridge does not forward cookies or Flow tokens to content scripts or Python. Flow page requests remain in the page MAIN world with browser credentials; the extension only returns the bounded operation result needed by the agent.
