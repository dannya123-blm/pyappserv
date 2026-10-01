import os
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.mgmt.keyvault import KeyVaultManagementClient

credential = DefaultAzureCredential()
subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
keyvault_client = KeyVaultManagementClient(credential, subscription_id)

resource_group_name = "rg-pystorage-lab"
location = "francecentral"
key_vault = "keyvault441"


key_vault_parameters = {
    "location": location,
    "properties": {
        "sku": "Standard_V2",
        
        
    }
    
    
}





