# US-34: 2-Pod WebSocket Broadcast Delivery (<2s), Ordering & Cross-Tenant 403 Rejection Scenarios

**Task:** `SCRUM-530` (`TASK-303-3`: Write scenarios & pytest integration tests verifying 2-pod <2s delivery & cross-tenant 403 rejection)  
**Parent Story:** `SCRUM-503` (`STORY-303` — `US-34`: Real-Time Multi-Pod WebSocket Alerting & Tenant Isolation)  
**Assignee:** Matvii Yeromenko  
**Estimate:** 0.75 SP (`1h 30m` / `90m`)  
**Target Modules:** [`backend/app/ws/router.py`](../../app/ws/router.py), [`backend/app/ws/gateway.py`](../../app/ws/gateway.py), [`backend/app/outbox/relay.py`](../../app/outbox/relay.py), [`backend/app/auth/permissions.py`](../../app/auth/permissions.py)  
**Authoritative Pytest Suites:** [`backend/tests/test_websocket_2pod_delivery_and_isolation.py`](../test_websocket_2pod_delivery_and_isolation.py), [`backend/tests/test_websocket_auth_and_fanout.py`](../test_websocket_auth_and_fanout.py)

---

## 1. Objective & Scope

Validate that MANTA's stateless multi-pod WebSocket gateway (`WebSocketGatewayManager` backed by per-tenant Redis Streams `tenant_stream:{tenant_id}`):
1. Fans out relayed outbox alerts to **all connected pods (Pod 1 & Pod 2)** within **`<= 2.0s`**.
2. Preserves **strict monotonic ordering** (Redis Stream `seq` and RFC 9562 UUIDv7 `event_id` order) across every pod.
3. Enforces **strict tenant isolation**: Tenant B subscribers on Pod 1 and Pod 2 receive **zero** frames from Tenant A's stream.
4. Rejects **cross-tenant WebSocket subscriptions** cleanly before `websocket.accept()` (`WS 1008` / `HTTP 403 Forbidden`) across all supported token transports (`Authorization` header, `?token=` query parameter, and `Sec-WebSocket-Protocol: bearer, <jwt>`), leaving zero leaked subscribers.

---

## 2. Multi-Pod Topology

```
Tenant A OutboxEvent ──► OutboxRelayWorker ──► XADD tenant_stream:{tenant_a}
                                                     │
                        ┌────────────────────────────┴────────────────────────────┐
                        ▼ (XREAD)                                                 ▼ (XREAD)
              Pod 1 WebSocketGateway                                    Pod 2 WebSocketGateway
                        │                                                         │
        ┌───────────────┴───────────────┐                         ┌───────────────┴───────────────┐
        ▼                               ▼                         ▼                               ▼
  Tenant A WS (Pod 1)             Tenant B WS (Pod 1)       Tenant A WS (Pod 2)             Tenant B WS (Pod 2)
  [Receives <= 2s, ordered]       [Receives 0 frames]       [Receives <= 2s, ordered]       [Receives 0 frames]
```

---

## 3. QA Verification Scenarios

### Scenario 1: 2-Pod Broadcast Delivery Within `<= 2.0s` SLO
- **Given:** Pod 1 and Pod 2 run independent `WebSocketGatewayManager` instances connected to a shared Valkey/Redis Streams broker. Tenant A has active WebSocket subscribers on both Pod 1 and Pod 2.
- **When:** `OutboxRelayWorker` publishes a batch of Tenant A domain alerts (`deployment.failed`, `incident.spike`, `health.degraded`) to `tenant_stream:{tenant_a}`.
- **Expected Result:**
  - Both Pod 1 and Pod 2 deliver all Tenant A frames within `elapsed <= 2.0s`.
  - Every delivered frame includes `"type": "event"`, `"schema_version": "1.0"`, `"tenant_id": "<tenant_a>"`, `"seq"`, `"event_id"`, `"event_type"`, `"occurred_at"`, and `"data"`.

### Scenario 2: Strict Monotonic Ordering Across Both Pods
- **Given:** 10 sequential Tenant A `OutboxEvent` rows are staged with monotonic UUIDv7 `id`s (`ordinal = 0..9`) and relayed to `tenant_stream:{tenant_a}`.
- **When:** Pod 1 and Pod 2 fan out the stream entries to their local Tenant A WebSocket subscribers.
- **Expected Result:**
  - Both Pod 1 and Pod 2 deliver `[0, 1, ..., 9]` in exact creation order.
  - Redis Stream `seq` IDs (`<ms>-<seq>`) and `event_id`s are strictly monotonically increasing on both pods.

### Scenario 3: Zero Cross-Tenant Frame Leakage Across Pods
- **Given:** Tenant A and Tenant B each have active WebSocket subscribers on Pod 1 and Pod 2.
- **When:** Alerts are published exclusively to `tenant_stream:{tenant_a}`.
- **Expected Result:**
  - Tenant A subscribers on Pod 1 and Pod 2 receive all Tenant A frames within `<= 2.0s`.
  - Tenant B subscribers on Pod 1 and Pod 2 receive **0 frames** (`[]`).
  - When interleaved alerts are published for both Tenant A and Tenant B, each tenant receives only its own events in monotonic order.

### Scenario 4: Cross-Tenant WebSocket Subscription Rejection (`HTTP 403` / `WS 1008`)
- **Given:** An authenticated user belonging to Tenant B (`claims.tenant_uuid == tenant_b`) with valid `DEVELOPER` / `VIEW_PROJECT_DORA` permissions.
- **When:** The user attempts to open a WebSocket connection to `/api/v1/ws/tenants/{tenant_a}/stream` on Pod 1 or Pod 2 using:
  1. `Authorization: Bearer <tenant_b_jwt>`
  2. `?token=<tenant_b_jwt>`
  3. `Sec-WebSocket-Protocol: bearer, <tenant_b_jwt>`
- **Expected Result:**
  - The handshake is rejected **strictly before `websocket.accept()`** with `WebSocketDisconnect(code=1008)` (`Forbidden` / policy violation).
  - Raw ASGI handshake emits `websocket.close` (`code=1008`) and never emits `websocket.accept`.
  - `gateway._subscribers` for `tenant_a` remains empty on both pods.

### Scenario 5: Clean Rejection on Missing / Insufficient Permissions & Forged Tenant Claims
- **Given:** A user with a valid JWT for `tenant_a` but insufficient role (e.g., `GUEST` / empty roles) or a malformed/untrusted JWT.
- **When:** Attempting to subscribe to `/api/v1/ws/tenants/{tenant_a}/stream`.
- **Expected Result:**
  - Rejected pre-accept with `WS 1008`, zero frames delivered, and zero subscriber state leaked.

---

## 4. Acceptance Criteria Traceability

| Acceptance Criterion | Scenario(s) | Automated Test(s) in `test_websocket_2pod_delivery_and_isolation.py` |
| --- | --- | --- |
| Two simulated pods deliver Tenant A alerts within `<= 2s` | Scenario 1, 2 | `test_two_pods_deliver_ordered_tenant_a_alerts_within_2s_and_isolate_tenant_b`, `test_two_pod_asgi_apps_deliver_live_alerts_within_2s` |
| Tenant B receives zero frames | Scenario 3 | `test_two_pods_deliver_ordered_tenant_a_alerts_within_2s_and_isolate_tenant_b`, `test_two_pods_interleaved_bidirectional_tenant_isolation` |
| Cross-tenant subscription fails cleanly (`403` / `1008`) | Scenario 4, 5 | `test_cross_tenant_ws_subscription_rejected_pre_accept_across_pods_and_transports` |
