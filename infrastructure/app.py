import os
import aws_cdk as cdk
from dotenv import load_dotenv

load_dotenv() 

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

ingress_stack = IngressStack(app, "TravelAgentIngressStack", env=env)

workflow_stack = WorkflowStack(app, "TravelAgentWorkflowStack",
    bus=ingress_stack.bus,
    env=env
)
DeliveryStack(app, "TravelAgentDeliveryStack", env=env)

observability_stack = ObservabilityStack(app, "TravelAgentObservabilityStack", 
    workflow_stack=workflow_stack,
    env=env
)

app.synth()
