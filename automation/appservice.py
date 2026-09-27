from azure.identity import DefaultAzureCredential
from azure.mgmt.web import WebSiteManagementClient


credential = DefaultAzureCredential()
subscription_id = "1e894640-118b-4225-86cf-2a809b92c7de"
web_client = WebSiteManagementClient(credential, subscription_id)

resource_group_name = "rg-pystorage-lab"
location = "francecentral"
plan_name = "appserviceplan441"

plan_parameters = {
    "location": location,
    "kind": "app",
    "sku": {
        "name": "F1",
        "tier": "Free",
        "capacity": 1  
    },
}

poller = web_client.app_service_plans.begin_create_or_update(
    resource_group_name,
    plan_name,
    plan_parameters
)

plan_result = poller.result()
print(f"App Service Plan created with ID: {plan_result.id}")

