from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_iam as iam,
    Duration,
    RemovalPolicy,
)
from constructs import Construct

class IngressStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # DynamoDB Request Log Table
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

        # Broker Lambda Execution Role
        # We define a custom role to adhere to least privilege
        broker_role = iam.Role(
            self, "BrokerLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        
        # Basic Lambda execution permissions (logs)
        broker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )
        
        # X-Ray Tracing permissions
        broker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AWSXrayWriteOnlyAccess")
        )

        # Scoped DynamoDB permissions
        self.request_table.grant_read_write_data(broker_role)

        # Scoped Bedrock permissions (specific model)
        broker_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
                    "arn:aws:bedrock:*:*:inference-profile/us.anthropic.claude-3-haiku-20240307-v1:0",
                ],
            )
        )

        # Broker Lambda Function
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
