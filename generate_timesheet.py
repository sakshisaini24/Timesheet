import datetime
import os
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
from fpdf import FPDF
from simple_salesforce import Salesforce
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment
from sf_connect import connect_to_salesforce

# ==============================================================================
# --- CONSTANTS AND CONFIGURATION ---
# ==============================================================================

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# API Keys from Environment Variables
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

GEN_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEN_API_KEY:
    genai.configure(api_key=GEN_API_KEY)

# Hardcoded Salesforce Activity ID for the prototype
ACTIVITY_ID = 'a01gK00000Jw4wMQAR'  # IMPORTANT: REPLACE WITH A REAL ID FROM YOUR ORG

# Global variables for state management
_LAST_PDF_PATH = None
_TIMESHEET_DRAFT = None

PICKLIST_MAPPING = {
    'PTO': 'PTO',
    'Meetings': 'Business Day - Morning Shift - Standard Time',
    'Misc': 'Business Day - Morning Shift - Standard Time',
    'Project Work': 'Business Day - Morning Shift - Standard Time'
}

# ==============================================================================
# --- PDF GENERATION & EMAIL ---
# ==============================================================================

class PDF(FPDF):
    """Custom PDF class to handle headers and footers."""
    def header(self):
        if os.path.exists('logo.png'):
            self.image('logo.png', 10, 8, 25)
        self.set_font('Inter', 'B', 20)
        self.cell(0, 10, 'Weekly Timesheet Summary', 0, 1, 'C')
        self.ln(15)

    def footer(self):
        self.set_y(-15)
        self.set_font('Inter', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

# In generate_timesheet.py, replace the PDF creation function

def create_timesheet_pdf(submitted_data):
    """Generates a professional, branded, and SORTED PDF."""
    pdf = PDF('P', 'mm', 'A4')
    
    try:
        pdf.add_font('Inter', '', 'Inter-Regular.ttf', uni=True)
        pdf.add_font('Inter', 'B', 'Inter-Bold.ttf', uni=True)
        pdf.add_font('Inter', 'I', 'Inter-Italic.ttf', uni=True)
    except RuntimeError:
        print("Font files not found, using Arial.")
        
    pdf.set_font('Inter', '', 11)
    pdf.add_page()
    
    pdf.set_font('Inter', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(30, 10, 'Day', 1, 0, 'C', 1)
    pdf.cell(70, 10, 'Details', 1, 0, 'C', 1)
    pdf.cell(25, 10, 'Hours', 1, 0, 'C', 1)
    pdf.cell(65, 10, 'Productivity Insight', 1, 1, 'C', 1)

    total_productive_hours = 0
    
    # --- THIS IS THE FIX for SORTING ---
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    # Filter out days not in the draft and sort them
    sorted_days = [day for day in day_order if day in submitted_data]

    for day in sorted_days:
        hours_data = submitted_data[day]
        # (The rest of the PDF generation logic for the loop is the same as before)
        pdf.set_font('Inter', '', 10)
        pto_hours = hours_data['data'].get('PTO', 0)
        daily_total = sum(hours_data['data'].values())
        worked_hours = daily_total - pto_hours

        if worked_hours > 0:
            total_productive_hours += worked_hours

        details_str = ""
        for activity, hours in hours_data['data'].items():
            if hours > 0:
                details_str += f"- {activity}: {hours} hrs\n"
        details_str = details_str.strip()
        
        num_lines = details_str.count('\n') + 1
        cell_height = 8
        row_height = max(num_lines * cell_height, 16)

        y_before_row = pdf.get_y()
        # ... (The rest of the multi_cell drawing logic is the same)
        pdf.multi_cell(30, row_height, day, 1, 'C', 0)
        
        pdf.set_y(y_before_row)
        pdf.set_x(40)
        pdf.multi_cell(70, cell_height, details_str, 1, 'L', 0)

        pdf.set_y(y_before_row)
        pdf.set_x(110)
        pdf.multi_cell(25, row_height, str(daily_total), 1, 'C', 0)

        daily_productivity_message = ""
        color = (0, 0, 0)
        
        if pto_hours >= 8:
            daily_productivity_message = "On Leave"
            color = (128, 128, 128)
        else: # (Productivity logic is the same)
            if worked_hours >= 10:
                daily_productivity_message = "Excellent! Remember to get some rest."
                color = (220, 53, 69)
            elif worked_hours >= 8:
                daily_productivity_message = "Excellent! Keep it up."
                color = (40, 167, 69)
            elif worked_hours >= 4:
                daily_productivity_message = "Good, productive day."
                color = (0, 123, 255)
            elif worked_hours > 0:
                daily_productivity_message = "Room for improvement."
                color = (255, 193, 7)
            
            if pto_hours > 0:
                daily_productivity_message += f" ({pto_hours}h PTO)"

        pdf.set_text_color(*color)
        pdf.set_y(y_before_row)
        pdf.set_x(135)
        pdf.multi_cell(65, row_height, daily_productivity_message, 1, 'C', 0)

        pdf.set_text_color(0, 0, 0)
        pdf.set_y(y_before_row + row_height)


    pdf.set_font('Inter', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(100, 12, 'Total Productive Hours', 1, 0, 'R', 1)
    pdf.cell(90, 12, f'{total_productive_hours} hours', 1, 1, 'C', 1)

    pdf_path = f"timesheet_summary_{datetime.date.today().isoformat()}.pdf"
    pdf.output(pdf_path)
    global _LAST_PDF_PATH
    _LAST_PDF_PATH = pdf_path
    return pdf_path


# ==============================================================================
# --- CORE LOGIC: GOOGLE & SALESFORCE INTEGRATION ---
# ==============================================================================

def get_calendar_service():
    """Returns an authorized Google Calendar service instance."""
    # This function requires your actual credentials logic.
    # It's a placeholder based on your original file.
    credentials_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    token_json_str = os.environ.get('GOOGLE_TOKEN_JSON')

    if not credentials_json or not token_json_str:
        print("WARNING: Missing Google credentials. The app will use mock data.")
        return None

    token_data = json.loads(token_json_str)
    creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    if not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            print(f"Failed to refresh Google token: {e}")
            return None
            
    return build('calendar', 'v3', credentials=creds)

def generate_timesheet_draft():
    """Generates a draft timesheet, differentiating between Meetings and Tasks."""
    global _TIMESHEET_DRAFT
    _TIMESHEET_DRAFT = None # Reset draft for fresh generation

    service = get_calendar_service()
    if service is None:
        # For testing without credentials, return a mock draft
        mock_date = datetime.date.today()
        return {
            'Monday': {'date': str(mock_date), 'data': {'Meetings': 2.5, 'Project Work': 4, 'Misc': 1.5}},
            'Tuesday': {'date': str(mock_date), 'data': {'Meetings': 0, 'Project Work': 0, 'Misc': 0, 'PTO': 8}}
        }

    try:
        today = datetime.date.today()
        start_of_week = today - datetime.timedelta(days=today.weekday())
        end_of_week = start_of_week + datetime.timedelta(days=4)
        timeMin = datetime.datetime.combine(start_of_week, datetime.time.min).isoformat() + 'Z'
        timeMax = datetime.datetime.combine(end_of_week, datetime.time.max).isoformat() + 'Z'

        events_result = service.events().list(
            calendarId='primary', timeMin=timeMin, timeMax=timeMax,
            singleEvents=True, orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
    except Exception as e:
        print(f"Error fetching calendar events: {e}")
        return {'status': 'error', 'message': str(e)}

    timesheet = {}
    for i in range(5):
        day = start_of_week + datetime.timedelta(days=i)
        timesheet[day.strftime('%A')] = {
            'date': day.isoformat(),
            'data': {'Meetings': 0, 'Project Work': 0, 'Misc': 0} 
        }

    for event in events:
        summary = event.get('summary', '').upper()
        if 'OOO' in summary or 'OUT OF OFFICE' in summary:
            start_date_str = event['start'].get('date')
            if start_date_str:
                start_date = datetime.date.fromisoformat(start_date_str)
                timesheet[start_date.strftime('%A')]['data'] = {'PTO': 8}
            continue

        if 'dateTime' in event['start'] and 'dateTime' in event['end']:
            start_dt = datetime.datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
            end_dt = datetime.datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
            duration_hours = round((end_dt - start_dt).total_seconds() / 3600, 2)
            day_of_week = start_dt.strftime('%A')

            if 'PTO' in timesheet[day_of_week]['data']:
                continue
            if '[TASK]' in summary:
                timesheet[day_of_week]['data']['Project Work'] += duration_hours
            else:
                timesheet[day_of_week]['data']['Meetings'] += duration_hours

    for day, data in timesheet.items():
        if 'PTO' not in data['data']:
            logged_hours = data['data'].get('Meetings', 0) + data['data'].get('Project Work', 0)
            misc_hours = 8 - logged_hours
            data['data']['Misc'] = round(misc_hours, 2) if misc_hours > 0 else 0

    _TIMESHEET_DRAFT = timesheet
    return _TIMESHEET_DRAFT


def submit_to_salesforce(submitted_data):
    """Submits timesheet data efficiently using the correct bulk insert method."""
    sf = connect_to_salesforce()
    if not sf:
        return {'status': 'error', 'message': 'Salesforce connection failed.'}

    try:
        user_info = sf.query("SELECT Id, ManagerId FROM User WHERE Username = 'sakshi.saini427@agentforce.com'")
        if not user_info or not user_info.get('records'):
            return {'status': 'error', 'message': 'User not found in Salesforce. Check the username.'}
        manager_id = user_info['records'][0]['ManagerId']
        if not manager_id:
            return {'status': 'error', 'message': 'User found, but they do not have a manager assigned.'}
    except Exception as e:
        return {'status': 'error', 'message': f"Error finding manager: {e}"}

    records_to_create = []
    for day, hours_data in submitted_data.items():
        pto_hours = hours_data['data'].get('PTO', 0)
        if pto_hours > 0:
            records_to_create.append({
                'Activity__c': ACTIVITY_ID, 'Date__c': hours_data['date'], 'Status__c': 'Submitted',
                'Time_Type__c': 'PTO', 'Hours__c': pto_hours
            })
        worked_hours = sum(v for k, v in hours_data['data'].items() if k != 'PTO')
        if worked_hours > 0:
            records_to_create.append({
                'Activity__c': ACTIVITY_ID, 'Date__c': hours_data['date'], 'Status__c': 'Submitted',
                'Time_Type__c': PICKLIST_MAPPING.get('Misc'), 'Hours__c': worked_hours
            })

    if not records_to_create:
        create_timesheet_pdf(submitted_data)
        return {'status': 'success', 'results': {'message': 'No hours to submit.', 'ids': []}}

    # This is the corrected line
    created_records = sf.bulk.Timesheet__c.insert(records_to_create)
    
    created_ids = [record['id'] for record in created_records if record.get('success')]
    errors = [record['errors'] for record in created_records if not record.get('success')]

    if errors:
        return {'status': 'error', 'message': f"Failed to create some records: {errors}"}
        
    try:
        if created_ids:
            approval_requests = []
            for record_id in created_ids:
                approval_requests.append({
                    "contextId": record_id, "nextApproverIds": [manager_id],
                    "comments": "Timesheet submitted automatically via Agentforce.", "actionType": "Submit"
                })
            data_payload = {"requests": approval_requests}
            sf.restful('process/approvals/', method='POST', data=json.dumps(data_payload))
    except Exception as e:
        return {'status': 'error', 'message': f"Failed to submit for approval: {e}"}

    create_timesheet_pdf(submitted_data)
    
    return {'status': 'success', 'results': {'message': 'Timesheet submitted for approval.', 'ids': created_ids}}
# ==============================================================================
# --- AI & CHATBOT LOGIC ---
# ==============================================================================

# In generate_timesheet.py, replace this helper function

def _update_draft_hours(day, hours, activity='Misc', clear_day=True):
    """
    Safely sets hours, correctly assigning to PTO, Meetings, Project Work, or Misc.
    """
    global _TIMESHEET_DRAFT
    day_capitalized = day.capitalize()
    if _TIMESHEET_DRAFT and day_capitalized in _TIMESHEET_DRAFT:
        if clear_day:
            # Reset the day's data completely
            _TIMESHEET_DRAFT[day_capitalized]['data'] = {'Meetings': 0, 'Misc': 0, 'Project Work': 0}
        
        activity_upper = activity.upper()
        # Use .get() to avoid KeyError if a category doesn't exist yet
        
        # --- THIS IS THE FIX ---
        if 'PTO' in activity_upper:
            current_hours = _TIMESHEET_DRAFT[day_capitalized]['data'].get('PTO', 0)
            _TIMESHEET_DRAFT[day_capitalized]['data']['PTO'] = current_hours + float(hours)
        elif 'MEETING' in activity_upper:
            current_hours = _TIMESHEET_DRAFT[day_capitalized]['data'].get('Meetings', 0)
            _TIMESHEET_DRAFT[day_capitalized]['data']['Meetings'] = current_hours + float(hours)
        elif 'PROJECT' in activity_upper:
            current_hours = _TIMESHEET_DRAFT[day_capitalized]['data'].get('Project Work', 0)
            _TIMESHEET_DRAFT[day_capitalized]['data']['Project Work'] = current_hours + float(hours)
        else: # Default to Misc
            current_hours = _TIMESHEET_DRAFT[day_capitalized]['data'].get('Misc', 0)
            _TIMESHEET_DRAFT[day_capitalized]['data']['Misc'] = current_hours + float(hours)
            
        return True
    return False

def process_chat_command(user_message):
    """Processes advanced user commands, including multiple actions in a single sentence."""
    global _TIMESHEET_DRAFT
    message_lower = user_message.lower()

    if 'submit' in message_lower or 'looks good' in message_lower or 'correct' in message_lower:
        return {'status': 'submitting', 'response': 'Great! Finalizing and submitting your timesheet now...🎉', 'draft': _TIMESHEET_DRAFT}

    try:
        prompt = f"""
        You are a powerful timesheet parsing engine. Analyze the user's request: '{user_message}'.
        Your task is to extract ALL actions the user wants to take.
        For each action, extract the day, hours, and activity (e.g., 'PTO', 'Misc').
        If 'PTO' is mentioned without hours, assume 8.
        Respond ONLY with a JSON object with one key, "actions", which is a list of action objects.
        Example for "Change Monday to 4 hours PTO, 2 hours misc and 2 hours meeting":
        {{"actions": [{{"day": "Monday", "hours": 4, "activity": "PTO"}}, {{"day": "Monday", "hours": 2, "activity": "Misc"}}, {{"day": "Monday", "hours":2, "activity":"Meetings"]}}
        If no valid actions, respond with {{"actions": []}}.
        """
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        json_response_text = response.text.strip().replace('`', '').replace('json', '')
        parsed_data = json.loads(json_response_text)
        actions = parsed_data.get('actions', [])

        if not actions:
            return {"status": "error", "response": "I'm sorry, Can you please specify your tasks?"}

        confirmation_messages = []
        day_to_update = actions[0].get('day')
        if day_to_update:
            _update_draft_hours(day_to_update, 0, activity='Misc', clear_day=True)

        for action in actions:
            day = action.get('day')
            hours = action.get('hours')
            activity = action.get('activity', 'Misc')
            if day and hours is not None:
                if _update_draft_hours(day, hours, activity, clear_day=False):
                    confirmation_messages.append(f"{hours} hours for {activity}")
        
        if confirmation_messages:
            full_confirmation = f"OK. I've updated {day_to_update} with " + " and ".join(confirmation_messages) + "."
            return {"status": "success", "response": full_confirmation, "draft": _TIMESHEET_DRAFT}

    except Exception as e:
        print(f"AI parsing or draft update failed: {e}")
        return {"status": "error", "response": "I had trouble processing that request. Please try rephrasing."}
    
    return {"status": "error", "response": "I was unable to update the timesheet with that information. Please try again."}

def generate_productivity_insights(timesheet_data):
    """Analyzes a completed timesheet and returns a productivity insight."""
    try:
        summary_lines, total_hours, meeting_hours = [], 0, 0
        for day, data in timesheet_data.items():
            day_total = sum(data['data'].values())
            total_hours += day_total
            meeting_hours += data['data'].get('Meetings', 0)
            summary_lines.append(f"- {day}: {day_total} hours")
        timesheet_summary = "\n".join(summary_lines)

        prompt = f"""
        You are a friendly and encouraging productivity coach.
        Analyze the following weekly timesheet summary for an employee.
        The total hours worked were {total_hours}.
        The total hours in meetings were {meeting_hours}.
        The daily breakdown is:\n{timesheet_summary}
        Based on this data, provide ONE concise, positive, and actionable insight for the user.
        Frame it as a helpful observation. Keep the entire response to under 40 words.
        """
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        return {"status": "success", "insight": response.text}
    except Exception as e:
        print(f"Could not generate insights: {e}")
        return {"status": "success", "insight": ""}

# ==============================================================================
# --- UTILITY FUNCTIONS ---
# ==============================================================================

def get_faqs_from_salesforce():
    """Queries Salesforce for a list of Knowledge Articles and returns FAQs."""
    sf = connect_to_salesforce()
    if not sf:
        return []
    try:
        faqs_result = sf.query("SELECT Id, Title, KnowledgeArticleId FROM Knowledge__kav WHERE PublishStatus = 'Online' LIMIT 5")
        faqs = [{'question': r['Title'], 'link': f"/lightning/r/Knowledge__kav/{r['KnowledgeArticleId']}/view"} for r in faqs_result.get('records', [])]
        return faqs
    except Exception as e:
        print(f"Error fetching FAQs from Salesforce: {e}")
        return []

def delete_timesheet_records(record_ids):
    """Deletes timesheet records from Salesforce."""
    sf = connect_to_salesforce()
    if not sf:
        return {'status': 'error', 'message': 'Salesforce connection failed.'}
    try:
        results = sf.bulk.Timesheet__c.delete(record_ids)
        print(f"DEBUG: Deletion results: {results}")
        return {'status': 'success', 'message': 'Records deleted successfully.'}
    except Exception as e:
        return {'status': 'error', 'message': f"Error deleting records: {e}"}


# In generate_timesheet.py

def get_team_timesheet_data(manager_id):
    """
    Queries Salesforce and aggregates team data, including the individual timesheet IDs for each user.
    """
    sf = connect_to_salesforce()
    
    manager_id_for_query = '005gK000007m2xxQAA' 

    soql_query = f"""
        SELECT Id, Owner.Name, Hours__c, Time_Type__c
        FROM Timesheet__c 
        WHERE Date__c = THIS_WEEK AND Status__c = 'Submitted'
        AND OwnerId IN (SELECT Id FROM User WHERE ManagerId = '{manager_id_for_query}')
    """
    try:
        results = sf.query(soql_query)
        team_data_with_ids = {}
        for record in results['records']:
            name = record['Owner']['Name']
            if name not in team_data_with_ids:

                team_data_with_ids[name] = {'Work': 0, 'PTO': 0, 'ids': []}
            
            hours = record['Hours__c']
            
            team_data_with_ids[name]['ids'].append(record['Id'])

            if record['Time_Type__c'] == 'PTO':
                team_data_with_ids[name]['PTO'] += hours
            else:
                team_data_with_ids[name]['Work'] += hours
        return team_data_with_ids
    except Exception as e:
        print(f"Error fetching team data: {e}")
        return {}


def generate_team_summary_insight(team_data):
    """Uses GenAI to analyze aggregated team data and create a summary for a manager."""
    data_summary = json.dumps(team_data, indent=2)
    prompt = f"""
    You are an expert business analyst. Analyze the following JSON data of a team's weekly timesheets.
    Data: {data_summary}
    Provide a concise, bullet-pointed summary for a busy manager. Identify:
    1. An overall summary of the team's focus.
    2. Any individuals at risk of burnout (over 45 hours).
    3. Any individuals with unusually low hours.
    4. A general trend or suggestion for the team.
    Be direct and frame your points as helpful observations.
    """
    try:
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        return {"status": "success", "summary": response.text}
    
    except genai.types.generation_types.StopCandidateException as e:
        print(f"AI response blocked: {e}")
        return {"status": "error", "message": "The generated response was blocked for safety reasons. Please try a different query."}
    except Exception as e:
        print(f"Error generating team summary: {e}")
        return {"status": "error", "message": "An error occurred while generating the AI summary. The AI service may be busy."}


def approve_timesheets(timesheet_ids):
    """Updates the status of given Timesheet records to 'Approved' in Salesforce."""
    sf = connect_to_salesforce()
    if not sf or not timesheet_ids:
        return False
    
    updates = [{'id': ts_id, 'Status__c': 'Approved'} for ts_id in timesheet_ids]
    try:
        results = sf.bulk.Timesheet__c.update(updates)
        print(f"Approval Results: {results}")
        return all(r.get('success', False) for r in results)
    except Exception as e:
        print(f"Error approving timesheets: {e}")
        return False

def reject_timesheets(timesheet_ids, reason, rejected_by_name):
    """Updates the status to 'Rejected' and posts a Chatter notification."""
    sf = connect_to_salesforce()
    if not sf or not timesheet_ids:
        return False

    updates = [{'id': ts_id, 'Status__c': 'Rejected'} for ts_id in timesheet_ids]
    try:
        # Update records to Rejected
        sf.bulk.Timesheet__c.update(updates)
        
        # Post a single Chatter notification for the first rejected timesheet's owner
        # In a real app, you might post to each owner. For a demo, one is sufficient.
        ts_to_notify = sf.Timesheet__c.get(timesheet_ids[0])
        owner_id = ts_to_notify.get('OwnerId')
        
        chatter_post_url = f"/services/data/v59.0/chatter/feed-elements"
        chatter_body = {
            "body": {
                "messageSegments": [
                    {"type": "Text", "text": f"Hi @[{owner_id}], your timesheet was rejected by {rejected_by_name}. Reason: "},
                    {"type": "Text", "text": reason}
                ]
            },
            "feedElementType": "FeedItem",
            "subjectId": owner_id # Post to the user's profile feed
        }
        sf.restful(chatter_post_url, method='POST', data=json.dumps(chatter_body))
        return True
    except Exception as e:
        print(f"Error rejecting timesheets: {e}")
        return False


    # In generate_timesheet.py

def get_users_with_missing_timesheets(manager_id):
    """
    Compares all users under a manager with those who have submitted timesheets
    and returns a list of users who have not submitted.
    """
    sf = connect_to_salesforce()
    try:
        # 1. Get all active users who report to this manager
        all_team_members_query = f"SELECT Name FROM User WHERE ManagerId = '{manager_id}' AND IsActive = true"
        all_team_results = sf.query(all_team_members_query)
        all_team_names = {user['Name'] for user in all_team_results['records']}

        # 2. Get the names of users from that team who HAVE submitted this week
        # --- THIS IS THE CORRECTED QUERY ---
        submitters_query = f"""
            SELECT Owner.Name 
            FROM Timesheet__c 
            WHERE Date__c = THIS_WEEK AND Status__c = 'Submitted'
            AND OwnerId IN (SELECT Id FROM User WHERE ManagerId = '{manager_id}')
            GROUP BY Owner.Name
        """
        submitter_results = sf.query(submitters_query)
        submitter_names = {record['Owner']['Name'] for record in submitter_results['records']}

        # 3. Find the difference
        missing_names = list(all_team_names - submitter_names)
        return missing_names

    except Exception as e:
        print(f"Error finding users with missing timesheets: {e}")
        return []
# ==============================================================================
# --- MAIN EXECUTION BLOCK (FOR TESTING) ---
# ==============================================================================
if __name__ == '__main__':
    print("Generating initial timesheet draft...")
    draft = generate_timesheet_draft()
    print("Draft generated:", json.dumps(draft, indent=2))















