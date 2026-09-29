import os
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.mgmt.sql import SqlManagementClient

load_dotenv()

credential = DefaultAzureCredential()
subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
client = SqlManagementClient(credential, subscription_id)


