# Intent Service

## Overview
The **intent** service is a small, low-latency LLM wrapper that classifies the user’s latest request into **one of a caller-supplied set of intent labels** (e.g., `simulation`, `exercise`, `visualization`, …). It is designed to be used early in a flow as a **decision point**: after intent classification, the flow can branch to specialized tooling or continue with a normal chat/RAG path.

Key design choices:
- **Caller defines the intent taxonomy** (`intent_options`) per request (or per session).
- **Caller owns the thresholding and branching logic** (the service returns a confidence score; it does not apply `confidence_threshold` itself).
- **Low-latency routing** is done via the EthelFlow model catalog using an inference class (typically `low_latency`).
- **Strict output shape**: service asks the model to return **only JSON** and then coerces/validates output server-side.

---

## Endpoint(s)

### `GET /healthz`
Health probe endpoint.

**Response**
```json
{"ok": true}
```

### `POST /intent`
Classify the user’s intent.

**Cluster URL**
- Inside cluster: `http://intent.default.svc:8000/intent`

---

## Request / Response

### Request fields
The service uses `IntentRequest` (Pydantic). The important fields are:

- `tenant` (string, required)  
  Tenant name used for routing via the model catalog.

- **One of:**
  - `messages` (list[dict], preferred)  
    Canonical chat history. Usually you should include at least the latest user message; a short recent history is optional.
  - `prompt` (string)  
    Backward-compatible: single-turn user text.

  **Important:** `prompt` and `messages` are **mutually exclusive**.

- `intent_options` (object, required)  
  Defines the allowed intents and a default fallback intent.

- `stream` (bool, optional)  
  Present for compatibility; **intent classification is currently non-streaming**. Use `false`.

- `deployment` (string, optional)  
  Escape hatch to override catalog routing. Normally omit.

### Recommended `intent_options` shape
```json
{
  "version": 1,
  "default_intent": "chat",
  "options": {
    "simulation": {
      "description": "User wants the system to generate or run a simulation (interactive or computed).",
      "examples": ["simulate", "model this", "run a simulation", "numerically solve"]
    },
    "exercise": {
      "description": "User wants an interactive exercise/problem (practice, hints, answer checking).",
      "examples": ["give me an exercise", "quiz me", "practice problems", "check my answer"]
    },
    "visualization": {
      "description": "User wants a visualization (plot/diagram/image).",
      "examples": ["plot", "visualize", "draw", "show me a graph", "make an image"]
    }
  },
  "confidence_threshold": 0.7
}
```

Notes:
- `confidence_threshold` is **not enforced by the service**; it is included for the **caller/flow** to apply.
- Keep options small and descriptions short for best low-latency behavior.
- The model does better when each option includes 2–6 representative `examples`.

### Example request (messages-based)
```json
{
  "tenant": "ethz",
  "messages": [
    {"role": "user", "content": "Give me a simulation of that"}
  ],
  "intent_options": {
    "version": 1,
    "default_intent": "chat",
    "options": {
      "simulation": {"description": "Run or generate a simulation."},
      "exercise": {"description": "Create an interactive exercise/problem."},
      "visualization": {"description": "Create a plot/diagram/image."}
    },
    "confidence_threshold": 0.7
  },
  "stream": false
}
```

### Example request (prompt-based)
```json
{
  "tenant": "ethz",
  "prompt": "Give me a simulation of that",
  "intent_options": {
    "version": 1,
    "default_intent": "chat",
    "options": {
      "simulation": {"description": "Run or generate a simulation."},
      "exercise": {"description": "Create an interactive exercise/problem."},
      "visualization": {"description": "Create a plot/diagram/image."}
    },
    "confidence_threshold": 0.7
  },
  "stream": false
}
```

### Response fields
The service returns `IntentResponse`:

- `result` (object)
  - `intent` (string): one of the allowed options (or the default intent)
  - `confidence` (number 0..1)
  - `topic` (string | null): short phrase if obvious
  - `language` (string | null): language tag/name if obvious
  - `reason` (string): parsing/coercion status:
    - `ok` (parsed JSON)
    - `ok_word` (single-word fallback)
    - `parse_failed` (could not parse; default returned)
    - `empty_raw` (model returned empty output; default returned)

- `tenant` (string)
- `provider` (string): provider name from catalog route
- `deployment` (string): Azure deployment used (from catalog or override)
- `raw` (string): raw model output (useful for debugging)

