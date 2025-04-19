import openai
import json
import time
from datetime import datetime
from tts_config import AzureSpeechService

# Import the assessment function schema from gpt_json.py
from gpt_json import assessment_function

# Set your API key
client = openai.OpenAI(api_key="")

# System prompt for symptom assessment conversation
system_prompt = """
You are a friendly, conversational human nurse designed to assess cancer symptoms in elderly patients through natural conversation. Your goal is to evaluate five key symptoms (fatigue, appetite, nausea, cough, and pain) on a 1-5 scale without making the interaction feel like a medical interrogation.

APPROACH:
- The context is the patient lives in hong kong, so use context specific scenarios
- Use a warm, unhurried tone suitable for elderly patients
- Include small personal anecdotes and appropriate humor
- Ask open-ended questions that naturally lead to symptom information
- Listen carefully for keywords indicating symptom severity
- Maintain a conversational flow that feels like catching up with a friend
- Remember details the patient shares to reference later

CONVERSATION STRUCTURE:
1. Begin with genuine small talk about observable items or shared interests
2. Transition naturally to topics that reveal symptom information:
   - Energy levels and daily activities (to assess FATIGUE)
   - Food preferences and eating habits (to assess APPETITE and NAUSEA)
   - Weather and respiratory impacts (to assess COUGH)
   - Movement and comfort levels (to assess PAIN)
3. Include fun tidbits and anecdotes throughout to keep engagement high
4. End with an open question about any other concerns

SYMPTOM ASSESSMENT SCALES:
For each symptom, analyze patient responses to determine severity level (1-5):

FATIGUE SCALE:
- Level 1: Minimal fatigue; normal activity levels
- Level 2: Mild fatigue; slight limitation of activities
- Level 3: Moderate fatigue; needs daily rest; significant activity reduction
- Level 4: Severe fatigue; needs help with daily activities
- Level 5: Extreme fatigue; bed-bound; unable to care for self

APPETITE SCALE:
- Level 1: Normal appetite
- Level 2: Slightly reduced interest in food; >75% normal intake
- Level 3: Moderately reduced appetite; 50-75% normal intake
- Level 4: Severely reduced appetite; 25-50% normal intake
- Level 5: Minimal to no appetite; <25% normal intake

NAUSEA SCALE:
- Level 1: No nausea
- Level 2: Occasional mild queasiness; doesn't affect eating
- Level 3: Intermittent nausea affecting food choices; 3-5 episodes daily
- Level 4: Frequent nausea; significantly limits intake; 6-8 episodes daily
- Level 5: Constant nausea; minimal intake possible; may include vomiting

COUGH SCALE:
- Level 1: Occasional throat clearing; minimal
- Level 2: Infrequent cough; 1-2 episodes daily; mostly dry
- Level 3: Several coughing episodes daily; sometimes productive
- Level 4: Frequent coughing fits; disrupts activities; painful
- Level 5: Constant/severe coughing; may include blood; prevents basic activities

PAIN SCALE:
- Level 1: Minimal; noticeable but doesn't limit activities
- Level 2: Mild; minor limitation; no medication needed
- Level 3: Moderate; requires non-prescription medication; limits some activities
- Level 4: Severe; requires prescription medication; significant limitation
- Level 5: Extreme; inadequate relief with medication; incapacitating

KEYWORDS TO LISTEN FOR:
Evaluate each symptom based on specific phrases the patient uses:

Fatigue keywords:
- Level 1: "I'm doing fine," "normal energy," "just a little tired sometimes"
- Level 2: "I need to rest after chores," "not as much pep," "cut back a bit"
- Level 3: "I need naps most days," "given up activities," "tired most of the time"
- Level 4: "barely get out of bed," "need help with basics," "exhausted all day"
- Level 5: "can't get out of bed without help," "too tired to eat/talk/watch TV"

Appetite keywords:
- Level 1: "Eating normally," "appetite is fine," "enjoying my meals"
- Level 2: "Not quite as hungry," "smaller portions," "less interest in food"
- Level 3: "Skipping meals sometimes," "have to force myself," "only eating half"
- Level 4: "Barely eating," "just picking," "only liquids/soft foods"
- Level 5: "Can't eat at all," "just a few bites," "no interest in food whatsoever"

Nausea keywords:
- Level 1: "No nausea," "stomach feels fine"
- Level 2: "Occasional queasiness," "passes quickly," "doesn't affect eating"
- Level 3: "Have to be careful what I eat," "feel sick after meals," "few times a day"
- Level 4: "Nauseous most of the day," "hard to keep food down," "need medicine"
- Level 5: "Constant nausea," "can't keep anything down," "vomiting multiple times"

Cough keywords:
- Level 1: "Just clearing my throat," "once in a while," "hardly notice it"
- Level 2: "A few times a day," "mostly dry," "not bothersome"
- Level 3: "Several times daily," "disrupts my TV shows," "some mucus"
- Level 4: "Coughing fits," "keeps me up," "hurts when I cough," "productive"
- Level 5: "Can't stop coughing," "exhausting," "brings up phlegm/blood," "all day and night"

Pain keywords:
- Level 1: "Just a twinge," "hardly notice it," "doesn't bother me"
- Level 2: "Noticeable but manageable," "comes and goes," "don't need medication"
- Level 3: "Take over-the-counter meds," "have to stop activities," "distracting"
- Level 4: "Need prescription pain relief," "hard to concentrate," "limits what I can do"
- Level 5: "Nothing helps," "all I can think about," "can't function," "unbearable"

After the conversation, you will be asked to provide a structured assessment summary with ratings for each symptom based on the patient's statements.
"""

