import datetime
import os.path
import json
import re
import base64
import mimetypes
import openai

import google.generativeai as genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

from fpdf import FPDF
from simple_salesforce import Salesforce
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment

# Import the Salesforce connection function from your separate file
from sf_connect import connect_to_salesforce


# -----------------------------
# Constants
# -----------------------------
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
openai.api_key = OPENAI_API_KEY

GEN_API_KEY = os.environ.get('GEMINI_API_KEY')
genai.configure(api_key=GEN_API_KEY)

# Hardcoded Salesforce Activity ID for the prototype
# REPLACE THIS WITH A REAL RECORD ID FROM YOUR ORG
ACTIVITY_ID = 'a01gK00000Jw4wMQAR'

_LAST_PDF_PATH = None
_TIMESHEET_DRAFT = None

NUM_DICT = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
    'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12
}

# Correct picklist mapping
PICKLIST_MAPPING = {
    'PTO': 'PTO',
    'Meetings': 'Business Day - Morning Shift - Standard Time',
    'Misc': 'Business Day - Morning Shift - Standard Time'
}

timesheet_function = {
    "name": "update_timesheet",
    "description": "Update the timesheet draft with hours for a given day",
    "parameters": {
        "type": "object",
        "properties": {
            "day": {
                "type": "string",
                "enum": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                "description": "The day of the week to update"
            },
            "activity": {
                "type": "string",
                "enum": ["Meetings", "Misc", "PTO"],
                "description": "Which activity to update"
            },
            "hours": {
                "type": "number",
                "description": "Number of hours to set for the activity"
            }
        },
        "required": ["day", "activity", "hours"]
    }
}

# -----------------------------
# PDF Generation
# -----------------------------

