
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_lambda as lambda_,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_logs as logs,
    aws_dynamodb as dynamodb,
    aws_sqs as sqs,
    aws_iam as iam,
    aws_events as events,
    aws_events_targets as targets,
)
from constructs import Construct
import os

class WorkflowStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, bus: events.EventBus, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # === 1. DynamoDB Request Log Table ===
        self.request_table = dynamodb.Table(
            self, "TravelRequestLog",
            table_name="travel-agent-request-log",
            partition_key=dynamodb.Attribute(
                name="requestId",
                type=dynamodb.AttributeType.STRING
            ),
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY, 
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        # === 2. Dead Letter Queue ===
        self.dlq = sqs.Queue(
            self, "BrokerDLQ",
            queue_name="travel-agent-broker-dlq",
            retention_period=Duration.days(14)
        )

        # === 3. Broker Lambda ===
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
            function_name="travel-agent-broker",
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
        
        # === 3b. EventBridge Rule (Moved from Ingress) ===
        # Matches source="com.travel.system", detail-type="TravelRequestSubmitted"
        # Targets Broker Lambda
        self.rule = events.Rule(
            self, "TravelRequestRule",
            event_bus=bus, # Bus passed from IngressStack
            event_pattern=events.EventPattern(
                source=["com.travel.system"],
                detail_type=["TravelRequestSubmitted"]
            )
        )

        self.rule.add_target(
            targets.LambdaFunction(
                self.broker_lambda,
                dead_letter_queue=self.dlq,
                retry_attempts=2 
            )
        )

        # === 4. Define Agent Lambdas (Stubs) ===
        
        # Flight Agent
        self.flight_lambda = lambda_.Function(
            self, "FlightAgent",
            function_name="travel-agent-flight",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="agents.flight.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "AMADEUS_CLIENT_ID": os.environ.get("AMADEUS_CLIENT_ID", ""),
                "AMADEUS_CLIENT_SECRET": os.environ.get("AMADEUS_CLIENT_SECRET", ""),
            }
        )

        # Hotel Agent
        self.hotel_lambda = lambda_.Function(
            self, "HotelAgent",
            function_name="travel-agent-hotel",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="agents.hotel.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "GOOGLE_PLACES_API_KEY": os.environ.get("GOOGLE_PLACES_API_KEY", ""),
            }
        )

        # Weather Agent
        self.weather_lambda = lambda_.Function(
            self, "WeatherAgent",
            function_name="travel-agent-weather",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="agents.weather.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "OPENWEATHER_API_KEY": os.environ.get("OPENWEATHER_API_KEY", ""),
            }
        )

        # Events Agent
        self.events_lambda = lambda_.Function(
            self, "EventsAgent",
            function_name="travel-agent-events",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="agents.events.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "GOOGLE_PLACES_API_KEY": os.environ.get("GOOGLE_PLACES_API_KEY", ""),
            }
        )

        # Synthesis Agent
        self.synthesis_lambda = lambda_.Function(
            self, "SynthesisAgent",
            function_name="travel-agent-synthesis",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="agents.synthesis.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(60),
            memory_size=256, 
        )
        
        # Grant Bedrock access to Synthesis
        self.synthesis_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
                    "arn:aws:bedrock:*:*:inference-profile/us.anthropic.claude-3-haiku-20240307-v1:0",
                ],
            )
        )

        # Delivery Agent
        self.delivery_lambda = lambda_.Function(
            self, "DeliveryAgent",
            function_name="travel-agent-delivery",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="agents.delivery.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(30),
            memory_size=128,
        )
        
        # Grant Delivery access to Table
        self.request_table.grant_read_write_data(self.delivery_lambda)
        self.delivery_lambda.add_environment("REQUEST_TABLE_NAME", self.request_table.table_name)
        self.delivery_lambda.add_environment("SENDER_EMAIL", os.environ.get("SENDER_EMAIL", ""))
        
        # Grant SES SendEmail
        self.delivery_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=["*"], # In production, restrict to specific identity ARN
            )
        )
        
        # Error Handler Agent
        self.error_handler_lambda = lambda_.Function(
            self, "ErrorHandlerAgent",
            function_name="travel-agent-error-handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="agents.error_handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(10),
            memory_size=128,
        )
        
        # Grant Error Handler access to Table
        self.request_table.grant_read_write_data(self.error_handler_lambda)
        self.error_handler_lambda.add_environment("REQUEST_TABLE_NAME", self.request_table.table_name)

        # === 5. Define Step Functions Tasks ===
        
        # Flight Task
        flight_task = tasks.LambdaInvoke(
            self, "FlightSearch",
            lambda_function=self.flight_lambda,
            payload_response_only=True,
            result_path="$.flight_output",
        )

        # Hotel Task
        hotel_task = tasks.LambdaInvoke(
            self, "HotelSearch",
            lambda_function=self.hotel_lambda,
            payload_response_only=True,
        )

        # Weather Task
        weather_task = tasks.LambdaInvoke(
            self, "WeatherSearch",
            lambda_function=self.weather_lambda,
            payload_response_only=True,
        )

        # Events Task
        events_task = tasks.LambdaInvoke(
            self, "EventsSearch",
            lambda_function=self.events_lambda,
            payload_response_only=True,
        )
        
        # Synthesis Task
        synthesis_task = tasks.LambdaInvoke(
            self, "SynthesizeResults",
            lambda_function=self.synthesis_lambda,
            payload_response_only=True,
        )

        # Delivery Task
        delivery_task = tasks.LambdaInvoke(
            self, "DeliverEmail",
            lambda_function=self.delivery_lambda,
            payload_response_only=True,
        )
        
        # Error Handler Task
        error_handler_task = tasks.LambdaInvoke(
            self, "HandleError",
            lambda_function=self.error_handler_lambda,
        )
        
        # === 6. Define Workflow Structure ===
        
        # Parallel State (Hotel, Weather, Events)
        parallel = sfn.Parallel(
            self, "ParallelUpdates",
            result_path="$.parallel_results"
        )
        parallel.branch(hotel_task)
        parallel.branch(weather_task)
        parallel.branch(events_task)
        
        # Success Chain
        definition = flight_task.next(parallel).next(synthesis_task).next(delivery_task)
        
        # Add Catch
        flight_task.add_catch(error_handler_task, errors=["States.ALL"], result_path="$.error")
        parallel.add_catch(error_handler_task, errors=["States.ALL"], result_path="$.error")
        synthesis_task.add_catch(error_handler_task, errors=["States.ALL"], result_path="$.error")
        delivery_task.add_catch(error_handler_task, errors=["States.ALL"], result_path="$.error")

        # === 7. State Machine ===
        
        log_group = logs.LogGroup(self, "WorkflowLogGroup",
            log_group_name="/travel-agent/workflow",
            retention=logs.RetentionDays.ONE_MONTH
        )

        self.state_machine = sfn.StateMachine(
            self, "TravelAgentWorkflow",
            state_machine_name="travel-agent-workflow",
            definition=definition,
            timeout=Duration.minutes(5),
            tracing_enabled=True,
            state_machine_type=sfn.StateMachineType.EXPRESS,
            logs=sfn.LogOptions(
                destination=log_group,
                level=sfn.LogLevel.ALL
            )
        )
        
        # Grant Broker permission to start workflow
        self.state_machine.grant_start_execution(self.broker_lambda)
        self.broker_lambda.add_environment("STATE_MACHINE_ARN", self.state_machine.state_machine_arn)
