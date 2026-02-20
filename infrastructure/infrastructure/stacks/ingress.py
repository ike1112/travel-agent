from aws_cdk import (
    Stack,
    CfnOutput,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_apigateway as apigateway,
    aws_events as events,
    aws_events_targets as targets,
    aws_sqs as sqs,
    Duration,
    RemovalPolicy,
)
from constructs import Construct

class IngressStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # === 1. EventBridge Custom Bus ===
        self.bus = events.EventBus(
            self, "TravelSystemBus",
            event_bus_name="travel-system"
        )
        
        # === 2. Intake Lambda (Gateway -> EventBridge) ===
        # Simple execution role
        intake_role = iam.Role(
            self, "IntakeLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        intake_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )
        # Grant PutEvents on our custom bus
        self.bus.grant_put_events_to(intake_role)

        self.intake_lambda = lambda_.Function(
            self, "IntakeLambda",
            function_name="travel-agent-intake",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="intake.handler.lambda_handler", 
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(10), 
            memory_size=128,
            role=intake_role,
            environment={
                "EVENT_BUS_NAME": self.bus.event_bus_name,
                "LOG_LEVEL": "INFO",
            },
        )

        # === 3. API Gateway (REST API) ===
        self.api = apigateway.LambdaRestApi(
            self, "TravelApi",
            rest_api_name="travel-agent-api",
            handler=self.intake_lambda,
            proxy=False,
            deploy_options=apigateway.StageOptions(
                stage_name="prod",
                tracing_enabled=True 
            )
        )
        
        # POST /travel resource
        travel_resource = self.api.root.add_resource("travel")
        travel_resource.add_method("POST")

        # Output the API URL with a clean name
        CfnOutput(self, "ApiUrl",
            value=f"{self.api.url}travel",
            description="Travel Agent API endpoint",
            export_name="travel-agent-api-url"
        )
