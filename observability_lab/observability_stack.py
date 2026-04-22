import json
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    CfnOutput,
    aws_sns as sns,
    aws_iam as iam,
    aws_logs as logs,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_ssm as ssm,
)
from constructs import Construct

# ── Naming convention ────────────────────────────────────────────────────────
#   Log groups : /ops-lab/{project}/{service}
#   CW metrics : OpsLab/{Project}   (each project uses its own sub-namespace)
#   SSM params : /ops-lab/shared/...
#   Dashboard  : OpsLabDashboard    (one per region, grows as projects onboard)
# ────────────────────────────────────────────────────────────────────────────

LOG_GROUP_PREFIX = "/ops-lab"
# Namespace used by the shared CW agent config template.
# Downstream projects that supply their own agent config can use OpsLab/{Project}.
SHARED_METRIC_NAMESPACE = "OpsLab/Shared"
RETENTION = logs.RetentionDays.ONE_MONTH  # 30 days — enforced platform-wide

# Placeholder substituted by instance user-data before fetching this config.
# Instances run:  sed -i 's/__LOG_GROUP__/\/ops-lab\/my-project\/my-service/g' /tmp/cw-agent.json
LOG_GROUP_PLACEHOLDER = "__LOG_GROUP__"


class OpsObservabilityStack(Stack):
    """
    Permanent shared observability foundation for the ops-lab platform.
    Deploy this stack once — all other stacks consume its SSM exports.

        poetry run cdk deploy OpsObservabilityStack

    SSM params written:
        /ops-lab/shared/sns-topic-arn
        /ops-lab/shared/cloudwatch-write-policy-arn
        /ops-lab/shared/cw-agent-config-ssm-path
        /ops-lab/shared/log-retention-days
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        tags = {"Project": "ops-lab", "Stack": "observability"}
        for k, v in tags.items():
            cdk.Tags.of(self).add(k, v)

        # ── Read VPC ID from networking stack (informational — used in dashboard) ──
        vpc_id = ssm.StringParameter.value_from_lookup(
            self, "/ops-lab/networking/vpc-id"
        )

        # ── SNS topic — single alert destination for all project alarms ───────
        self.alert_topic = sns.Topic(
            self,
            "OpsLabAlerts",
            topic_name="ops-lab-alerts",
            display_name="OpsLab Alerts",
        )
        # Subscribe after deploy:
        #   aws sns subscribe \
        #     --topic-arn $(aws ssm get-parameter --name /ops-lab/shared/sns-topic-arn \
        #                   --query Parameter.Value --output text) \
        #     --protocol email \
        #     --notification-endpoint you@example.com \
        #     --region ap-southeast-2

        ssm.StringParameter(
            self,
            "SnsTopicArnParam",
            parameter_name="/ops-lab/shared/sns-topic-arn",
            string_value=self.alert_topic.topic_arn,
            description="Shared SNS alert topic ARN — all project alarms point here",
        )

        # ── IAM managed policy — CloudWatch write, scoped to OpsLab/* ────────
        # StringLike covers all sub-namespaces (OpsLab/SsmFleet, OpsLab/3Tier, etc.)
        # so downstream projects don't need their own write policy.
        self.cw_write_policy = iam.ManagedPolicy(
            self,
            "OpsLabCwWritePolicy",
            managed_policy_name="OpsLabCloudWatchWrite",
            description="CloudWatch write access for all OpsLab agents and instances",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["cloudwatch:PutMetricData"],
                    resources=["*"],
                    conditions={
                        "StringLike": {
                            "cloudwatch:namespace": "OpsLab/*",
                        }
                    },
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogStreams",
                    ],
                    resources=[
                        f"arn:aws:logs:*:*:log-group:{LOG_GROUP_PREFIX}/*",
                        f"arn:aws:logs:*:*:log-group:{LOG_GROUP_PREFIX}/*:*",
                    ],
                ),
                # CW agent needs to read its own SSM config at boot
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["ssm:GetParameter"],
                    resources=[
                        f"arn:aws:ssm:*:*:parameter{LOG_GROUP_PREFIX}/*",
                    ],
                ),
            ],
        )

        ssm.StringParameter(
            self,
            "CwWritePolicyArnParam",
            parameter_name="/ops-lab/shared/cloudwatch-write-policy-arn",
            string_value=self.cw_write_policy.managed_policy_arn,
            description="OpsLabCloudWatchWrite managed policy ARN — attach to any instance profile or Lambda role",
        )

        # ── CloudWatch agent config template — stored in SSM ─────────────────
        # Generic template: instances substitute LOG_GROUP_PLACEHOLDER at boot
        # before calling amazon-cloudwatch-agent-ctl -a fetch-config.
        # Projects that need per-service log routing should supply their own config
        # derived from this template.
        cw_agent_config = {
            "agent": {
                "metrics_collection_interval": 60,
                "run_as_user": "root",
            },
            "logs": {
                "logs_collected": {
                    "files": {
                        "collect_list": [
                            {
                                "file_path": "/var/log/messages",
                                "log_group_name": LOG_GROUP_PLACEHOLDER,
                                "log_stream_name": "{instance_id}",
                                "timezone": "UTC",
                            },
                            {
                                "file_path": "/var/log/cloud-init-output.log",
                                "log_group_name": LOG_GROUP_PLACEHOLDER,
                                "log_stream_name": "{instance_id}-cloud-init",
                                "timezone": "UTC",
                            },
                        ]
                    }
                }
            },
            "metrics": {
                "namespace": SHARED_METRIC_NAMESPACE,
                "metrics_collected": {
                    "cpu": {
                        "measurement": ["cpu_usage_active"],
                        "metrics_collection_interval": 60,
                        "totalcpu": True,
                        "append_dimensions": {"InstanceId": "${aws:InstanceId}"},
                    },
                    "mem": {
                        "measurement": ["mem_used_percent", "mem_available"],
                        "metrics_collection_interval": 60,
                        "append_dimensions": {"InstanceId": "${aws:InstanceId}"},
                    },
                    "disk": {
                        "measurement": ["disk_used_percent"],
                        "metrics_collection_interval": 60,
                        "resources": ["/"],
                        "append_dimensions": {"InstanceId": "${aws:InstanceId}"},
                    },
                },
                "append_dimensions": {
                    "InstanceId": "${aws:InstanceId}",
                    "InstanceType": "${aws:InstanceType}",
                },
                "aggregation_dimensions": [["InstanceId"]],
            },
        }

        cw_agent_config_param_name = "/ops-lab/shared/cw-agent-config"
        ssm.StringParameter(
            self,
            "CwAgentConfigParam",
            parameter_name=cw_agent_config_param_name,
            string_value=json.dumps(cw_agent_config, indent=2),
            description=f"CW agent config template — replace {LOG_GROUP_PLACEHOLDER} with target log group before fetch-config",
        )

        ssm.StringParameter(
            self,
            "CwAgentConfigPathParam",
            parameter_name="/ops-lab/shared/cw-agent-config-ssm-path",
            string_value=cw_agent_config_param_name,
            description="SSM path of the shared CW agent config template",
        )

        # ── Log retention constant ────────────────────────────────────────────
        # Downstream stacks read this and apply it to their own log groups.
        ssm.StringParameter(
            self,
            "LogRetentionDaysParam",
            parameter_name="/ops-lab/shared/log-retention-days",
            string_value="30",
            description="Log retention days enforced across all /ops-lab/* log groups",
        )

        # ── IAM role for SSM Hybrid Activations (Proxmox VMs, on-prem hosts) ─
        # Used when creating an activation:
        #   aws ssm create-activation \
        #     --iam-role SSMHybridRole \
        #     --registration-limit 10 \
        #     --region ap-southeast-2
        self.hybrid_role = iam.Role(
            self,
            "SSMHybridRole",
            role_name="SSMHybridRole",
            assumed_by=iam.ServicePrincipal("ssm.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
            ],
        )
        self.hybrid_role.add_managed_policy(self.cw_write_policy)

        # ── CloudWatch Alarms ─────────────────────────────────────────────────
        alarm_action = cw_actions.SnsAction(self.alert_topic)

        cpu_alarm = cw.Alarm(
            self,
            "HighCpuAlarm",
            alarm_name="OpsLab-HighCPU",
            alarm_description="CPU above 80% for 10 minutes (OpsLab/Shared namespace)",
            metric=cw.Metric(
                namespace=SHARED_METRIC_NAMESPACE,
                metric_name="cpu_usage_active",
                statistic="Average",
                period=cdk.Duration.minutes(5),
            ),
            threshold=80,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        cpu_alarm.add_alarm_action(alarm_action)

        mem_alarm = cw.Alarm(
            self,
            "HighMemAlarm",
            alarm_name="OpsLab-HighMemory",
            alarm_description="Memory above 85% for 10 minutes (OpsLab/Shared namespace)",
            metric=cw.Metric(
                namespace=SHARED_METRIC_NAMESPACE,
                metric_name="mem_used_percent",
                statistic="Average",
                period=cdk.Duration.minutes(5),
            ),
            threshold=85,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        mem_alarm.add_alarm_action(alarm_action)

        # ── CloudWatch Dashboard ──────────────────────────────────────────────
        # One dashboard for the whole platform. New project rows are added here
        # as each downstream stack onboards. Widget layout: 24 columns wide.
        dashboard = cw.Dashboard(
            self,
            "OpsLabDashboard",
            dashboard_name="OpsLabDashboard",
        )

        dashboard.add_widgets(
            cw.TextWidget(
                markdown=(
                    "# OpsLab Platform — ap-southeast-2\n"
                    f"VPC: `{vpc_id}` | "
                    "Shared observability foundation. Add new project rows below as stacks onboard."
                ),
                width=24,
                height=2,
            ),
        )

        dashboard.add_widgets(
            cw.GraphWidget(
                title="CPU Utilisation % (OpsLab/Shared)",
                left=[
                    cw.Metric(
                        namespace=SHARED_METRIC_NAMESPACE,
                        metric_name="cpu_usage_active",
                        statistic="Average",
                        period=cdk.Duration.minutes(5),
                        label="CPU %",
                    )
                ],
                width=12,
                height=6,
            ),
            cw.GraphWidget(
                title="Memory Used % (OpsLab/Shared)",
                left=[
                    cw.Metric(
                        namespace=SHARED_METRIC_NAMESPACE,
                        metric_name="mem_used_percent",
                        statistic="Average",
                        period=cdk.Duration.minutes(5),
                        label="Mem %",
                    )
                ],
                width=12,
                height=6,
            ),
        )

        dashboard.add_widgets(
            cw.GraphWidget(
                title="Disk Used % — root volume (OpsLab/Shared)",
                left=[
                    cw.Metric(
                        namespace=SHARED_METRIC_NAMESPACE,
                        metric_name="disk_used_percent",
                        statistic="Average",
                        period=cdk.Duration.minutes(5),
                        label="Disk %",
                    )
                ],
                width=12,
                height=6,
            ),
            cw.AlarmStatusWidget(
                title="Alarm States",
                alarms=[cpu_alarm, mem_alarm],
                width=12,
                height=6,
            ),
        )

        # ── CloudFormation outputs ────────────────────────────────────────────
        CfnOutput(
            self,
            "OpsLabAlertTopicArn",
            value=self.alert_topic.topic_arn,
            export_name="OpsLabAlertTopicArn",
            description="SNS topic ARN — subscribe an email to receive all OpsLab alerts",
        )
        CfnOutput(
            self,
            "OpsLabCwWritePolicyArn",
            value=self.cw_write_policy.managed_policy_arn,
            export_name="OpsLabCwWritePolicyArn",
            description="Managed policy ARN — attach to any instance profile or Lambda role",
        )
        CfnOutput(
            self,
            "SSMHybridRoleName",
            value=self.hybrid_role.role_name,
            export_name="SSMHybridRoleName",
            description="IAM role for SSM Hybrid Activations (Proxmox VMs, on-prem)",
        )
        CfnOutput(
            self,
            "OpsLabDashboardUrl",
            value=(
                "https://ap-southeast-2.console.aws.amazon.com/cloudwatch/home"
                "?region=ap-southeast-2#dashboards:name=OpsLabDashboard"
            ),
            description="CloudWatch dashboard URL",
        )
        CfnOutput(
            self,
            "CwAgentConfigSsmPath",
            value=cw_agent_config_param_name,
            description="SSM path of the shared CW agent config template",
        )
