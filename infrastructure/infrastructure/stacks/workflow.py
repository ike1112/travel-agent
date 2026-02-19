from aws_cdk import (
    Stack,
)
from constructs import Construct

class WorkflowStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # Empty for Phase 2
        print("Workflow Stack Initialized")
