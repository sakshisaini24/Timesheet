import datetime
import os.path
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from simple_salesforce import Salesforce

# Import the Salesforce connection function from your separate file
from sf_connect import connect_to_salesforce

# Define the scopes needed for the Google Calendar API
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Hardcoded Salesforce Activity ID for the prototype
# REPLACE THIS WITH A REAL RECORD ID FROM YOUR ORG
ACTIVITY_ID = 'a01gK00000Jw4wMQAR'

# CORRECT PICKLIST MAPPING
# Map the values from the calendar logic to your Salesforce picklist values
# MAKE SURE THESE VALUES EXACTLY MATCH THE ONES IN YOUR ORG
PICKLIST_MAPPING = {
    'PTO': 'PTO',      
    'Meetings': 'Business Day - Morning Shift - Standard Time',
    'Misc': 'Business Day - Morning Shift - Standard Time'
}

def get_calendar_service():
    """Handles the authentication flow for Google Calendar."""
    creds = None
    
    # Read credentials and token from environment variables
    credentials_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    token_json = os.environ.get('GOOGLE_TOKEN_JSON')
    
    if token_json:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    else:
        # Since we can't run a local server on Render, we cannot re-authenticate
        # if a token doesn't exist. We assume the token is always present.
        print("Error: Google Calendar token not found in environment variables.")
        return None

    if not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # We can still try to refresh the token if a valid one exists
            creds.refresh(Request())
        else:
            print("Error: Invalid or expired Google Calendar token. Cannot re-authenticate on Render.")
            return None
    
    return build('calendar', 'v3', credentials=creds)

def generate_timesheet_draft():
    """Fetches calendar events and generates a timesheet draft."""
    service = get_calendar_service()
    
    if service is None:
        return {'status': 'error', 'message': 'Google Calendar API token is not valid. Please provide a valid token in Render environment variables.'}
    
    try:
        today = datetime.date.today()
        start_of_week = today - datetime.timedelta(days=today.weekday())
        end_of_week = start_of_week + datetime.timedelta(days=4)
        
        timeMin = datetime.datetime.combine(start_of_week, datetime.time.min).isoformat() + 'Z'
        timeMax = datetime.datetime.combine(end_of_week, datetime.time.max).isoformat() + 'Z'

        events_result = service.events().list(calendarId='primary', timeMin=timeMin,
                                              timeMax=timeMax, singleEvents=True,
                                              orderBy='startTime').execute()
        events = events_result.get('items', [])
    except Exception as e:
        print(f"Error fetching calendar events: {e}")
        return {'status': 'error', 'message': f'Failed to fetch calendar events: {e}'}
    
    timesheet = {}
    for i in range(5):
        day = start_of_week + datetime.timedelta(days=i)
        timesheet[day.strftime('%A')] = {'date': day.isoformat(), 'data': {'Meetings': 0, 'Misc': 0}}
        
    for event in events:
        if 'OOO' in event.get('summary', '').upper() or 'OUT OF OFFICE' in event.get('summary', '').upper():
            start_date_str = event['start'].get('date')
            if start_date_str:
                start_date = datetime.date.fromisoformat(start_date_str)
                timesheet[start_date.strftime('%A')]['data']['PTO'] = 8
            continue
            
        if 'dateTime' in event['start'] and 'dateTime' in event['end']:
            start_date = datetime.datetime.fromisoformat(event['start'].get('dateTime').replace('Z', '+00:00'))
            end_date = datetime.datetime.fromisoformat(event['end'].get('dateTime').replace('Z', '+00:00'))
            duration_minutes = (end_date - start_date).total_seconds() / 60
            hours = round(duration_minutes / 60, 2)
            
            day_of_week = start_date.strftime('%A')
            
            if 'PTO' not in timesheet[day_of_week]['data']:
                timesheet[day_of_week]['data']['Meetings'] += hours
    
    for day, data in timesheet.items():
        if 'PTO' not in data['data']:
            misc_hours = 8 - data['data']['Meetings']
            if misc_hours > 0:
                data['data']['Misc'] = round(misc_hours, 2)
                
    return timesheet

