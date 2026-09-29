import os
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.mgmt.sql import SqlManagementClient
from azure.mgmt.network import NetworkManagementClient

load_dotenv()

credential = DefaultAzureCredential()
subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
sql_client = SqlManagementClient(credential, subscription_id)
network_client = NetworkManagementClient(credential, subscription_id)

resource_group_name = "rg-pystorage-lab"
location = "francecentral"
server_name = "sqlserv441"
sql_db_name = "sqldb441"
priv_endpoint_name = "privendpoint442"
subnet2_name = "subnet442"
vnet_name = "vnet441"

sql_server_parameters = {
    "location": location,
    "properties": {
        "administratorLogin" : "adminLogin12",
        "administratorLoginPassword" : "adminLogin1Pass!!" 
    } 
}

sql_db_parameters = {
   "location": location,
   "sku": {
       "name": "S0",
       "tier": "Standard"
    }
}


poller = sql_client.servers.begin_create_or_update(
    resource_group_name,
    server_name,
    sql_server_parameters
)

server_result = poller.result()

poller = sql_client.databases.begin_create_or_update(
    resource_group_name,
    server_name,
    sql_db_name,
    sql_db_parameters
)

db_result = poller.result()

subnet2_id = network_client.subnets.get(
    resource_group_name,
    vnet_name,
    subnet2_name
).id

privendpoint_parameters = {
      "location": location,
      "properties": {
          "subnet": {
              "id": subnet2_id
          },
          "privateLinkServiceConnections": [
              {
                  "name": "privateLinkServiceConnections",
                  "properties": {
                      "privateLinkServiceId": print(f"/subscriptions/{os.getenv("AZURE_SUBSCRIPTION_ID")}/resourceGroups/rg-pystorage-lab/providers/Microsoft.Sql/servers/sqlserv441"),
                      "groupIds": ["sqlserver"]
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


print("SQL Infrastructure deployed successfully!")


