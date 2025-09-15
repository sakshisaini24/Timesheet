import datetime
import generate_timesheet  # This imports your script from Day 1

def run_chatbot():
    """Simulates the Agentforce chatbot interaction."""
    
    # Run the core logic from Day 1 to get the timesheet draft
    print("Timesheet Bot is preparing your draft...")
    service = generate_timesheet.get_calendar_service()
    draft = generate_timesheet.generate_timesheet_draft(service)
    
    if not draft:
        print("No timesheet draft could be generated. Please check your calendar and try again.")
        return

    print("\n--- Timesheet Draft Overview ---")
    for day, data in draft.items():
        if 'PTO' in data:
            print(f"** {day}: Marked as PTO (Paid Time Off) **")
        else:
            print(f"** {day}: {data['Meetings']} hours from meetings, {data['Misc']} hours marked as Misc. **")
    
    print("\n------------------------------")
    
    # Start the interactive confirmation flow
    print("Welcome! I have prepared a draft of your timesheet for this week.")
    print("Let's go through it to confirm everything is correct.")

    submitted_days = []
    
    # Loop through each day to get confirmation
    for day, data in draft.items():
        if 'PTO' in data:
            prompt = f"On {day}, my draft suggests you were Out of Office, so I have marked it as PTO. Is this correct? (yes/no)"
        else:
            prompt = f"For {day}, I have filled {data['Meetings']} hours from your meetings and the remaining {data['Misc']} hours as 'Misc'. Do you want to submit this? (yes/no)"

        response = input(f"\n{prompt}\nYour answer: ").lower().strip()
        
        if response == 'yes':
            submitted_days.append(day)
            print(f"Great! {day} confirmed.")
        else:
            print(f"No problem. I will leave {day} for you to fill out manually in Salesforce.")
            
    # Final Submission Message
    print("\n--- Finalizing Submission ---")
    if submitted_days:
        print("Based on our conversation, the following days have been confirmed and submitted to Salesforce:")
        for day in submitted_days:
            print(f"- {day}")
        print("\nYour timesheet has been finalized! Have a great weekend!")
    else:
        print("No days were confirmed. Your timesheet remains empty. You can fill it manually.")

if __name__ == '__main__':
    run_chatbot()