def submit_to_salesforce(submitted_data):
    """Connects to SF, creates timesheet records, and submits for approval."""
    sf = connect_to_salesforce()
    if not sf:
        return {'status': 'error', 'message': 'Salesforce connection failed.'}

    # 1. Query for the current user's manager
    try:
        user_info = sf.query(f"SELECT Id, ManagerId FROM User WHERE Username = 'sakshi.saini427@agentforce.com'")
        if not user_info['records']:
            return {'status': 'error', 'message': 'User not found in Salesforce.'}
        
        user_id = user_info['records'][0]['Id']
        manager_id = user_info['records'][0]['ManagerId']
        
        if not manager_id:
            return {'status': 'error', 'message': 'User does not have a manager assigned in Salesforce.'}

    except Exception as e:
        return {'status': 'error', 'message': f"Error finding manager: {e}"}

    # 2. Create the Timesheet Records
    records_to_create = []
    for day, hours_data in submitted_data.items():
        for activity, hours in hours_data['data'].items():
            if hours > 0:
                record = {
                    'Activity__c': ACTIVITY_ID,
                    'Date__c': hours_data['date'],
                    'Status__c': 'Submitted',
                    'Time_Type__c': PICKLIST_MAPPING.get(activity, 'Uncategorized'),
                    'Hours__c': hours
                }
                records_to_create.append(record)
    
    created_ids = []
    for record in records_to_create:
        try:
            result = sf.Timesheet__c.create(record)
            created_ids.append(result['id'])
        except Exception as e:
            return {'status': 'error', 'message': f"Failed to create records: {e}"}

    # 3. Submit the created records for approval to the manager
    try:
        approval_requests = []
        for record_id in created_ids:
            approval_requests.append({
                "contextId": record_id,
                "nextApproverIds": [manager_id], 
                "comments": "Timesheet submitted automatically via Agentforce.",
                "actionType": "Submit"
            })
        
        data_payload = {"requests": approval_requests}
        response = sf.restful('process/approvals/', method='POST', data=json.dumps(data_payload))
        
    except Exception as e:
        return {'status': 'error', 'message': f"Failed to submit for approval: {e}"}

    return {'status': 'success', 'results': {'message': 'Timesheet submitted for approval.', 'ids': created_ids}}

def generate_bot_response(user_message):
    """Generates a dynamic response based on user input."""
    lower_message = user_message.lower()

    if "hours" in lower_message or "time on" in lower_message:
        draft = generate_timesheet_draft()
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
            if day in lower_message:
                day_data = draft.get(day.capitalize(), {}).get('data', {})
                if 'PTO' in day_data:
                    return f"You were marked as PTO on {day.capitalize()} for 8 hours."
                else:
                    return f"On {day.capitalize()}, you had {day_data.get('Meetings', 0)} hours of meetings and {day_data.get('Misc', 0)} hours marked as miscellaneous."
        return "I can't find that specific day. Please ask about a day of the week."

    elif "draft" in lower_message or "summary" in lower_message:
        draft = generate_timesheet_draft()
        summary = "Here is your timesheet draft summary:\n"
        for day, data in draft.items():
            day_data = data.get('data', {})
            if 'PTO' in day_data:
                summary += f"- {day}: 8 hours PTO\n"
            else:
                summary += f"- {day}: Meetings: {day_data.get('Meetings', 0)} hrs, Misc: {day_data.get('Misc', 0)} hrs\n"
        return summary
    
    elif "hello" in lower_message or "hi" in lower_message:
        return "Hello! I am your timesheet assistant. How can I help you with your timesheet draft?"

    # New Logic: Check for a command to update hours
    elif ("update" in lower_message or "change" in lower_message or "set" in lower_message) and ("hours" in lower_message or "time" in lower_message):
        import re
        
        # Find the number in the user's message
        numbers = re.findall(r'\b\d+\b', lower_message)
        hours = float(numbers[0]) if numbers else None
        
        # Find the day of the week
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
            if day in lower_message:
                if hours is not None:
                    draft = generate_timesheet_draft()
                    day_data = draft.get(day.capitalize(), {}).get('data', {})
                    day_data['Misc'] = hours
                    day_data['Meetings'] = 0 # Assuming the user wants to override
                    return f"Okay, I have set {hours} hours for {day.capitalize()}."

        return "I couldn't understand that. Please specify the day and the number of hours."

    return "I can help with questions about your timesheet. Try asking me about your hours on a specific day."


if __name__ == '__main__':
    draft = generate_timesheet_draft()
    print("Draft generated:", draft)