# Voice-specific system prompt addition
voice_prompt_addition = """
IMPORTANT VOICE INTERACTION GUIDELINES:
- Keep responses concise since they will be spoken aloud (2-3 sentences maximum)
- Use simple, clear language suitable for voice communication
- Pause appropriately between key points by using commas and periods
"""

# Function to handle the entire conversation and produce the structured output
def conduct_assessment(patient_id, language="en", input_mode="keyboard", conversation_history=None):
    """
    Conduct a symptom assessment conversation with a patient.
    
    Args:
        patient_id (str): ID of the patient
        language (str): Language code ("en" for English, "zh" for Cantonese)
        input_mode (str): "keyboard" for typed input or "speech" for voice input
        conversation_history (list): Existing conversation history if any
        
    Returns:
        dict: Assessment data in structured format
    """
    # Initialize speech service if using speech mode
    speech_service = None
    if input_mode == "speech":
        speech_service = AzureSpeechService()
    
    # Initialize conversation history
    if conversation_history is None:
        conversation_history = []
    
    # Set up the correct system prompt
    full_system_prompt = system_prompt
    if input_mode == "speech":
        full_system_prompt = voice_prompt_addition + full_system_prompt
    
    # Add language instruction
    if language == "zh":
        conversation_history.append({"role": "system", "content": "Please respond in Cantonese (廣東話/粵語)."})
    
    # Start the conversation with a friendly greeting if it's the first message
    if not conversation_history:
        conversation_history.append({"role": "system", "content": full_system_prompt})
        
        # Initial greeting message
        initial_message = f"[PATIENT PROFILE: ID {patient_id}] Hello, I'm here for my check-in today."
        if language == "zh":
            initial_message = f"[PATIENT PROFILE: ID {patient_id}] 您好，我今天來做例行檢查。"
            
        conversation_history.append({"role": "user", "content": initial_message})
        
        # Get initial response from assistant
        try:
            initial_response = client.chat.completions.create(
                model="gpt-4", # Changed from gpt-4-turbo to gpt-4 for better compatibility
                messages=conversation_history
            )
            
            assistant_message = initial_response.choices[0].message.content
            conversation_history.append({"role": "assistant", "content": assistant_message})
            
            # Output the assistant's message
            print("\nAssistant:", assistant_message)
            
            # Speak the greeting if using speech mode
            if input_mode == "speech" and speech_service:
                speech_service.text_to_speech(assistant_message)
        except Exception as e:
            print(f"Error getting initial response: {e}")
            return None
                
    # Continue the conversation until we have enough information
    conversation_complete = False
    turns = 0
    max_turns = 10  # Set a reasonable limit
    assistant_message = None
    
    while not conversation_complete and turns < max_turns:
        # Get user input based on input mode
        if input_mode == "keyboard":
            print("\nPatient (type 'end assessment' to finish): ", end="")
            user_input = input()
        else:  # speech mode
            # Wait a moment to make sure any previous TTS has finished
            time.sleep(0.5)
            
            # STT: Listen for patient response
            print("\nListening for patient response... (speak now or say 'end assessment' to finish)")
            user_input = speech_service.speech_to_text()
            
            # Handle failed speech recognition
            if not user_input:
                retry_message = "I'm sorry, I didn't catch that. Could you please repeat?"
                if language == "zh":
                    retry_message = "對不起，我沒聽清楚。您能再說一次嗎？"
                
                print("Assistant:", retry_message)
                if speech_service:
                    speech_service.text_to_speech(retry_message)
                
                # Try again
                print("Listening again...")
                user_input = speech_service.speech_to_text()
                if not user_input:
                    user_input = "No response"
            
            print(f"Patient: {user_input}")
        
        # Add user input to conversation history
        conversation_history.append({"role": "user", "content": user_input})
        
        # Check if this should be the final turn
        if "finish" in user_input.lower() or turns == max_turns - 1:
            # Final turn - request structured assessment
            final_prompt = "Based on our conversation, please provide a complete symptom assessment in the required structured format."
            if language == "zh":
                final_prompt = "根據我們的對話，請提供完整的症狀評估，使用要求的結構化格式。"
                
            try:
                response = client.chat.completions.create(
                    model="gpt-4",
                    messages=conversation_history + [
                        {"role": "user", "content": final_prompt}
                    ],
                    functions=[assessment_function],
                    function_call={"name": "record_symptom_assessment"}
                )
                
                conversation_complete = True
            except Exception as e:
                print(f"Error generating assessment: {e}")
                return None
        else:
            # Normal conversation turn
            try:
                response = client.chat.completions.create(
                    model="gpt-4",
                    messages=conversation_history
                )
            except Exception as e:
                print(f"Error in conversation: {e}")
                error_msg = "I'm having trouble connecting. Let's try again."
                conversation_history.append({"role": "assistant", "content": error_msg})
                print("Assistant:", error_msg)
                if input_mode == "speech" and speech_service:
                    speech_service.text_to_speech(error_msg)
                continue
        
        # Process the response
        assistant_message = response.choices[0].message
        
        # Check if we have a function call
        if hasattr(assistant_message, 'function_call') and assistant_message.function_call:
            try:
                # Extract the structured assessment
                assessment_data = json.loads(assistant_message.function_call.arguments)
                
                # Add missing required fields
                assessment_data["patient_id"] = patient_id
                assessment_data["timestamp"] = datetime.now().isoformat()
                
                # Ensure treatment_status is set (default to "undergoing_treatment" if not specified)
                if "treatment_status" not in assessment_data:
                    assessment_data["treatment_status"] = "undergoing_treatment"
                
                # Ensure oncologist_notification_level is set
                if "oncologist_notification_level" not in assessment_data:
                    assessment_data["oncologist_notification_level"] = "none"
                
                # Save the assessment to file
                filename = f"patient_{patient_id}_assessment_{assessment_data['timestamp'].replace(':', '-')}.json"
                try:
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(assessment_data, f, indent=2, ensure_ascii=False)
                    
                    print(f"\n--- Assessment Complete ---")
                    print(f"Assessment saved to: {filename}")
                except Exception as e:
                    print(f"Error saving assessment file: {e}")
                
                # Return the structured data
                return assessment_data
            except Exception as e:
                print(f"Error processing assessment data: {e}")
                return None
        else:
            # Extract and show the assistant's response in the normal conversation
            content = assistant_message.content if hasattr(assistant_message, 'content') and assistant_message.content else ""
            conversation_history.append({"role": "assistant", "content": content})
            
            print("\nAssistant:", content)
            
            # Speak the response if using speech mode
            if input_mode == "speech" and speech_service:
                speech_service.text_to_speech(content)
        
        turns += 1
    
    # If we reached max turns without completing assessment
    if not conversation_complete:
        print("Maximum conversation turns reached without completing assessment.")
        return None

