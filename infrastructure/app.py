#!/usr/bin/env python3
import os
import aws_cdk as cdk

from infrastructure.stacks.ingress import IngressStack
from infrastructure.stacks.workflow import WorkflowStack
from infrastructure.stacks.delivery import DeliveryStack
from infrastructure.stacks.observability import ObservabilityStack

app = cdk.App()

# Shared environment
env = cdk.Environment(
    account=os.getenv('CDK_DEFAULT_ACCOUNT'),
    region=os.getenv('CDK_DEFAULT_REGION')
)

IngressStack(app, "TravelAgentIngressStack", env=env)
WorkflowStack(app, "TravelAgentWorkflowStack", env=env)
DeliveryStack(app, "TravelAgentDeliveryStack", env=env)
ObservabilityStack(app, "TravelAgentObservabilityStack", env=env)

app.synth()
