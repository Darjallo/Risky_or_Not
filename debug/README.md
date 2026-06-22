# EthelFlow — Architecture & Troubleshooting Handoff (microk8s)

_Last updated: 2026-01-07_

This document is a compact “context pack” to bootstrap future debugging and design discussions about **EthelFlow** without re-pasting long chat logs.

---

## 1) High-level architecture

EthelFlow runs on **microk8s Kubernetes**. The system is split into multiple **agent services** (separate Deployments/Services), and a central **EthelFlow API** that orchestrates flows.

Typical services you may see in `kubectl get pods`:

- `ethelflow` (FastAPI on **:8080**) — `/flow`, `/assets`, etc.
- `embedding` (FastAPI on **:8000**) — `/embedding`
- `reasoning` (FastAPI on **:8000**) — `/reasoning` (or equivalent)
- `store-vectors` (FastAPI on **:8000**) — `/store_vectors`
- `store-text`, `store-chunks`, `file-to-text`, `chunk-text`, `executor`, etc.
- `postgres` and `minio`

Agents are called by the orchestrator flow code (LangGraph) via node adapters.

---

## 2) Repository layout (key paths)

> Repo root assumed as `.../ethelflow/`

- **Flows:** `ethelflow/ethelflow/flows/`
  - Examples:
    - `e2e_embedding.py`
    - `reasoning_multiprompt.py`
    - `reasoning_multiprompt_with_document.py`
    - `multi_math_check.py`
    - `quiz.py`
- **Agents:** `ethelflow/agents/`
  - Examples:
    - `ethelflow/agents/embedding/`
    - `ethelflow/agents/reasoning/`
    - `ethelflow/agents/store_vectors/`
- **Node adapters:** `ethelflow/agents/*/node_adapter.py`
  - Key:
    - `ethelflow/agents/embedding/node_adapter.py`
    - `ethelflow/agents/reasoning/node_adapter.py`
- **Model catalog loader:** `ethelflow/model_catalog.py`
- **Debug scripts:** `ethelflow/debug/`
  - Examples:
    - `upload_and_embed.py`
    - `mountains.py`
    - `multi_math.py`
    - `quiz.py`
- **K8s manifests:** `ethelflow/k8s/`
  - Examples:
    - `embedding.yaml`, `reasoning.yaml`, `store-vectors.yaml`
- **Alembic migrations:** `ethelflow/alembic/`
  - Versions: `ethelflow/alembic/versions/`
  - Initial revision: `be78615815ca_initial_schema.py`

---

## 3) Model catalog (single source of truth)

All agents that need routing **must mount** a model catalog file from ConfigMap:

- ConfigMap name: `ethelflow-model-catalog`
- Key: `catalog.yaml`
- Mount path (directory): `/etc/ethelflow`
- File path: `/etc/ethelflow/catalog.yaml`
- Env var required by services:
  - `ETHELFLOW_MODEL_CATALOG_PATH=/etc/ethelflow/catalog.yaml`

### Why it matters
If the catalog file is missing or not mounted, services typically fail with:

- `FileNotFoundError: /etc/ethelflow/catalog.yaml`
- CrashLoopBackOff at startup (for services that load catalog in lifespan)
- Or request-time failures (for services that load lazily)

---

## 4) Tenant routing and context propagation

EthelFlow uses **tenants** (e.g., `ethz`) and **inference classes** (e.g., `reasoning`) to pick deployments/providers via the catalog.

**Important current behavior:** flows often receive only `context` (dict). Therefore:

- Put `tenant` in **context**, not only at the top-level request.
- Put routing hints like `inference_class` in **context** (when needed by the node adapter).

### Common symptom
Reasoning node adapter error:

- `ValueError: Expected non-empty str for tenant, got None`

This usually means the flow’s state doesn’t contain the tenant key the adapter expects.

---

## 5) Provider API key wiring (Azure OpenAI)

Provider key is expected via environment variable name:

- `ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY`

The value should come from a K8s Secret (in the same namespace):

- Secret name: `ethz-azure-openai-secrets`
- Key: `api_key`

### Failure modes
- **CreateContainerConfigError** with events like:
  - `Error: secret "ethz-azure-openai-secrets" not found`
- Runtime error from embedding/reasoning service:
  - `Missing provider API key env var ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY ...`

---

## 6) K8s manifest checklist for each agent

For agents that use the catalog (embedding, reasoning, store-vectors, store-text, store-chunks, etc.):

### Must-have
- Env:
  - `ETHELFLOW_MODEL_CATALOG_PATH=/etc/ethelflow/catalog.yaml`
- Volume mount:
  - Mount ConfigMap `ethelflow-model-catalog` to `/etc/ethelflow`
  - Item mapping:
    - key: `catalog.yaml`
    - path: `catalog.yaml`
