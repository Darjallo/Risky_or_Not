from pydantic import BaseModel, Field, field_validator
from importlib.util import find_spec
from typing import Any
from uuid import UUID


class FlowRequest(BaseModel):
    """
    Represents a request to execute a flow.
    """

    flow: str = Field(
        ...,
        description="The name of the flow to execute.",
        examples=["example_flow"],
    )
    tenant: str = Field(
        ...,
        description="The tenant identifier for the flow execution.",
        examples=["tenant_id"],
    )
    context: dict = Field(
        default_factory=dict,
        description="Optional context data passed into the flow.",
        examples=[{"user_message": "Hello"}],
    )
    stream: bool = Field(
        False,
        description="If true, stream flow updates instead of returning a single response.",
        examples=[False],
    )

    @field_validator("flow")
    @classmethod
    def validate_flow_name(cls, v):
        """
        Validate that the flow name corresponds to an existing flow module.
        """
        if not v or not find_spec(f"ethelflow.flows.{v}"):
            raise ValueError(f"No such flow '{v}'")
        return v


class FlowContinueRequest(BaseModel):
    """
    Represents a request to continue a flow that has been interrupted.
    """

    data: dict[str, Any] = Field(
        ...,
        description="Mapping from interrupt identifiers to continuation values.",
        examples=[{"interrupt_id": {"user_input": "Continue"}}],
    )
    stream: bool = Field(
        False,
        description="If true, stream flow updates instead of returning a single response.",
        examples=[False],
    )

    # TODO: validate that the interrupt IDs are valid UUIDs
