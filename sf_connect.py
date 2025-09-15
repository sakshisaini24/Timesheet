from simple_salesforce import Salesforce

def connect_to_salesforce():
    """Establishes a connection to the Salesforce org."""
    
    USERNAME = "sakshi.saini427@agentforce.com"
    PASSWORD = "Aeth@12345"
    SECURITY_TOKEN = "vgTE2KQfzpF2uuYiPMcNx9ZMh"

    try:
        sf = Salesforce(
            username=USERNAME,
            password=PASSWORD,
            security_token=SECURITY_TOKEN,
        )
        return sf
    except Exception as e:
        print(f"Error connecting to Salesforce: {e}")
        return None
