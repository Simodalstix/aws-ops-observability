import os
import aws_cdk as cdk
from observability_lab.observability_stack import OpsObservabilityStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION"),
)

OpsObservabilityStack(app, "OpsObservabilityStack", env=env)

app.synth()