class PDF(FPDF):
    def header(self):
    
        self.image('logo.png', 10, 8, 25)
        self.set_font('Inter', 'B', 20)
        self.cell(0, 10, 'Weekly Timesheet Summary', 0, 1, 'C')
        self.ln(15)

    def footer(self):
        
        self.set_y(-15)
        self.set_font('Inter', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

def create_timesheet_pdf(submitted_data):
    """Generates a professional, branded PDF with a table and color."""
    
    pdf = PDF('P', 'mm', 'A4')
    
    try:
        pdf.add_font('Inter', '', 'Inter-Regular.ttf', uni=True)
        pdf.add_font('Inter', 'B', 'Inter-Bold.ttf', uni=True) 
        pdf.add_font('Inter', 'I', 'Inter-Italic.ttf', uni=True) 
        pdf.set_font('Inter', '', 11)
    except RuntimeError:
        print("Font file not found. Using default Arial.")
        pdf.set_font('Arial', '', 11)

    pdf.add_page()
    
    # Table Header (This part is correct)
    pdf.set_font('Inter', 'B', 12)
    pdf.set_fill_color(240, 240, 240) 
    pdf.cell(30, 10, 'Day', 1, 0, 'C', 1)
    pdf.cell(70, 10, 'Details', 1, 0, 'C', 1)
    pdf.cell(25, 10, 'Hours', 1, 0, 'C', 1)
    pdf.cell(65, 10, 'Productivity Insight', 1, 1, 'C', 1)

    total_hours = 0
    # This loop builds the main rows of the table
    for day, hours_data in submitted_data.items():
        pdf.set_font('Inter', '', 10)
        is_pto = 'PTO' in hours_data['data']
        daily_hours = sum(hours_data['data'].values())
        
        if not is_pto:
            total_hours += daily_hours

        details_str = ""
        for activity, hours in hours_data['data'].items():
            details_str += f"- {activity}: {hours} hrs\n"
        details_str = details_str.strip()
        
        num_lines = details_str.count('\n') + 1
        cell_height = 8 
        row_height = num_lines * cell_height

        y_before_row = pdf.get_y()

        pdf.multi_cell(30, row_height, day, 1, 'C', 0)
        
        pdf.set_y(y_before_row)
        pdf.set_x(40)
        pdf.multi_cell(70, cell_height, details_str, 1, 'L', 0)

        pdf.set_y(y_before_row)
        pdf.set_x(110)
        pdf.multi_cell(25, row_height, str(daily_hours), 1, 'C', 0)

        daily_productivity_message = ""
        color = (0, 0, 0)
        if is_pto:
            daily_productivity_message = "On Leave"
            color = (128, 128, 128)
        else:
            if daily_hours >= 10:
                daily_productivity_message = "Excellent! Remember to get some rest."
                color = (220, 53, 69)
            elif daily_hours >= 8:
                daily_productivity_message = "Excellent! Keep it up."
                color = (40, 167, 69)
            elif daily_hours >= 6:
                daily_productivity_message = "Good. Solid day."
                color = (0, 123, 255)
            else:
                daily_productivity_message = "Room for improvement."
                color = (255, 193, 7)

        pdf.set_text_color(*color)
        pdf.set_y(y_before_row)
        pdf.set_x(135)
        pdf.multi_cell(65, row_height, daily_productivity_message, 1, 'C', 0)

        pdf.set_text_color(0, 0, 0)
        pdf.set_y(y_before_row + row_height)
    
    # <-- FIX: This block was moved outside the loop by un-indenting it.
    # This code now runs only ONCE after the loop is finished.
    pdf.set_font('Inter', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(100, 12, 'Total Productive Hours', 1, 0, 'R', 1)
    pdf.cell(90, 12, f'{total_hours} hours', 1, 1, 'C', 1)

    pdf_path = f"timesheet_summary_{datetime.date.today().isoformat()}.pdf"
    pdf.output(pdf_path)
    return pdf_path
# -----------------------------
# Email Sending
# -----------------------------
def send_timesheet_email(pdf_path, user_email):
    """Sends an email with the generated PDF attached using SendGrid."""
    try:
        api_key = os.environ.get('SENDGRID_API_KEY')
        if not api_key:
            print("Error: SendGrid API key not found in environment variables.")
            return False

        message = Mail(
            from_email='sakshi.tech24@gmail.com',
            to_emails=user_email,
            subject='Your Weekly Timesheet Summary',
            plain_text_content='Please find your timesheet summary attached.'
        )

        # Attach the PDF
        with open(pdf_path, 'rb') as f:
            data = f.read()
            encoded_file = base64.b64encode(data).decode()

        attachedFile = Attachment(
            file_content=encoded_file,
            file_name=os.path.basename(pdf_path),
            file_type=mimetypes.guess_type(pdf_path)[0],
            disposition='attachment',
            content_id='timesheet_summary_pdf'
        )
        message.attachment = attachedFile

        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"Email sent with status code: {response.status_code}")
        return True

    except Exception as e:
        print(f"Error sending email with SendGrid: {e}")
        return False


# -----------------------------
# Google Calendar
# -----------------------------
def get_calendar_service():
    """
    Returns an authorized Google Calendar service instance.
    Automatically refreshes access token using the refresh_token.
    Requires environment variables:
        - GOOGLE_CREDENTIALS_JSON (client secret JSON)
        - GOOGLE_TOKEN_JSON (token JSON with refresh_token)
    """

    credentials_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    token_json_str = os.environ.get('GOOGLE_TOKEN_JSON')

    if not credentials_json or not token_json_str:
        raise Exception("Missing GOOGLE_CREDENTIALS_JSON or GOOGLE_TOKEN_JSON environment variable")

    # Load token
    token_data = json.loads(token_json_str)
    creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    # Refresh token if expired
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print("Google access token refreshed successfully")
            except Exception as e:
                raise Exception(f"Failed to refresh token: {e}")
        else:
            raise Exception("Invalid token: no refresh_token available or token invalid")

    # Build service
    service = build('calendar', 'v3', credentials=creds)
    return service

# -----------------------------
# Timesheet Draft Generation
# -----------------------------
def generate_timesheet_draft():
    """Generates a draft timesheet based on Google Calendar events."""
    global _TIMESHEET_DRAFT
    if _TIMESHEET_DRAFT is not None:
        return _TIMESHEET_DRAFT

    service = get_calendar_service()
    if service is None:
        return {'status': 'error', 'message': 'Google Calendar API token is not valid.'}

    try:
        today = datetime.date.today()
        start_of_week = today - datetime.timedelta(days=today.weekday())
        end_of_week = start_of_week + datetime.timedelta(days=4)

        timeMin = datetime.datetime.combine(start_of_week, datetime.time.min).isoformat() + 'Z'
        timeMax = datetime.datetime.combine(end_of_week, datetime.time.max).isoformat() + 'Z'

        events_result = service.events().list(
            calendarId='primary',
            timeMin=timeMin,
            timeMax=timeMax,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])

    except Exception as e:
        print(f"Error fetching calendar events: {e}")
        return {'status': 'error', 'message': f'Failed to fetch calendar events: {e}'}

    # Initialize timesheet dictionary for Mon–Fri
    timesheet = {}
    for i in range(5):
        day = start_of_week + datetime.timedelta(days=i)
        timesheet[day.strftime('%A')] = {
            'date': day.isoformat(),
            'data': {'Meetings': 0, 'Misc': 0}
        }

    # Process events
    for event in events:
        if 'OOO' in event.get('summary', '').upper() or 'OUT OF OFFICE' in event.get('summary', '').upper():
            start_date_str = event['start'].get('date')
            if start_date_str:
                start_date = datetime.date.fromisoformat(start_date_str)
                timesheet[start_date.strftime('%A')]['data']['PTO'] = 8
            continue

        if 'dateTime' in event['start'] and 'dateTime' in event['end']:
            start_date = datetime.datetime.fromisoformat(
                event['start']['dateTime'].replace('Z', '+00:00')
            )
            end_date = datetime.datetime.fromisoformat(
                event['end']['dateTime'].replace('Z', '+00:00')
            )
            duration_minutes = (end_date - start_date).total_seconds() / 60
            hours = round(duration_minutes / 60, 2)

            day_of_week = start_date.strftime('%A')
            if 'PTO' not in timesheet[day_of_week]['data']:
                timesheet[day_of_week]['data']['Meetings'] += hours

    # Fill Misc hours
    for day, data in timesheet.items():
        if 'PTO' not in data['data']:
            misc_hours = 8 - data['data']['Meetings']
            if misc_hours > 0:
                data['data']['Misc'] = round(misc_hours, 2)

    _TIMESHEET_DRAFT = timesheet
    return _TIMESHEET_DRAFT


