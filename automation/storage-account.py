import os
from azure.identity import DefaultAzureCredential
from azure.mgmt.resource.resources import ResourceManagementClient
from azure.mgmt.storage import StorageManagementClient

subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")

credential = DefaultAzureCredential()
resource_client = ResourceManagementClient(credential, subscription_id)
storage_client = StorageManagementClient(credential, subscription_id)

resource_group_name = "rg-pystorage-lab"
location = "francecentral"

storage_name = "pystorageacc441"
parameters = {
   "location": location,
   "kind": "StorageV2",
   "sku": {"name": "Standard_GRS"},
   "properties": {
        "access_tier": "Hot",
        "allow_blob_public_access": False,
   }
    
 }

resource_client.resource_groups.begin_delete(
    resource_group_name,
    {"location": location}
)

poller = storage_client.storage_accounts.begin_create(
    resource_group_name,
    storage_name,
    parameters
    
)

account_result = poller.result()



print("Credential object created:", credential)
print("Listing resource groups i already have: ")
print(f"Resource group '{resource_group_name}' created (or already existed)")
print(f"Storage Account '{storage_name}' created (or already existed)")
for rg in resource_client.resource_groups.list():
    print(" -", rg.name)