- Provider key env var (if the service calls providers directly):
  - `ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY` via `secretKeyRef`

### Example volume snippet
```yaml
volumeMounts:
  - name: model-catalog
    mountPath: /etc/ethelflow
    readOnly: true

volumes:
  - name: model-catalog
    configMap:
      name: ethelflow-model-catalog
      items:
        - key: catalog.yaml
          path: catalog.yaml
```

---

## 7) Debugging playbook

### A) Quick pod status
```bash
microk8s kubectl -n default get pods -o wide
```

### B) CrashLoopBackOff: inspect logs + describe
```bash
POD=$(microk8s kubectl -n default get pod -l app=embedding -o jsonpath='{.items[0].metadata.name}')
microk8s kubectl -n default describe pod "$POD" | sed -n '1,220p'
microk8s kubectl -n default logs "$POD" --tail=200
microk8s kubectl -n default logs "$POD" --previous --tail=200 || true
```

### C) Confirm catalog file exists inside a running pod
```bash
POD=$(microk8s kubectl -n default get pod -l app=store-vectors -o jsonpath='{.items[0].metadata.name}')
microk8s kubectl -n default exec -it "$POD" -- ls -la /etc/ethelflow
microk8s kubectl -n default exec -it "$POD" -- head -n 20 /etc/ethelflow/catalog.yaml
```

### D) Confirm secret exists
```bash
microk8s kubectl -n default get secret ethz-azure-openai-secrets -o yaml
```

### E) Restart rollouts (after applying manifests)
```bash
microk8s kubectl -n default apply -f k8s/embedding.yaml -f k8s/reasoning.yaml
microk8s kubectl -n default rollout restart deploy embedding reasoning
microk8s kubectl -n default rollout status deploy embedding
microk8s kubectl -n default rollout status deploy reasoning
```

---

## 8) /flow calling conventions (debug scripts)

### General rule
If a flow or node adapter needs routing:
- Provide `tenant` in `context`.
- Provide `inference_class` in `context` (if using class routing).
- Avoid hardcoding deployments in scripts unless explicitly testing an override.

Example payload shape:
```json
{
  "flow": "some_flow",
  "tenant": "ethz",
  "context": {
    "tenant": "ethz",
    "inference_class": "reasoning",
    "reasoning_effort": "low"
  },
  "stream": true
}
```

---

## 9) Postgres notes (embeddings tables)

Observed tables in `public` included:
- `embeddings_text_embedding_3_large`
- `embeddings_text_embedding_3_small`
- legacy `embedding_models` (found in initial Alembic revision)

In at least one run:
- vectors were inserted into `embeddings_text_embedding_3_large`
- the store-vectors response reported logical `space: ada3_large` and `store_table: ada3_large`

This indicates a **mapping layer** exists (logical “store handle” vs physical table name). If you change catalog store handles, verify store-vectors implementation and its mapping logic.

---

## 10) Alembic: removing `embedding_models`

- The table `embedding_models` appears in:
  - `alembic/versions/be78615815ca_initial_schema.py`

If you remove it:
1. Create a new Alembic revision with `op.drop_table("embedding_models")`.
2. Ensure `revision` is set to the real generated ID and `down_revision` points to the current head.
3. Keep downgrade consistent (recreate with correct column types / imports).

---

## 11) Known “gotchas” we hit (symptom → fix)

### A) `FileNotFoundError: /etc/ethelflow/catalog.yaml`
- Fix: mount ConfigMap + set `ETHELFLOW_MODEL_CATALOG_PATH`

### B) `Tenant 'debug' not found in catalog.tenants`
- Fix: call with tenant present in `catalog.tenants` (e.g., `ethz`) OR add tenant to catalog

### C) `Missing provider API key env var ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY`
- Fix: set env var via Secret, and ensure Secret exists in namespace

### D) `CreateContainerConfigError` + secret not found
- Fix: create the Secret in `default` namespace or reference correct name/namespace

### E) Streaming client: `incomplete chunked read`
- Usually server threw mid-stream; check `ethelflow` pod logs for traceback.

---

## 12) What to paste in a new chat

If starting fresh, paste:
1. Which component is failing (`embedding`, `reasoning`, `store-vectors`, `ethelflow`).
2. The last ~200 lines of logs for that pod.
3. The relevant manifest snippet (env + volumes) for that service.
4. The request payload your debug script sent (or script name + diff).

---

## Appendix: handy commands

### List deployments + images
```bash
microk8s kubectl -n default get deploy -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image
```

### Tail logs from orchestrator
```bash
POD=$(microk8s kubectl -n default get pod -l app=ethelflow -o jsonpath='{.items[0].metadata.name}')
microk8s kubectl -n default logs "$POD" --since=10m --tail=300
```
