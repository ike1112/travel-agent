
from aws_cdk import (
    Stack,
    Duration,
    aws_cloudwatch as cw,
)
from constructs import Construct
# from infrastructure.stacks.workflow import WorkflowStack # Avoid circular import

class ObservabilityStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, workflow_stack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        dashboard = cw.Dashboard(self, "TravelAgentDashboard",
            dashboard_name="TravelAgent-Overview"
        )
        
        # 1. Step Functions Metrics (Manual Metric creation for reliability)
        sfn_executions_started = cw.Metric(
            namespace="AWS/States",
            metric_name="ExecutionsStarted",
            dimensions_map={"StateMachineArn": workflow_stack.state_machine.state_machine_arn},
            period=Duration.minutes(5),
            statistic="Sum"
        )
        sfn_executions_succeeded = cw.Metric(
            namespace="AWS/States",
            metric_name="ExecutionsSucceeded",
            dimensions_map={"StateMachineArn": workflow_stack.state_machine.state_machine_arn},
            period=Duration.minutes(5),
            statistic="Sum"
        )
        sfn_executions_failed = cw.Metric(
            namespace="AWS/States",
            metric_name="ExecutionsFailed",
            dimensions_map={"StateMachineArn": workflow_stack.state_machine.state_machine_arn},
            period=Duration.minutes(5),
            statistic="Sum"
        )
        
        sfn_widget = cw.GraphWidget(
            title="Workflow Executions",
            left=[sfn_executions_started, sfn_executions_succeeded, sfn_executions_failed],
            width=24
        )
        
        # 2. Broker Lambda Errors & Invocations
        # Use metric_invocations helpers if available on Function, or manual too
        broker_invocations = workflow_stack.broker_lambda.metric_invocations(period=Duration.minutes(5))
        broker_errors = workflow_stack.broker_lambda.metric_errors(period=Duration.minutes(5))
        
        broker_widget = cw.GraphWidget(
            title="Broker Lambda Activity",
            left=[broker_invocations],
            right=[broker_errors],
            width=12
        )
        
        # 3. Agent Lambda Errors (Aggregate View)
        # We can't easily aggregate unless we use math expressions, let's just show key agents
        flight_errors = workflow_stack.flight_lambda.metric_errors(period=Duration.minutes(5), label="Flight Errors")
        hotel_errors = workflow_stack.hotel_lambda.metric_errors(period=Duration.minutes(5), label="Hotel Errors")
        synthesis_errors = workflow_stack.synthesis_lambda.metric_errors(period=Duration.minutes(5), label="Synthesis Errors")
        delivery_errors = workflow_stack.delivery_lambda.metric_errors(period=Duration.minutes(5), label="Delivery Errors")
        
        agents_widget = cw.GraphWidget(
            title="Agent Errors",
            left=[flight_errors, hotel_errors, synthesis_errors, delivery_errors],
            width=12
        )
        
        # 4. DLQ Depth
        dlq_visible = workflow_stack.dlq.metric_approximate_number_of_messages_visible(
            period=Duration.minutes(1),
            statistic="Maximum",
            label="DLQ Depth"
        )
        
        dlq_widget = cw.GraphWidget(
            title="Dead Letter Queue Depth",
            left=[dlq_visible],
            width=24
        )

        # Add to Dashboard
        dashboard.add_widgets(sfn_widget)
        dashboard.add_widgets(broker_widget, agents_widget)
        dashboard.add_widgets(dlq_widget)
        
        print("Observability Stack Initialized with Dashboard")
