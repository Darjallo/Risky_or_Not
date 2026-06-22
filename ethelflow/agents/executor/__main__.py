import ast
import base64
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from kubernetes_asyncio import client, config, watch
from kubernetes_asyncio.client.api_client import ApiClient

from ethelflow.agents.executor.models import ExecutionRequest, ExecutionResult

NAMESPACE = "default"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await config.load_config()
    except config.ConfigException:
        raise RuntimeError(
            "Kubernetes config not found. Ensure running in cluster or set kubeconfig path."
        )
    yield


app = FastAPI(lifespan=lifespan)


def execution_plan(exec_type: str) -> tuple[str, list[str]]:
    """Return (script filename, command argv) for each supported executor type."""
    if exec_type == "python":
        return "script.py", ["python3", "/scripts/script.py"]
    if exec_type == "r":
        return "script.R", ["Rscript", "/scripts/script.R"]
    if exec_type == "maxima":
        cmd = 'batchload("/scripts/script.mac")$'
        return "script.mac", [
            "maxima",
            "--very-quiet",
            f"--batch-string={cmd}",
        ]
    raise ValueError(f"Unknown execution type: {exec_type}")


@app.post("/execute")
async def execute_code(req: ExecutionRequest):
    # Resolve plan (script name + command) for this execution type
    try:
        script_name, command = execution_plan(req.type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Decode script for ALL types
    try:
        code = base64.b64decode(req.code_b64).decode("utf-8")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 encoding")

    # Minimal validation: keep python syntax check
    if req.type == "python":
        try:
            ast.parse(code)
        except SyntaxError as e:
            raise HTTPException(status_code=400, detail=f"Syntax error: {e.msg}")

    execution_id = uuid.uuid4()
    execution_name = f"execution-{execution_id.hex[:8]}"
    print(f"Execution ID: {execution_id}")

    # Job uses a ConfigMap volume called execution_name with a single file script_name
    job_manifest = client.V1Job(
        metadata=client.V1ObjectMeta(name=execution_name),
        spec=client.V1JobSpec(
            backoff_limit=0,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"ethel.ethz.ch/execution-id": str(execution_id)}
                ),
                spec=client.V1PodSpec(
                    restart_policy="Never",
                    containers=[
                        client.V1Container(
                            name="executor",
                            image=req.image,
                            image_pull_policy="IfNotPresent",
                            command=command,
                            volume_mounts=[
                                client.V1VolumeMount(
                                    name="script-volume",
                                    mount_path="/scripts",
                                    read_only=True,
                                )
                            ],
                        )
                    ],
                    volumes=[
                        client.V1Volume(
                            name="script-volume",
                            config_map=client.V1ConfigMapVolumeSource(name=execution_name),
                        )
                    ],
                ),
            ),
        ),
    )

    pod_name = None
    exit_code = -1
    logs = ""

    async with ApiClient() as api_client:
        batch_v1 = client.BatchV1Api(api_client)
        core_v1 = client.CoreV1Api(api_client)

        # Always attempt cleanup, even on failures
        try:
            # Create ConfigMap first (avoids a race where pod starts before CM exists)
            config_map_manifest = client.V1ConfigMap(
                metadata=client.V1ObjectMeta(name=execution_name),
                data={script_name: code},
            )
            await core_v1.create_namespaced_config_map(
                namespace=NAMESPACE, body=config_map_manifest
            )

            # Create Job
            await batch_v1.create_namespaced_job(namespace=NAMESPACE, body=job_manifest)

            # Wait for pod completion
            w = watch.Watch()
            async for event in w.stream(
                core_v1.list_namespaced_pod,
                namespace=NAMESPACE,
                label_selector=f"ethel.ethz.ch/execution-id={execution_id}",
                timeout_seconds=60,
            ):
                pod = event["object"]
                if pod.status.phase in ("Succeeded", "Failed"):
                    pod_name = pod.metadata.name
                    w.stop()
                    break

            if not pod_name:
                raise HTTPException(status_code=500, detail="Pod not found / timed out")

            # Read exit code + logs
            pod = await core_v1.read_namespaced_pod(name=pod_name, namespace=NAMESPACE)
            exit_code = pod.status.container_statuses[0].state.terminated.exit_code
            logs = await core_v1.read_namespaced_pod_log(
                name=pod_name, namespace=NAMESPACE
            )

        except HTTPException:
            # Re-raise FastAPI exceptions as-is
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Execution error: {str(e)}")
        finally:
            # Best-effort cleanup (delete job and configmap)
            try:
                await batch_v1.delete_namespaced_job(
                    name=execution_name,
                    namespace=NAMESPACE,
                    propagation_policy="Background",
                )
            except Exception:
                pass
            try:
                await core_v1.delete_namespaced_config_map(
                    name=execution_name, namespace=NAMESPACE
                )
            except Exception:
                pass

    return ExecutionResult(
        execution_id=execution_id,
        return_code=exit_code,
        stdout=logs,
        stderr=logs,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