# -----------------------------
# Salesforce Submission
# -----------------------------
def submit_to_salesforce(submitted_data):
    """Submits timesheet data to Salesforce and triggers approval workflow."""
    sf = connect_to_salesforce()
    global _LAST_PDF_PATH
    if not sf:
        return {'status': 'error', 'message': 'Salesforce connection failed.'}

    try:
        user_info = sf.query(
            "SELECT Id, ManagerId, Email FROM User WHERE Username = 'sakshi.saini427@agentforce.com'"
        )
        if not user_info['records']:
            return {'status': 'error', 'message': 'User not found in Salesforce.'}

        user_id = user_info['records'][0]['Id']
        manager_id = user_info['records'][0]['ManagerId']
        user_email = user_info['records'][0]['Email']

        if not manager_id:
            return {'status': 'error', 'message': 'User does not have a manager assigned in Salesforce.'}

    except Exception as e:
        return {'status': 'error', 'message': f"Error finding manager: {e}"}

    records_to_create = []
    for day, hours_data in submitted_data.items():
        total_hours = hours_data['data'].get('Meetings', 0) + hours_data['data'].get('Misc', 0)
        is_pto = 'PTO' in hours_data['data']
        if is_pto:
            total_hours = 8  # A full day is 8 hours
            record = {
                'Activity__c': ACTIVITY_ID,
                'Date__c': hours_data['date'],
                'Status__c': 'Submitted',
                'Time_Type__c': PICKLIST_MAPPING.get('PTO', 'Uncategorized'),
                'Hours__c': total_hours
            }
            records_to_create.append(record)
            # Create record only if it's NOT PTO
        if not is_pto:
            record = {
                'Activity__c': ACTIVITY_ID,
                'Date__c': hours_data['date'],
                'Status__c': 'Submitted',
                'Time_Type__c': PICKLIST_MAPPING.get('Misc', 'Uncategorized'),
                'Hours__c': total_hours
            }
            records_to_create.append(record)

    created_ids = []
    for record in records_to_create:
        try:
            result = sf.Timesheet__c.create(record)
            created_ids.append(result['id'])
        except Exception as e:
            return {'status': 'error', 'message': f"Failed to create records: {e}"}

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
        sf.restful('process/approvals/', method='POST', data=json.dumps(data_payload))

    except Exception as e:
        return {'status': 'error', 'message': f"Failed to submit for approval: {e}"}

    pdf_path = create_timesheet_pdf(submitted_data)
    _LAST_PDF_PATH = pdf_path

    return {'status': 'success', 'results': {'message': 'Timesheet submitted for approval.', 'ids': created_ids}}


