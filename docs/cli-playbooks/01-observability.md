# Playbook 01 — Deploy and Operate OpsObservabilityStack

This stack is a permanent foundation. Deploy it once; all other ops-lab stacks
consume its SSM exports. Never tear it down while any downstream stack is live.

---

## Prerequisites

- AWS CLI configured for account `820242933814`, region `ap-southeast-2`
- CDK bootstrapped: `poetry run cdk bootstrap`
- `aws-ops-networking` stack already deployed (provides `/ops-lab/networking/vpc-id`)

---

## Deploy

```bash
poetry install
poetry run cdk deploy OpsObservabilityStack
```

CDK will output the SNS topic ARN, IAM policy ARN, and dashboard URL on completion.

---

## Subscribe an Email to Alerts

```bash
TOPIC_ARN=$(aws ssm get-parameter \
  --name /ops-lab/shared/sns-topic-arn \
  --query Parameter.Value --output text \
  --region ap-southeast-2)

aws sns subscribe \
  --topic-arn "$TOPIC_ARN" \
  --protocol email \
  --notification-endpoint you@example.com \
  --region ap-southeast-2
```

Check your inbox and confirm the subscription.

---

## Verify the Stack

```bash
poetry run python scripts/verify_observability.py
```

This checks all SSM parameters, the SNS topic, IAM policy, CW agent config,
dashboard, and SSM Hybrid Role.

---

## Read Exported SSM Parameters

All downstream stacks read these at deploy time:

```bash
aws ssm get-parameters-by-path \
  --path /ops-lab/shared \
  --region ap-southeast-2 \
  --query "Parameters[*].{Name:Name,Value:Value}" \
  --output table
```

---

## Use the CloudWatch Agent Config on a New Instance

The config stored at `/ops-lab/shared/cw-agent-config` is a template. The
`__LOG_GROUP__` placeholder must be replaced before calling `fetch-config`.

Example user-data snippet:

```bash
CONFIG_PATH=$(aws ssm get-parameter \
  --name /ops-lab/shared/cw-agent-config-ssm-path \
  --query Parameter.Value --output text --region ap-southeast-2)

# Fetch the template
aws ssm get-parameter --name "$CONFIG_PATH" \
  --query Parameter.Value --output text --region ap-southeast-2 \
  > /tmp/cw-agent.json

# Substitute the log group for this instance's role
sed -i 's|__LOG_GROUP__|/ops-lab/my-project/my-service|g' /tmp/cw-agent.json

# Write it to a local SSM config file and start the agent
amazon-cloudwatch-agent-ctl \
  -a fetch-config -s -m ec2 \
  -c file:/tmp/cw-agent.json
```

---

## Attach the IAM Policy to a Downstream Instance Profile

```bash
POLICY_ARN=$(aws ssm get-parameter \
  --name /ops-lab/shared/cloudwatch-write-policy-arn \
  --query Parameter.Value --output text \
  --region ap-southeast-2)

aws iam attach-role-policy \
  --role-name <InstanceRoleName> \
  --policy-arn "$POLICY_ARN"
```

---

## SSM Hybrid Activation (Proxmox VMs / On-Prem)

Register an on-prem host as an SSM managed instance:

```bash
# Create an activation (valid for 1 day, up to 10 registrations)
aws ssm create-activation \
  --iam-role SSMHybridRole \
  --registration-limit 10 \
  --expiration-date "$(date -u -d '+1 day' '+%Y-%m-%dT%H:%M:%SZ')" \
  --region ap-southeast-2
# Note the ActivationId and ActivationCode from the output.

# On the target VM, install the SSM agent and register:
sudo amazon-ssm-agent -register \
  -code "<ActivationCode>" \
  -id "<ActivationId>" \
  -region ap-southeast-2

sudo systemctl enable --now amazon-ssm-agent
```

On-prem instances ship logs to `/ops-lab/onprem/{hostname}` log groups.

---

## View the Dashboard

```bash
echo "https://ap-southeast-2.console.aws.amazon.com/cloudwatch/home\
?region=ap-southeast-2#dashboards:name=OpsLabDashboard"
```

---

## Tear Down (only if all downstream stacks are destroyed first)

```bash
poetry run cdk destroy OpsObservabilityStack
```

---

## Migration Note — aws-ssm-puppet-fleet

`aws-ssm-puppet-fleet` contains its own `ObservabilityStack` that duplicates
much of what this repo now owns (SNS topic, IAM policy, CW agent config, dashboard).
Once `OpsObservabilityStack` is the live foundation, the fleet stack should be
updated to:

1. Remove its `ObservabilityStack` entirely.
2. Read `/ops-lab/shared/sns-topic-arn` and `/ops-lab/shared/cloudwatch-write-policy-arn`
   from SSM Parameter Store instead of exporting them itself.
3. Point its CW alarms at the shared SNS topic ARN.
4. Remove the duplicate `OpsLabDashboard` — this repo owns the single dashboard.

Until that migration is done, both dashboards and both SNS topics will exist in
the account. The shared one (this repo) is the canonical destination.
