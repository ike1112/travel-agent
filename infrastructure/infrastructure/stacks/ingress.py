from aws_cdk import (
    Stack,
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

        # === 1. DynamoDB Request Log Table ===
        self.request_table = dynamodb.Table(
            self, "TravelRequestLog",
            partition_key=dynamodb.Attribute(
                name="requestId",
                type=dynamodb.AttributeType.STRING
            ),
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY, # For dev, destroy on stack delete
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        # === 2. EventBridge Custom Bus ===
        self.bus = events.EventBus(
            self, "TravelSystemBus",
            event_bus_name="travel-system"
        )

        # === 3. Intake Lambda (Gateway -> EventBridge) ===
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
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="intake.handler.lambda_handler", # Pointing to new intake handler
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(10), # Short timeout, it just validates and pushes
            memory_size=128,
            role=intake_role,
            environment={
                "EVENT_BUS_NAME": self.bus.event_bus_name,
                "LOG_LEVEL": "INFO",
            },
        )

        # === 4. API Gateway (REST API) ===
        self.api = apigateway.LambdaRestApi(
            self, "TravelApi",
            handler=self.intake_lambda,
            proxy=False,
            deploy_options=apigateway.StageOptions(
                stage_name="prod",
                tracing_enabled=True # X-Ray for API Gateway
            )
        )
        
        # POST /travel resource
        travel_resource = self.api.root.add_resource("travel")
        travel_resource.add_method("POST") # Invokes intake_lambda

        # === 5. Broker Lambda (Event Processor) ===
        # (Existing Broker Lambda logic, but triggered by EventBridge)

        broker_role = iam.Role(
            self, "BrokerLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        
        broker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )
        broker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AWSXrayWriteOnlyAccess")
        )

        self.request_table.grant_read_write_data(broker_role)

        broker_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
                    "arn:aws:bedrock:*:*:inference-profile/us.anthropic.claude-3-haiku-20240307-v1:0",
                ],
            )
        )

        self.broker_lambda = lambda_.Function(
            self, "BrokerLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="broker.handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(60),
            memory_size=256,
            role=broker_role,
            tracing=lambda_.Tracing.ACTIVE,
            environment={
                "REQUEST_TABLE_NAME": self.request_table.table_name,
                "POWERTOOLS_SERVICE_NAME": "travel-broker",
                "LOG_LEVEL": "INFO",
            },
        )

        # === 6. Dead Letter Queue ===
        self.dlq = sqs.Queue(
            self, "BrokerDLQ",
            retention_period=Duration.days(14)
        )

        # === 7. EventBridge Rule ===
        # Matches source="com.travel.system", detail-type="TravelRequestSubmitted"
        # Targets Broker Lambda, sends failures to DLQ
        self.rule = events.Rule(
            self, "TravelRequestRule",
            event_bus=self.bus,
            event_pattern=events.EventPattern(
                source=["com.travel.system"],
                detail_type=["TravelRequestSubmitted"]
            )
        )

        self.rule.add_target(
            targets.LambdaFunction(
                self.broker_lambda,
                dead_letter_queue=self.dlq,
                retry_attempts=2 # Retry twice before DLQ
            )
        )