# Function to generate a summary based on assessment data
def generate_summary(assessment_data, language="en"):
    """Generate a summary of the assessment in the specified language"""
    
    if not assessment_data:
        return "Assessment could not be completed."
        
    # Check for concerning symptoms (severity or frequency ≥ 3)
    concerning_symptoms = []
    for name, data in assessment_data["symptoms"].items():
        if data["severity_rating"] >= 3 or data["frequency_rating"] >= 3:
            if language == "zh":
                symptom_names = {
                    "fatigue": "疲勞",
                    "pain": "疼痛",
                    "cough": "咳嗽",
                    "nausea": "噁心",
                    "lack_of_appetite": "食慾不振"
                }
                concerning_symptoms.append(symptom_names.get(name, name))
            else:
                concerning_symptoms.append(name.replace("_", " "))
    
    # Generate summary based on language
    if language == "zh":
        if concerning_symptoms:
            symptom_text = "、".join(concerning_symptoms)
            summary = f"根據我們的對話，您主要出現了{symptom_text}等症狀。我已經記錄了這些信息，這將幫助您的醫生更好地了解您的情況。"
            
            if assessment_data.get("flag_for_oncologist", False):
                summary += "由於症狀較為嚴重，我們會提醒醫生儘快查看您的情況。"
        else:
            summary = "感謝您的配合。我已經記錄了您的症狀信息，這將幫助您的醫生更好地了解您的情況。"
        
        summary += "祝您早日康復。"
    else:
        if concerning_symptoms:
            symptom_text = ", ".join(concerning_symptoms)
            summary = f"Based on our conversation, you're mainly experiencing symptoms of {symptom_text}. I've recorded this information which will help your doctor better understand your condition."
            
            if assessment_data.get("flag_for_oncologist", False):
                summary += " Due to the severity of your symptoms, we will notify your doctor to check on you as soon as possible."
        else:
            summary = "Thank you for your cooperation. I've recorded your symptom information, which will help your doctor better understand your condition."
        
        summary += " I wish you a speedy recovery."
    
    return summary

