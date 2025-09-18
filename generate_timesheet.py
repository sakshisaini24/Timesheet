import datetime
import os.path
import json
import re
import base64
import mimetypes

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


# -----------------------------
# PDF Generation
# -----------------------------
def create_timesheet_pdf(submitted_data):
    """Generates a PDF of the timesheet and returns the file path."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    # Header
    pdf.cell(200, 10, txt="Timesheet Summary for the Week", ln=1, align="C")
    pdf.ln(5)

    # Timesheet data
    total_hours = 0
    for day, hours_data in submitted_data.items():
        daily_hours = sum(hours_data['data'].values())
        total_hours += daily_hours
        pdf.cell(200, 10, txt=f"{day} - {daily_hours} hours", ln=1)
        for activity, hours in hours_data['data'].items():
            pdf.cell(200, 10, txt=f"  - {activity}: {hours} hours", ln=1)

    # Productivity meter
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 16)
    if total_hours >= 40:
        productivity_message = "Weekly Productivity: Excellent!"
    elif total_hours >= 32:
        productivity_message = "Weekly Productivity: Good!"
    else:
        productivity_message = "Weekly Productivity: Can do better!."
    pdf.cell(200, 10, txt=f"{productivity_message} ({total_hours} hours)", ln=1, align="C")

    # Save the PDF
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
    creds = None
    credentials_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    token_json = os.environ.get('GOOGLE_TOKEN_JSON')

    if token_json:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    else:
        print("Error: Google Calendar token not found in environment variables.")
        return None

    if not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print("Error: Invalid or expired Google Calendar token. Cannot re-authenticate on Render.")
            return None

    return build('calendar', 'v3', credentials=creds)


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
        for activity, hours in hours_data['data'].items():
            total_hours = sum(hours_data['data'].values())
            if total_hours > 0:
                record = {
                    'Activity__c': ACTIVITY_ID,
                    'Date__c': hours_data['date'],
                    'Status__c': 'Submitted',
                    'Time_Type__c': PICKLIST_MAPPING.get(activity, 'Uncategorized'),
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


def generate_bot_response(user_message):
    """Generates a natural language response for timesheet queries."""
    global _TIMESHEET_DRAFT
    lower_message = user_message.lower()

    if "hours" in lower_message or "time on" in lower_message:
        draft = _TIMESHEET_DRAFT
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
            if day in lower_message:
                day_data = draft.get(day.capitalize(), {}).get('data', {})
                if 'PTO' in day_data:
                    return f"You were marked as PTO on {day.capitalize()} for 8 hours."
                else:
                    return f"On {day.capitalize()}, you had {day_data.get('Meetings', 0)} hours of meetings and {day_data.get('Misc', 0)} hours marked as miscellaneous."
        return "I can't find that specific day. Please ask about a day of the week."

    elif "draft" in lower_message or "summary" in lower_message:
        draft = _TIMESHEET_DRAFT
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

    elif ("change" in lower_message or "set" in lower_message) and ("hours" in lower_message or "time" in lower_message):
        numbers = re.findall(r'\b\d+\b', lower_message)
        if not numbers:
            # If no digit found, check for number words
            for num_word, digit in NUM_DICT.items():
                if num_word in lower_message:
                    hours = float(digit)
                    break
            else:
                hours = None
        else:
            hours = float(numbers[0])

        for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
            if day in lower_message:
                if hours is not None:
                    if update_timesheet_draft(day.capitalize(), hours):
                        return f"Okay, I have set {hours} hours for {day.capitalize()}. Let me know if you need to make any more changes."
                    else:
                        return "I could not update the timesheet. Please try again."

        return "I couldn't understand that. Please specify the day and the number of hours."

    return "I can help with questions about your timesheet. Try asking me about your hours on a specific day."


def update_draft_from_chat(message):
    """Updates draft hours from chatbot input."""
    lower_message = message.lower()

    if ("change" in lower_message or "set" in lower_message) and ("hours" in lower_message or "time" in lower_message):
        numbers = re.findall(r'\b\d+\b', lower_message)
        if not numbers:
            # If no digit found, check for number words
            for num_word, digit in NUM_DICT.items():
                if num_word in lower_message:
                    hours = float(digit)
                    break
            else:
                hours = None
        else:
            hours = float(numbers[0])

        for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
            if day in lower_message:
                if hours is not None:
                    if update_timesheet_draft(day.capitalize(), hours):
                        return {
                            'status': 'success',
                            'response': f"Okay, I have set {hours} hours for {day.capitalize()}.",
                            'draft': _TIMESHEET_DRAFT
                        }
                    else:
                        return {'status': 'error', 'response': "I could not update the timesheet. Please try again."}

    return {'status': 'error', 'response': "I can only update hours for a specific day."}


# -----------------------------
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


# -----------------------------
# Main Entry
# -----------------------------
if __name__ == '__main__':
    draft = generate_timesheet_draft()
    print("Draft generated:", draft)





