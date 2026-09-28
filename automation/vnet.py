from azure.identity import DefaultAzureCredential
from azure.mgmt.network import NetworkManagementClient

credential = DefaultAzureCredential()
subscription_id = "1e894640-118b-4225-86cf-2a809b92c7de"

network_client = NetworkManagementClient(credential, subscription_id)

resource_group_name = "rg-pystorage-lab"
location = "francecentral"
vnet_name = "vnet441"
subnet1_name = "subnet441"
subnet2_name = "subnet442"

vnet_parameters = {
    "properties": {
        "address_space": {
            "address_prefixes": [
                '10.0.0.0/16'
            ]
        }
    }

}

subnet1_parameters = {
    "properties": {
        "address_prefix": '10.0.1.0/24',
        delegations = {
        "name": "delegation1",
        "properties": {
            "service_name": "Microsoft.Web/serverFarms"
        }
    }
    }
}

subnet2_parameters = {
    "properties": {
        "address_prefix": '10.0.2.0/24',
        private_endpoint_network_policies = "Disabled"
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

print(f"Virtual Network created with ID: {vnet_result.id}")
print(f"Subnet 1 created with ID: {subnet1_result.id}")
print(f"Subnet 2 created with ID: {subnet2_result.id}")
