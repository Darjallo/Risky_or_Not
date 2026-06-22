# executor

## Overview
`executor` is a **sandboxed code-execution service**. It accepts a container image, an execution type (`python`, `r`, or `maxima`), and base64-encoded code. It then:

1. Creates a short-lived **Kubernetes ConfigMap** containing the script.
2. Launches a short-lived **Kubernetes Job** that mounts the script into `/scripts`.
3. Waits for the Job pod to complete (Succeeded/Failed).
4. Returns the exit code and captured pod logs.

This is intended for controlled, internal workflows (e.g., running snippets in a known image) and **must not be exposed to untrusted users**.

---

## Endpoint(s)
- `POST /execute`

Service URL used by the node adapter:
- `http://executor.default.svc:8000/execute`

---

## Request/Response

### Request model: `ExecutionRequest`
Fields:
- `image` (str): Container image to run (e.g., `python:3.11-slim`, a custom image with R/maxima, etc.)
- `type` (literal: `"python" | "r" | "maxima"`): Execution mode.
- `code_b64` (str): Base64-encoded script text.
- `stream` (bool | None, default `false`): Present but currently **not used** by the service implementation.

Validation behavior:
- `code_b64` is required for all execution types.
- For `type="python"`, the service performs a basic `ast.parse()` syntax check before launching a Job.

### Response model: `ExecutionResult`
Fields:
- `execution_id` (UUID): ID assigned by the service for this run.
- `return_code` (int): Container exit code.
- `stdout` (str): Pod logs (currently the same as `stderr`).
- `stderr` (str): Pod logs (currently the same as `stdout`).

> Note: The service currently returns a single combined log stream and assigns it to both `stdout` and `stderr`.

### Example request
```json
{
  "image": "python:3.11-slim",
  "type": "python",
  "code_b64": "cHJpbnQoIkhlbGxvIGZyb20gZXhlY3V0b3IhIikK",
  "stream": false
}
```

### Example response
```json
{
  "execution_id": "4a2c31f0-1a7b-4c71-84ad-3c0a7d0b4d5b",
  "return_code": 0,
  "stdout": "Hello from executor!\n",
  "stderr": "Hello from executor!\n"
}
```

---

## Node adapter contract

### Node: `executor_node(...)` (in `node_adapter.py`)
Default input keys:
- `image` → passed to request `image`
- `type` → passed to request `type`
- `code` → base64-encoded and sent as request `code_b64`

Default output keys:
- `execution_result` → the parsed `ExecutionResult`

Important notes for flow authors:
- The adapter currently yields the `ExecutionResult` **object** (a Pydantic model), not a JSON dict.  
  If downstream nodes need JSON-serializable state, consider converting via `data.model_dump(mode="json")` (without changing the service).

---

## Routing/Model catalog expectations
- **Not catalog-routed.** This service does not use tenant/space/inference_class.
- It does require access to Kubernetes API credentials (in-cluster ServiceAccount or kubeconfig) to create Jobs/ConfigMaps in the target namespace.

---

## Storage/DB interaction
- **No Postgres access.**
- Temporary K8s resources only:
  - `ConfigMap` named `execution-<idprefix>` containing the script file
  - `Job` named `execution-<idprefix>` running the container

Cleanup:
- The service attempts best-effort cleanup of both Job and ConfigMap in a `finally` block.

---

## k8s deployment/service name
- Kubernetes Service (expected): `executor` in namespace `default`
  - DNS: `executor.default.svc`
  - Port: `8000`

Runtime behavior:
- Creates **Jobs** and **ConfigMaps** in `NAMESPACE="default"` (currently hardcoded).

---

## Troubleshooting

### 1) `Kubernetes config not found`
Symptom:
- 500 with message like: “Kubernetes config not found…”

Cause:
- The service cannot load in-cluster config or kubeconfig.

Fix:
- Run in-cluster with a ServiceAccount, or ensure kubeconfig is available and `kubernetes_asyncio.config.load_config()` can find it.

### 2) `Pod not found / timed out`
Symptom:
- 500 “Pod not found / timed out”

Causes:
- Job cannot schedule (image pull errors, insufficient resources, RBAC).
- The watch times out (currently 60s).

Fix:
- Inspect Job/Pod events:
  - `kubectl -n default get jobs,pods | grep execution-`
  - `kubectl -n default describe pod <podname>`
- Consider increasing watch timeout if long-running executions are expected.

### 3) RBAC / permission errors
Symptom:
- 500 with “Forbidden” or similar errors when creating Jobs/ConfigMaps or reading logs.

Fix:
- Ensure the executor's ServiceAccount has permissions for:
  - `create/get/list/watch/delete` on `configmaps`
  - `create/get/list/watch/delete` on `jobs` (batch)
  - `get/list/watch` on `pods`
  - `get` on `pods/log`

### 4) `Syntax error` for Python
Symptom:
- 400 “Syntax error: …”

Cause:
- The service parses Python code before launching the Job.

Fix:
- Correct the Python syntax; for other types (R/maxima), no syntax pre-check is performed.

---

## Security considerations (read before production use)
- Treat `executor` as **high risk**: it runs arbitrary code in arbitrary images.
- Keep it internal, gated by strong authentication/authorization, and prefer allowlisted images.
- Apply resource limits/quotas and consider network policies to restrict egress from execution jobs.