# -----------------------------
# Draft Updates
# -----------------------------
def update_timesheet_draft(day, new_hours):
    global _TIMESHEET_DRAFT
    if _TIMESHEET_DRAFT and day in _TIMESHEET_DRAFT:
        _TIMESHEET_DRAFT[day]['data']['Misc'] = new_hours
        _TIMESHEET_DRAFT[day]['data']['Meetings'] = 0
        return True
    return False

# -----------------------------
# Gemini Integration
# -----------------------------
def generate_bot_response(user_message):
    global _TIMESHEET_DRAFT
    draft_summary = _TIMESHEET_DRAFT or {}
    prompt = f"You are a helpful timesheet assistant.\nTimesheet: {draft_summary}\nUser asks: {user_message}"

    try:
        response = genai.chat.create(
            model="chat-bison-001",
            messages=[{"author": "user", "content": prompt}],
            temperature=0.2
        )
        answer = response.last["content"][0]["text"]
        return answer
    except Exception as e:
        return f"Error generating response: {e}"


#Updating the draft with user input

def update_draft_from_chat(user_message):
    """
    Updates the draft from chat, converting number words to digits
    AND handling special commands like 'PTO'.
    """
    global _TIMESHEET_DRAFT
    if not _TIMESHEET_DRAFT:
        return {"status": "error", "response": "No draft found."}

    processed_message = user_message.lower()
    for word, number in NUM_DICT.items():
        if word in processed_message:
            processed_message = processed_message.replace(word, str(number))

    # Pattern 1: Look for a day and a number
    number_match = re.search(r"(monday|tuesday|wednesday|thursday|friday).+?(\d+\.?\d*|\d+)", processed_message, re.I)
    
    # Pattern 2: Look for a day and "pto"
    pto_match = re.search(r"(monday|tuesday|wednesday|thursday|friday).+?(pto)", processed_message, re.I)

    if number_match:
        day = number_match.group(1).capitalize()
        hours = float(number_match.group(2))
        if day in _TIMESHEET_DRAFT:
            # Set total hours, assigning all to Misc and clearing other categories
            _TIMESHEET_DRAFT[day]['data']['Misc'] = hours
            _TIMESHEET_DRAFT[day]['data']['Meetings'] = 0
            if 'PTO' in _TIMESHEET_DRAFT[day]['data']:
                del _TIMESHEET_DRAFT[day]['data']['PTO'] # Remove PTO if setting hours
            
            return {
                "status": "success",
                "response": f"Updated {day} with a total of {hours} hours.",
                "draft": _TIMESHEET_DRAFT
            }
            
    # --- THIS IS THE NEW LOGIC ---
    elif pto_match:
        day = pto_match.group(1).capitalize()
        if day in _TIMESHEET_DRAFT:
            # Set the day to a full 8-hour PTO day
            _TIMESHEET_DRAFT[day]['data']['PTO'] = 8
            _TIMESHEET_DRAFT[day]['data']['Misc'] = 0
            _TIMESHEET_DRAFT[day]['data']['Meetings'] = 0
            
            return {
                "status": "success",
                "response": f"Set {day} as a full day of PTO.",
                "draft": _TIMESHEET_DRAFT
            }

    return {"status": "error", "response": "I was unable to update the timesheet with that information. Please try again."}



