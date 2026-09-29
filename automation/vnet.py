import os
from dotenv import load_dotenv
# vnet, 2 isolated subnets, private endpoint that links to subnet
from azure.identity import DefaultAzureCredential
from azure.mgmt.network import NetworkManagementClient

load_dotenv()

credential = DefaultAzureCredential()
subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")

network_client = NetworkManagementClient(credential, subscription_id)

resource_group_name = "rg-pystorage-lab"
location = "francecentral"
vnet_name = "vnet441"
subnet1_name = "subnet441"
subnet2_name = "subnet442"
priv_endpoint_name = "privendpoint441"


vnet_parameters = {
    "location": location,
    "properties": {
        "addressSpace": {
            "addressPrefixes": [
                '10.0.0.0/16'
            ]
        }
    }

}

subnet1_parameters = {
    "properties": {
        "addressPrefix": '10.0.1.0/24',
        "delegations": [{
            "name": "delegation1",
            "properties": {
                "serviceName": "Microsoft.Web/serverFarms"
            }
        }
            
        ]
    }
}

subnet2_parameters = {
    "properties": {
        "addressPrefix": '10.0.2.0/24',
        "privateEndpointNetworkPolicies": "Disabled"
    }

}

poller = network_client.virtual_networks.begin_create_or_update(
    resource_group_name,
    vnet_name,
    vnet_parameters
)
vnet_result = poller.result()

poller = network_client.subnets.begin_create_or_update(
    resource_group_name,
    vnet_name,
    subnet1_name,
    subnet1_parameters
)

subnet1_result = poller.result()

poller = network_client.subnets.begin_create_or_update(
    resource_group_name,
    vnet_name,
    subnet2_name,
    subnet2_parameters
)

subnet2_result = poller.result()

privendpoint_parameters = {
      "location": location,
      "properties": {
          "subnet": {
              "id": subnet2_result.id
          },
          "privateLinkServiceConnections": [
              {
                  "name": "privateLinkServiceConnections",
                  "properties": {
                      "privateLinkServiceId": "/subscriptions/1e894640-118b-4225-86cf-2a809b92c7de/resourceGroups/rg-pystorage-lab/providers/Microsoft.Storage/storageAccounts/pystorageacc441",
                      "groupIds": ["blob"]
                  }
              }
          ]
      }
  }

poller = network_client.private_endpoints.begin_create_or_update(
      resource_group_name,
      priv_endpoint_name,
      privendpoint_parameters
  )

priv_endpoint_result = poller.result()


print(f"Virtual Network created with ID: {vnet_result.id}")
print(f"Subnet 1 created with ID: {subnet1_result.id}")
print(f"Subnet 2 created with ID: {subnet2_result.id}")
print(f"Private Endpoint created with ID: {priv_endpoint_result.id}")
