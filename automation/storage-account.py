from azure.identity import DefaultAzureCredential
from azure.mgmt.resource.resources import ResourceManagementClient

subscription_id = "1e894640-118b-4225-86cf-2a809b92c7de"

credential = DefaultAzureCredential()
resource_client = ResourceManagementClient(credential, subscription_id)

print("Credential object created:", credential)
print("Listing resource groups i already have: ")
for rg in resource_client.resource_groups.list():
    print(" -", rg.name)