# In generate_timesheet.py

def process_chat_command(user_message):
    """
    Processes advanced user commands, including multiple actions in a single sentence.
    """
    global _TIMESHEET_DRAFT
    message_lower = user_message.lower()

    if 'submit' in message_lower or 'looks good' in message_lower or 'correct' in message_lower:
        return {'status': 'submitting', 'response': 'Great! Finalizing and submitting your timesheet now...', 'draft': _TIMESHEET_DRAFT}

    try:
        # --- NEW: A more advanced prompt that asks for a LIST of actions ---
        prompt = f"""
        You are a powerful timesheet parsing engine. Analyze the user's request: '{user_message}'.
        Your task is to extract ALL actions the user wants to take. Some requests may have one action, others may have multiple.
        
        For each action, extract the day, the hours, and the activity.
        - The day must be one of: Monday, Tuesday, Wednesday, Thursday, Friday.
        - The hours MUST be a number. If the activity is 'PTO', and no hours are mentioned, assume 8.
        - The activity is what the user is describing (e.g., 'PTO', 'Misc', 'Project Work').

        Respond ONLY with a single JSON object. The object should have one key, "actions", which contains a list of action objects.
        Example for "Change Monday to 4 hours PTO and 4 hours misc":
        {{"actions": [{{"day": "Monday", "hours": 4, "activity": "PTO"}}, {{"day": "Monday", "hours": 4, "activity": "Misc"}}]}}
        
        If you cannot determine any valid actions, respond with {{"actions": []}}.
        """
        
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        
        json_response_text = response.text.strip().replace('`', '').replace('json', '')
        parsed_data = json.loads(json_response_text)
        actions = parsed_data.get('actions', [])

        if not actions:
            return {"status": "error", "response": "I'm sorry, I couldn't find any specific actions in your request."}

        # --- NEW: Loop through the list of actions and apply each one ---
        confirmation_messages = []
        # First, clear the day's data once to avoid conflicts
        day_to_update = actions[0].get('day')
        if day_to_update:
             _TIMESHEET_DRAFT[day_to_update.capitalize()]['data'] = {'Meetings': 0, 'Misc': 0}

        for action in actions:
            day = action.get('day')
            hours = action.get('hours')
            activity = action.get('activity', 'Misc')

            if day and hours is not None:
                if _update_draft_hours(day, hours, activity, clear_day=False): # Pass a flag to prevent re-clearing
                    confirmation_messages.append(f"{hours} hours for {activity}")
                else:
                    return {"status": "error", "response": f"I couldn't find {day} in the current draft."}
        
        if confirmation_messages:
            full_confirmation = f"OK. I've updated {day_to_update} with " + " and ".join(confirmation_messages) + "."
            return {"status": "success", "response": full_confirmation, "draft": _TIMESHEET_DRAFT}

    except Exception as e:
        print(f"AI parsing or draft update failed: {e}")
        return {"status": "error", "response": "I had trouble processing that request. Please try rephrasing."}
    
    return {"status": "error", "response": "I was unable to update the timesheet with that information. Please try again."}