### Example response
```json
{
  "result": {
    "intent": "simulation",
    "topic": "concept-inventory performance across languages",
    "confidence": 0.83,
    "language": "en",
    "reason": "ok"
  },
  "tenant": "ethz",
  "provider": "ethz_azure_openai",
  "deployment": "Ethel_5_nano",
  "raw": "{\"intent\":\"simulation\",\"confidence\":0.83,\"topic\":\"concept-inventory performance across languages\",\"language\":\"en\"}"
}
```

---

## Node adapter contract (flow integration)

### Adapter
`ethelflow.agents.intent.node_adapter.intent_node(...)`

### Inputs (state keys)
- `tenant_key` (default `"tenant"`) → **string**
- `prompt_key` (default `"prompt"`) → **string or None**
- `messages_key` (default `"messages"`) → **list[dict] or None**
- `intent_options_key` (default `"intent_options"`) → **dict (required)**

**Important:** The adapter should pass **either** `prompt` **or** `messages`. Do not supply both.

### Output
- `output_key` (default `"intent_response"`) → **dict**
  - This is `IntentResponse.model_dump(...)` (i.e., the JSON-equivalent dict).

### Typical flow usage
1. Normalize context into canonical `messages`.
2. Call `intent_node(...)` to populate `intent_response`.
3. Unpack `intent_response["result"]` into flat state fields.
4. Apply threshold and branch in the flow (e.g., `if intent in allowed and confidence >= threshold`).

---

## Routing / Model catalog expectations (tenant/space)

The service routes model calls through the EthelFlow `ModelCatalog`:

- It looks up:
  - `tenant` from the request
  - inference class from env var `ETHELFLOW_INFERENCE_CLASS` (typically `low_latency`)
- It resolves:
  - `provider` (e.g., `ethz_azure_openai`)
  - `deployment` (Azure deployment name, e.g., `Ethel_5_nano`)

### Required environment variables
- `ETHELFLOW_MODEL_CATALOG_PATH=/etc/ethelflow/catalog.yaml`
- `ETHELFLOW_INFERENCE_CLASS=low_latency` (or your chosen class name)
- Provider API key env var that matches your provider:
  - e.g., `ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY` (from K8s secret)

### Optional tuning env vars
- `ETHELFLOW_AZURE_OPENAI_API_VERSION` (default: `2025-04-01-preview`)
- `ETHELFLOW_INTENT_MAX_COMPLETION_TOKENS` (default: 1024)
- `ETHELFLOW_INTENT_REASONING_EFFORT` (`low|medium|high`, default: `low`)

**Important:** Do **not** pass `temperature` for reasoning-style deployments (many reject it).

---

## Storage / DB interaction
None.  
The intent service is stateless and does not read/write Postgres or S3.

---

## k8s deployment/service name
- Deployment: `intent`
- Service: `intent`
- Port: `8000`
- Readiness/liveness should probe: `GET /healthz`

Typical in-cluster URL used by node adapter:
- `http://intent.default.svc:8000/intent`

---

## Troubleshooting

### 1) Pod CrashLoop: `No module named ... __main__`
- Ensure `ethelflow/agents/intent/__main__.py` exists in the image.
- Ensure the deployment command is:
  - `command: ["python", "-m", "ethelflow.agents.intent"]`

### 2) 500 with `Unsupported value: 'temperature'`
- Remove any `temperature` parameter from the OpenAI call.
- Reasoning deployments commonly only support default temperature.

### 3) `result.reason = empty_raw` and `raw=""`
Common causes:
- Too-small `max_completion_tokens` for the model’s visible output (increase `ETHELFLOW_INTENT_MAX_COMPLETION_TOKENS`)
- Model spends tokens on hidden reasoning; keep prompts short and set `ETHELFLOW_INTENT_REASONING_EFFORT=low` if supported
- Very long `messages` history; pass only the last user message + a short recent context

### 4) `parse_failed` even though the user obviously asked for e.g. “simulation”
- Add clearer `description` and 2–6 `examples` per option.
- Ensure option keys are simple strings (no spaces).
- Ensure your flow is not sending both `prompt` and `messages`.

### 5) Catalog changes don’t take effect
- Apply the ConfigMap and restart the deployment(s):
  - `kubectl apply -f k8s/model_catalog.yaml`
  - `kubectl rollout restart deploy/intent`

### 6) Wrong deployment/provider returned
- Check the tenant route in `catalog.yaml` under:
  - `tenants.<tenant>.inference.low_latency`
- Confirm `ETHELFLOW_INFERENCE_CLASS=low_latency` in the intent deployment.
