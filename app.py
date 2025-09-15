from flask import Flask, jsonify, request
from flask_cors import CORS
import generate_timesheet

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

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

@app.route('/update_draft', methods=['POST'])
def update_draft():
    data = request.json
    message = data.get('message', '')

    response = generate_timesheet.update_draft_from_chat(message)
    
    return jsonify(response)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