# --- You also need to slightly modify the helper function to support this ---
def _update_draft_hours(day, hours, activity='Misc', clear_day=True):
    """
    Safely sets hours, with an option to prevent clearing the day's data.
    """
    global _TIMESHEET_DRAFT
    day_capitalized = day.capitalize()

    if _TIMESHEET_DRAFT and day_capitalized in _TIMESHEET_DRAFT:
        if clear_day:
            _TIMESHEET_DRAFT[day_capitalized]['data'] = {'Meetings': 0, 'Misc': 0}
        
        current_hours = _TIMESHEET_DRAFT[day_capitalized]['data'].get(activity.upper(), 0)
        
        if activity.upper() == 'PTO':
            _TIMESHEET_DRAFT[day_capitalized]['data']['PTO'] = current_hours + float(hours)
        else:
            # For simplicity, lump all non-PTO work into Misc
            _TIMESHEET_DRAFT[day_capitalized]['data']['Misc'] += float(hours)
        
        return True
    return False
# FAQs from Salesforce
# -----------------------------
def get_faqs_from_salesforce():
    """Queries Salesforce for a list of Knowledge Articles and returns FAQs."""
    sf = connect_to_salesforce()
    if not sf:
        return []
    
    try:
        # Corrected SOQL query with the correct API name for the Title field
        faqs_result = sf.query("SELECT Id, Title, KnowledgeArticleId FROM Knowledge__kav WHERE PublishStatus = 'Online' LIMIT 5")
        faqs = []
        for record in faqs_result.get('records', []):
            faqs.append({
                "question": record['Title'],
                "link": f"https://orgfarm-2bc7acb5c3-dev-ed.develop.lightning.force.com/lightning/r/Knowledge__kav/{record['KnowledgeArticleId']}/view"
            })
        return faqs
    except Exception as e:
        print(f"Error fetching FAQs from Salesforce: {e}")
        return []


  # In generate_timesheet.py, add this new function at the bottom
def delete_timesheet_records(record_ids):
    """Deletes timesheet records from Salesforce."""
    sf = connect_to_salesforce()
    if not sf:
        return {'status': 'error', 'message': 'Salesforce connection failed.'}
    
    try:
        # Simple-salesforce bulk delete method
        results = sf.bulk.Timesheet__c.delete(record_ids)
        
        # Log the deletion results
        print(f"DEBUG: Deletion results: {results}")

        return {'status': 'success', 'message': 'Records deleted successfully.'}
    except Exception as e:
        return {'status': 'error', 'message': f"Error deleting records: {e}"}

# In generate_timesheet.py

def generate_productivity_insights(timesheet_data):
    """
    Analyzes a completed timesheet and returns a productivity insight using GenAI.
    """
    try:
        # Convert the timesheet data to a simple string for the prompt
        summary_lines = []
        total_hours = 0
        meeting_hours = 0
        for day, data in timesheet_data.items():
            day_total = sum(data['data'].values())
            total_hours += day_total
            meeting_hours += data['data'].get('Meetings', 0)
            summary_lines.append(f"- {day}: {day_total} hours")

        timesheet_summary = "\n".join(summary_lines)

        # This is the magic: a prompt that asks the AI to be a coach
        prompt = f"""
        You are a friendly and encouraging productivity coach.
        Analyze the following weekly timesheet summary for an employee.
        The total hours worked were {total_hours}.
        The total hours in meetings were {meeting_hours}.
        The daily breakdown is:
        {timesheet_summary}

        Based on this data, provide ONE concise, positive, and actionable insight for the user.
        Frame it as a helpful observation. If meeting hours are high, suggest focus time.
        If total hours are high, encourage rest. If meeting hours are low, praise their focus.
        Start the response with a phrase like "Here's a quick insight on your week:".
        Keep the entire response to under 40 words.
        """

        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        
        return {"status": "success", "insight": response.text}

    except Exception as e:
        print(f"Could not generate insights: {e}")
        # Don't return an error, just return an empty success so it doesn't break the flow
        return {"status": "success", "insight": ""}
# -----------------------------
# Main Entry
# -----------------------------
if __name__ == '__main__':
    draft = generate_timesheet_draft()
    print("Draft generated:", draft)
































