"""
Verify the OpsObservabilityStack is healthy and all SSM exports are present.

Usage:
    poetry run python scripts/verify_observability.py
"""

import sys
import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"

SSM_PARAMS = [
    "/ops-lab/shared/sns-topic-arn",
    "/ops-lab/shared/cloudwatch-write-policy-arn",
    "/ops-lab/shared/cw-agent-config-ssm-path",
    "/ops-lab/shared/log-retention-days",
]

CHECKS_PASSED = []
CHECKS_FAILED = []


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")
    CHECKS_PASSED.append(msg)


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    CHECKS_FAILED.append(msg)


def check_ssm_params(ssm_client) -> dict:
    print("\n── SSM Parameters ───────────────────────────────────────────────")
    values = {}
    for name in SSM_PARAMS:
        try:
            resp = ssm_client.get_parameter(Name=name)
            val = resp["Parameter"]["Value"]
            values[name] = val
            ok(f"{name} = {val}")
        except ClientError as e:
            fail(f"{name} — {e.response['Error']['Code']}")
    return values


def check_sns_topic(sns_client, topic_arn: str) -> None:
    print("\n── SNS Topic ────────────────────────────────────────────────────")
    if not topic_arn:
        fail("SNS topic ARN not found in SSM — skipping topic check")
        return
    try:
        attrs = sns_client.get_topic_attributes(TopicArn=topic_arn)
        name = attrs["Attributes"].get("DisplayName", topic_arn.split(":")[-1])
        subs = attrs["Attributes"].get("SubscriptionsConfirmed", "0")
        ok(f"Topic exists: {name}")
        if int(subs) == 0:
            print(f"  [WARN] No confirmed subscriptions — run 'aws sns subscribe' to receive alerts")
        else:
            ok(f"Confirmed subscriptions: {subs}")
    except ClientError as e:
        fail(f"Topic unreachable — {e.response['Error']['Code']}")


def check_iam_policy(iam_client, policy_arn: str) -> None:
    print("\n── IAM Managed Policy ───────────────────────────────────────────")
    if not policy_arn:
        fail("Policy ARN not found in SSM — skipping policy check")
        return
    try:
        resp = iam_client.get_policy(PolicyArn=policy_arn)
        name = resp["Policy"]["PolicyName"]
        ok(f"Policy exists: {name} ({policy_arn})")
    except ClientError as e:
        fail(f"Policy unreachable — {e.response['Error']['Code']}")


def check_cw_agent_config(ssm_client, config_path: str) -> None:
    print("\n── CW Agent Config ──────────────────────────────────────────────")
    if not config_path:
        fail("CW agent config SSM path not found — skipping config check")
        return
    try:
        resp = ssm_client.get_parameter(Name=config_path)
        import json
        cfg = json.loads(resp["Parameter"]["Value"])
        namespace = cfg.get("metrics", {}).get("namespace", "??")
        ok(f"Config present at {config_path} (namespace: {namespace})")
        # Verify placeholder is still in the template
        raw = resp["Parameter"]["Value"]
        if "__LOG_GROUP__" in raw:
            ok("__LOG_GROUP__ placeholder intact")
        else:
            fail("__LOG_GROUP__ placeholder missing from config — template may be corrupted")
    except ClientError as e:
        fail(f"Config param unreachable — {e.response['Error']['Code']}")
    except (json.JSONDecodeError, KeyError) as e:
        fail(f"Config param not valid JSON — {e}")


def check_dashboard(cw_client) -> None:
    print("\n── CloudWatch Dashboard ─────────────────────────────────────────")
    try:
        resp = cw_client.list_dashboards()
        names = [d["DashboardName"] for d in resp.get("DashboardEntries", [])]
        if "OpsLabDashboard" in names:
            ok("OpsLabDashboard exists")
        else:
            fail("OpsLabDashboard not found")
    except ClientError as e:
        fail(f"Dashboard list failed — {e.response['Error']['Code']}")


def check_hybrid_role(iam_client) -> None:
    print("\n── SSM Hybrid Role ──────────────────────────────────────────────")
    try:
        resp = iam_client.get_role(RoleName="SSMHybridRole")
        ok(f"SSMHybridRole exists (ARN: {resp['Role']['Arn']})")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            fail("SSMHybridRole not found")
        else:
            fail(f"Role check failed — {e.response['Error']['Code']}")


def main():
    session = boto3.Session(region_name=REGION)
    ssm_client = session.client("ssm")
    sns_client = session.client("sns")
    iam_client = session.client("iam")
    cw_client = session.client("cloudwatch")

    print(f"Verifying OpsObservabilityStack — region: {REGION}")

    params = check_ssm_params(ssm_client)
    check_sns_topic(sns_client, params.get("/ops-lab/shared/sns-topic-arn", ""))
    check_iam_policy(iam_client, params.get("/ops-lab/shared/cloudwatch-write-policy-arn", ""))

    config_path_param = params.get("/ops-lab/shared/cw-agent-config-ssm-path", "")
    check_cw_agent_config(ssm_client, config_path_param)
    check_dashboard(cw_client)
    check_hybrid_role(iam_client)

    print(f"\n── Summary ──────────────────────────────────────────────────────")
    print(f"  Passed: {len(CHECKS_PASSED)}   Failed: {len(CHECKS_FAILED)}")

    if CHECKS_FAILED:
        print("\nFailed checks:")
        for c in CHECKS_FAILED:
            print(f"  - {c}")
        sys.exit(1)

    print("  All checks passed.")


if __name__ == "__main__":
    main()
