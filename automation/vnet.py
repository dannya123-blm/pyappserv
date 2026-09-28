from azure.identity import DefaultAzureCredential
from azure.mgmt.network import NetworkManagementClient

credential = DefaultAzureCredential()
subscription_id = "1e894640-118b-4225-86cf-2a809b92c7de"

network_client = NetworkManagementClient(credential, subscription_id)

resource_group_name = "rg-pystorage-lab"
location = "francecentral"
vnet_name = "vnet441"

vnet_parameters = {
    "location": location,
    
}