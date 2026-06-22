# ethelflow/model_catalog.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    import yaml
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "Missing dependency: PyYAML. Add 'pyyaml' to requirements."
    ) from e


DEFAULT_CATALOG_PATH = "/etc/ethelflow/catalog.yaml"


def _read_yaml_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Catalog at {path} did not parse to a mapping/dict.")
    return data


def _env_key_for_provider_api_key(provider_name: str) -> str:
    # ethz_azure_openai -> ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY
    safe = re.sub(r"[^A-Za-z0-9]+", "_", provider_name).upper().strip("_")
    return f"ETHELFLOW_PROVIDER_{safe}_API_KEY"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    kind: str
    endpoint: str
    api_key_env: str


@dataclass(frozen=True)
class EmbeddingRoute:
    tenant: str
    provider: ProviderConfig
    deployment: str
    space: str
    dimension: int
    store_table: str


@dataclass(frozen=True)
class InferenceRoute:
    tenant: str
    class_name: str
    provider: ProviderConfig
    deployment: str
    interface: str


class ModelCatalog:
    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw

    @classmethod
    def load(cls, path: Optional[str] = None) -> "ModelCatalog":
        path = path or os.getenv("ETHELFLOW_MODEL_CATALOG_PATH", DEFAULT_CATALOG_PATH)
        return cls(_read_yaml_file(path))

    def provider(self, provider_name: str) -> ProviderConfig:
        providers = self.raw.get("providers") or {}
        p = providers.get(provider_name)
        if not isinstance(p, dict):
            raise KeyError(f"Provider {provider_name!r} not found in catalog.providers")

        kind = str(p.get("kind") or "")
        endpoint = str(p.get("endpoint") or "")
        if not kind:
            raise ValueError(f"Provider {provider_name!r} is missing 'kind'")
        if not endpoint:
            raise ValueError(f"Provider {provider_name!r} is missing 'endpoint'")

        # We do NOT store secrets in the catalog. We derive the env var name from provider.
        api_key_env = _env_key_for_provider_api_key(provider_name)
        return ProviderConfig(name=provider_name, kind=kind, endpoint=endpoint, api_key_env=api_key_env)

    def tenant_embedding_route(self, tenant: str, space: Optional[str] = None) -> EmbeddingRoute:
        tenants = self.raw.get("tenants") or {}
        t = tenants.get(tenant)
        if not isinstance(t, dict):
            raise KeyError(f"Tenant {tenant!r} not found in catalog.tenants")

        emb = t.get("embeddings") or {}
        provider_name = str(emb.get("provider") or "")
        deployment = str(emb.get("deployment") or "")
        default_space = str(emb.get("default_space") or "")

        if not provider_name:
            raise ValueError(f"Tenant {tenant!r} embeddings missing 'provider'")
        if not deployment:
            raise ValueError(f"Tenant {tenant!r} embeddings missing 'deployment'")
        if not default_space:
            raise ValueError(f"Tenant {tenant!r} embeddings missing 'default_space'")

        space = space or default_space

        spaces = ((self.raw.get("embeddings") or {}).get("spaces")) or {}
        s = spaces.get(space)
        if not isinstance(s, dict):
            raise KeyError(f"Embedding space {space!r} not found in catalog.embeddings.spaces")

        dim = int(s.get("dimension"))
        store = s.get("store") or {}
        store_table = str(store.get("table") or "")
        if not store_table:
            raise ValueError(f"Embedding space {space!r} missing embeddings.spaces.{space}.store.table")

        return EmbeddingRoute(
            tenant=tenant,
            provider=self.provider(provider_name),
            deployment=deployment,
            space=space,
            dimension=dim,
            store_table=store_table,
        )

    def tenant_inference_route(self, tenant: str, class_name: str) -> InferenceRoute:
        tenants = self.raw.get("tenants") or {}
        t = tenants.get(tenant)
        if not isinstance(t, dict):
            raise KeyError(f"Tenant {tenant!r} not found in catalog.tenants")

        classes = ((self.raw.get("inference") or {}).get("classes")) or {}
        c = classes.get(class_name)
        if not isinstance(c, dict):
            raise KeyError(f"Inference class {class_name!r} not found in catalog.inference.classes")
        interface = str(c.get("interface") or "")
        if not interface:
            raise ValueError(f"Inference class {class_name!r} missing inference.classes.{class_name}.interface")

        inf = (t.get("inference") or {}).get(class_name) or {}
        if not isinstance(inf, dict):
            raise KeyError(f"Tenant {tenant!r} missing inference mapping for class {class_name!r}")

        provider_name = str(inf.get("provider") or "")
        deployment = str(inf.get("deployment") or "")
        if not provider_name:
            raise ValueError(f"Tenant {tenant!r} inference.{class_name} missing 'provider'")
        if not deployment:
            raise ValueError(f"Tenant {tenant!r} inference.{class_name} missing 'deployment'")

        return InferenceRoute(
            tenant=tenant,
            class_name=class_name,
            provider=self.provider(provider_name),
            deployment=deployment,
            interface=interface,
        )

