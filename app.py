import os
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import generate_timesheet

app = Flask(__name__)
CORS(app, resources={r"/*": {
    "origins": [
        "https://orgfarm-2bc7acb5c3-dev-ed.develop.vf.force.com",
        "https://orgfarm-2bc7acb5c3-dev-ed--c.develop.vf.force.com"
    ],
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization"]
}})

@app.route('/generate_draft')
def generate_draft():
    try:
        draft = generate_timesheet.generate_timesheet_draft()
        return jsonify(draft)
    except Exception as e:
        print(f"Error generating draft: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/submit_timesheet', methods=['POST'])
def submit_timesheet():
    try:
        data = request.json
        if not data:
            return jsonify({'status': 'error', 'message': 'No data received'}), 400
            
        result = generate_timesheet.submit_to_salesforce(data)
        
        print("Salesforce Submission Result:", result)
        
        return jsonify(result)
    except Exception as e:
        print(f"Error submitting timesheet: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    
@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    message = data.get('message', '')
    
    bot_response = generate_timesheet.generate_bot_response(message)
    
    return jsonify({'response': bot_response})


@app.route('/update_draft_from_chat', methods=['POST'])
def update_draft():
    data = request.json
    message = data.get('message', '')

    response = generate_timesheet.process_chat_command(message) 
    
    return jsonify(response)

@app.route('/download_pdf')
def download_pdf():
    try:
        
        pdf_path = generate_timesheet._LAST_PDF_PATH
        if not pdf_path or not os.path.exists(pdf_path):
            return jsonify({"status": "error", "message": "PDF not found."}), 404
        
        return send_file(pdf_path, as_attachment=True, download_name=os.path.basename(pdf_path))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/faqs')
def get_faqs():
    faqs = generate_timesheet.get_faqs_from_salesforce()
    return jsonify({'faqs': faqs})


@app.route('/recall_to_draft', methods=['POST'])
def recall_to_draft():
    data = request.json
    record_ids = data.get('ids', [])
    
    if not record_ids:
        return jsonify({'status': 'error', 'message': 'No record IDs provided.'}), 400

    result = generate_timesheet.delete_timesheet_records(record_ids)
    
    return jsonify(result)


@app.route('/get_insight', methods=['POST'])
def get_insight():
    # The UI will send the final confirmed draft in the request body
    final_draft = request.json 
    if not final_draft:
        return jsonify({'status': 'error', 'message': 'No data received'}), 400
    
    insight_result = generate_timesheet.generate_productivity_insights(final_draft)
    return jsonify(insight_result)


@app.route('/team_summary/<manager_id>')
def team_summary(manager_id):
    team_data_with_ids = generate_timesheet.get_team_timesheet_data(manager_id)
    
    if not team_data_with_ids:
        missing_users = generate_timesheet.get_users_with_missing_timesheets(manager_id)
        return jsonify({
            "status": "pending", # A new status for this state
            "message": "There are no timesheets awaiting your approval.",
            "missingUsers": missing_users
        })

    labels = list(team_data_with_ids.keys())
    chart_data = { 'labels': labels, 'datasets': [
            {'label': 'Work Hours', 'data': [d['Work'] for d in team_data_with_ids.values()], 'backgroundColor': 'rgba(0, 123, 255, 0.7)'},
            {'label': 'PTO Hours', 'data': [d['PTO'] for d in team_data_with_ids.values()], 'backgroundColor': 'rgba(108, 117, 125, 0.7)'}
    ]}
    ai_summary = generate_timesheet.generate_team_summary_insight(team_data_with_ids)
    
    return jsonify({
        "status": "success",
        "chartData": chart_data,
        "aiSummary": ai_summary.get('summary', ''),
        "teamDataWithIds": team_data_with_ids,
        "missingUsers": [] # Send empty list on success
    })

@app.route('/approve_timesheets', methods=['POST'])
def approve_timesheets_endpoint():
    data = request.json
    timesheet_ids = data.get('ids', [])
    print(f"DEBUG: Received IDs for approval: {timesheet_ids}")
    
    success = generate_timesheet.approve_timesheets(timesheet_ids)
    if success:
        return jsonify({"status": "success", "message": "Timesheets approved."})
    return jsonify({"status": "error", "message": "Failed to approve timesheets."}), 500


def reject_timesheets_endpoint():
    data = request.json
    timesheet_ids = data.get('ids', [])
    reason = data.get('reason', 'No reason provided.')
    manager_name = data.get('manager_name', 'Manager')
    success = generate_timesheet.reject_timesheets(timesheet_ids, reason, manager_name)
    if success:
        return jsonify({"status": "success", "message": "Timesheets rejected and user notified."})
    return jsonify({"status": "error", "message": "Failed to reject timesheets."}), 500
if __name__ == '__main__':
    app.run(debug=True, port=5000)















