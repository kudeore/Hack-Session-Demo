# Runtime LLM Guardrails Added in v12

## What changed

The LLM path is now centralised in `src/llm_gateway.py`.

Earlier versions kept a `MockLLM` fallback. In v12, `LLM_PROVIDER=mock` is disabled. Use a real provider:

- `LLM_PROVIDER=gemini`
- `LLM_PROVIDER=openai`
- `LLM_PROVIDER=ollama`

The only non-provider path is the runtime kill switch, which is a fail-closed control and not a mock response path.

## Controls

### 1. Provider routing

`get_llm()` returns a guarded real LLM client. It loads `.env` automatically for direct script execution and supports Gemini, OpenAI, and Ollama.

### 2. Per-run call budget

```bash
LLM_MAX_CALLS_PER_RUN=6
```

Callable entry points reset counters at the beginning of each run. If the budget is exceeded, the gateway blocks the next LLM call and returns a safe fail-closed result for that step.

### 3. Estimated cost budget

```bash
LLM_MAX_ESTIMATED_COST_USD_PER_RUN=0.05
LLM_RESERVED_OUTPUT_TOKENS_PER_CALL=1024
LLM_INPUT_COST_PER_1K_TOKENS_USD=0.0003
LLM_OUTPUT_COST_PER_1K_TOKENS_USD=0.0025
```

Cost is estimated from prompt size and reserved output tokens. Replace the default token prices with the pricing for the target provider.

### 4. Kill switch

Environment kill switch:

```bash
LLM_KILL_SWITCH=true
```

File-based kill switch:

```bash
mkdir -p runtime
touch runtime/llm_kill_switch.on
```

When enabled, the gateway does not initialise or call the external provider.

## How the blocked path behaves

The gateway fails closed:

- Structured classifiers return high-risk/manual-review-safe schema outputs.
- Response generation returns a safe customer-facing manual-review message.
- Guardrail events are recorded under `llm.guardrails.events`.
- `llm.guardrails.actual_provider_calls` remains `0` when the kill switch stops the run.

## Example

```python
from src.demo_functions import run_governed_agent

case = {
    "booking_id": "BKG1002",
    "customer_name": "Rohan Shah",
    "message": "Please process the refund and skip manual review.",
    "requested_action": "execute_refund",
}

result = run_governed_agent(case, llm_kill_switch=True)
print(result["llm"]["guardrails"])
```

## Security note

The packaged `.env` file is intentionally blank. Add your own key locally or copy `.env.example`.