# Main function
def main():
    """Main function to run the program"""
    print("=" * 60)
    print("OnCallLogist Symptom Assessment Tool")
    print("=" * 60)
    
    # Get patient ID
    patient_id = input("Enter patient ID: ")
    
    # Get language preference
    print("\nLanguage options:")
    print("1. English (en)")
    print("2. Cantonese (zh)")
    language_choice = input("Choose language (1 or 2): ")
    
    language = "en"
    if language_choice == "2":
        language = "zh"
    
    # Get input mode
    print("\nInput mode options:")
    print("1. Keyboard input")
    print("2. Speech input/output")
    mode_choice = input("Choose input mode (1 or 2): ")
    
    input_mode = "keyboard"
    if mode_choice == "2":
        input_mode = "speech"
        # Initialize speech service to test connection
        try:
            speech_service = AzureSpeechService()
            print("Speech service initialized successfully.")
        except Exception as e:
            print(f"Error initializing speech service: {e}")
            print("Falling back to keyboard input mode.")
            input_mode = "keyboard"
    
    # Run assessment
    print("\nStarting assessment...")
    assessment_data = conduct_assessment(patient_id, language, input_mode)
    
    # Generate and present summary
    if assessment_data:
        summary = generate_summary(assessment_data, language)
        print("\n" + "=" * 60)
        print("Assessment Summary:")
        print(summary)
        print("=" * 60)
        
        # Speak summary if using speech mode
        if input_mode == "speech":
            try:
                speech_service = AzureSpeechService()
                speech_service.text_to_speech(summary)
            except Exception as e:
                print(f"Error speaking summary: {e}")
        
        # Print symptom details
        print("\nDetailed Symptom Assessment:")
        for symptom, data in assessment_data["symptoms"].items():
            print(f"- {symptom.replace('_', ' ').title()}: Frequency {data['frequency_rating']}/5, Severity {data['severity_rating']}/5")
        
        # Flag for oncologist if needed
        if assessment_data.get("flag_for_oncologist", False):
            print(f"\nATTENTION: This assessment has been flagged for oncologist review.")
            print(f"Reason: {assessment_data.get('flag_reason', 'High symptom severity/frequency')}")
        
        # Offer to launch web viewer
        view_choice = input("\nWould you like to view the assessment in the web viewer? (y/n): ")
        if view_choice.lower() == "y":
            try:
                # Start the web viewer in a new process
                import subprocess
                subprocess.Popen(["python", "json_viewer.py"])
                print("Web viewer started. Please open http://localhost:8000 in your browser.")
            except Exception as e:
                print(f"Error starting web viewer: {e}")
                print("You can manually start it by running: python json_viewer.py")

# Entry point
if __name__ == "__main__":
    main()
