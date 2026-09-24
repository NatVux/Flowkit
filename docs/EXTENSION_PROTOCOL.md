# Extension <-> Python Protocol

The Manifest V3 service worker connects to `ws://127.0.0.1:9222`. The Python agent sends commands; the extension executes Flow work in the signed-in Flow tab and returns responses through the authenticated local callback endpoint when a request id is present.

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
