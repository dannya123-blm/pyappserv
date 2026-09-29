import os
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.mgmt.web import WebSiteManagementClient
from azure.mgmt.network import NetworkManagementClient

load_dotenv()

credential = DefaultAzureCredential()
subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
web_client = WebSiteManagementClient(credential, subscription_id)
network_client = NetworkManagementClient(credential, subscription_id)


resource_group_name = "rg-pystorage-lab"
location = "francecentral"
plan_name = "appserviceplan441"
app_name = "appservicename441"
vnet_name = "vnet441"
subnet1_name = "subnet441"

plan_parameters = {
    "location": location,
    "kind": "linux",
    "sku": {
        "name": "B1",
        "tier": "Basic",
        "capacity": 1
    },
    "properties": {
        "reserved": True
    }
       
}

subnet1_id = network_client.subnets.get(
    resource_group_name,
    vnet_name,
    subnet1_name
).id

poller = web_client.app_service_plans.begin_create_or_update(
    resource_group_name,
    plan_name,
    plan_parameters
)
plan_result = poller.result()


app_parameters = {
    "location": location,
    "kind": "app,linux",
    "identity": {
        "type": "SystemAssigned"
    },
    "properties": {
        "serverFarmId": plan_result.id,
        "virtualNetworkSubnetId": subnet1_id,
        "siteConfig": {
            "always_on": True, 
            "linuxFxVersion": "PYTHON|3.10"
        }
    }
    
    
}

poller = web_client.web_apps.begin_create_or_update(
    resource_group_name,
        app_name,
        app_parameters
)

app_result = poller.result()



print(f"App Service Plan created with ID: {plan_result.id}")

