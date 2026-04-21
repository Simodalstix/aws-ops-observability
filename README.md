# aws-ops-observability

Shared observability foundation for the ops-lab AWS platform. Deployed permanently — all other platform projects depend on it for alerting, IAM, and CloudWatch agent configuration.

## What It Deploys

| Resource | Purpose |
|---|---|
| SNS topic | Single alert destination for all project alarms |
| IAM managed policy | CloudWatch write access, attached by downstream instance profiles and Lambda roles |
| CW agent config (SSM) | Parameterised template — any VM or instance substitutes its log group name at boot |
| CloudWatch dashboard | SSM/EC2 widgets; grows as platform projects deploy |
| Log retention policy | 30-day retention enforced on all `/ops-lab/*` log groups |

## SSM Parameters

**Reads:**
- `/ops-lab/networking/vpc-id`

**Writes:**
- `/ops-lab/shared/sns-topic-arn`
- `/ops-lab/shared/cloudwatch-write-policy-arn`
- `/ops-lab/shared/cw-agent-config-ssm-path`
- `/ops-lab/shared/log-retention-days`

## Stack

- **IaC:** AWS CDK (Python) + Poetry
- **Region:** ap-southeast-2
- **Stack name:** `OpsObservabilityStack`

## Usage

```bash
poetry install
cdk deploy
```

See [`docs/cli-playbooks/01-observability.md`](docs/cli-playbooks/01-observability.md) for full operational runbook.

## Platform Context

Part of a modular ops-lab platform:

- [`aws-ops-networking`](https://github.com/simoda/aws-ops-networking) — VPC foundation ✅
- **`aws-ops-observability`** — shared alerting and observability ✅ ← this repo
- `aws-ssm-puppet-fleet` — EC2 fleet via SSM + Puppet ✅
- `aws-3tier-platform` — ALB / ASG / RDS / ElastiCache 🔜
- `aws-event-driven-pipeline` — SQS / Kinesis / Lambda / S3 🔜
