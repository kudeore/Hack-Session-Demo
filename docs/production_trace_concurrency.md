## runtime controls around the flow:

```text
User request
  -> RequestContext created
  -> trace_id / request_id / idempotency_key assigned
  -> graph runs with isolated state
  -> every skill emits trace-aware audit event
  -> optional checkpoint after each major step
  -> manual-review handoff uses idempotency evidence
```

## Why this matters for concurrency

One container can process multiple requests. The container is not the isolation boundary. Each request is isolated through:

- `trace_id`
- `request_id`
- per-request workflow `state`
- audit `sequence_number`
- `idempotency_key`
- optional workflow checkpoints

Even if audit events are physically interleaved, they can be reconstructed by `trace_id` and `sequence_number`.

## Key audit event fields

Each audit event includes:

```json
{
  "event_id": "EVT-...",
  "trace_id": "TRC-...",
  "request_id": "REQ-...",
  "case_id": "RF-BKG1002-C002",
  "sequence_number": 7,
  "workflow_name": "governed_refund_agent",
  "workflow_version": "runtime-guardrails-2026.06",
  "tenant_id": "reference_airline",
  "channel": "cli",
  "container_id": "local_or_pod_id",
  "step": "policy_as_code_evaluator",
  "event_type": "skill_invocation"
}
```

## Commands

Normal governed run:

```bash
python src/app.py --naive --audit
```

Write audit to local JSONL file:

```bash
python src/app.py --audit --save-audit outputs/audit_trace.jsonl
```

Write JSON checkpoint files:

```bash
python src/app.py --audit --state-backend json_file --checkpoint-dir outputs/checkpoints
```

Concurrent request run:

```bash
python src/concurrent_requests_demo.py
```

## Production mapping

| Demo component | Production equivalent |
|---|---|
| `RequestContextFactory` | API gateway / workload platform trace context |
| `InMemoryAuditEmitter` | Kafka, EventHub, Pub/Sub, SIEM, GRC evidence store |
| `JsonlAuditEmitter` | Local JSONL event emission |
| `InMemoryStateStore` | Redis / Postgres / DynamoDB / workflow engine checkpoints |
| `JsonFileStateStore` | Local checkpoint persistence |
| `idempotency.py` | DB table with unique idempotency key |
| Dockerfile | Container image for Kubernetes / ECS / Cloud Run / Azure Container Apps |

## Important production principle

The agent runtime container should be stateless.

It can execute the graph, but it should not own:

- durable workflow state
- audit evidence
- enterprise credentials
- policy source of truth
- RAG index source of truth
- case management records

Those should be external services.

