"""
Telenurse Testing Interface

This module creates a web-based testing interface for the telenurse application.
It allows non-technical medical researchers to easily test the system with a
user-friendly interface for configuring options, editing prompts, and viewing results.
"""

import os
import sys
import json
import threading
import webbrowser
import tempfile
import logging
import time
import traceback
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import socketserver
import subprocess
from tts_config import AzureSpeechService

from gpt_convo import conduct_assessment, set_web_interface_mode, add_message_to_queue
set_web_interface_mode(True)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_interface")

# Try to import from the specific files
try:
    logger.info("Attempting to import from gpt_convo.py, gpt_json.py")
    
    # First, try direct import
    try:
        from gpt_convo import conduct_assessment
        logger.info("Successfully imported conduct_assessment from gpt_convo.py")
    except ImportError:
        # If that fails, try using importlib (more flexible)
        import importlib.util
        
        if os.path.exists("gpt_convo.py"):
            spec = importlib.util.spec_from_file_location("gpt_convo", "gpt_convo.py")
            gpt_convo = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gpt_convo)
            conduct_assessment = gpt_convo.conduct_assessment
            logger.info("Successfully imported conduct_assessment using importlib")
        else:
            logger.error("Could not find gpt_convo.py")
            conduct_assessment = None
    
    # Import assessment_function from gpt_json.py
    try:
        from gpt_json import assessment_function
        logger.info("Successfully imported assessment_function from gpt_json.py")
    except ImportError:
        # If that fails, try using importlib
        if os.path.exists("gpt_json.py"):
            spec = importlib.util.spec_from_file_location("gpt_json", "gpt_json.py")
            gpt_json = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gpt_json)
            assessment_function = gpt_json.assessment_function
            logger.info("Successfully imported assessment_function using importlib")
        else:
            logger.error("Could not find gpt_json.py")
            assessment_function = None
    
except Exception as e:
    logger.error(f"Error importing required modules: {e}")
    conduct_assessment = None
    assessment_function = None

# If imports failed, create placeholders
if conduct_assessment is None or assessment_function is None:
    print("\n" + "=" * 80)
    print(" WARNING: Required components not found")
    print("=" * 80)
    print("\nCould not import required functions from the expected files.")
    print("Make sure the following files are in the current directory:")
    print("  - gpt_convo.py (with conduct_assessment function)")
    print("  - gpt_json.py (with assessment_function schema)")
    print("  - json_viewer.py (optional, for JSON visualization)")
    
    # Ask user if they want to continue anyway
    continue_anyway = input("\nWould you like to continue anyway with placeholders? (y/n): ")
    if continue_anyway.lower() != 'y':
        sys.exit(1)
    
    # Create placeholder conduct_assessment function
    if conduct_assessment is None:
        print("\nCreating placeholder conduct_assessment function")
        
        def placeholder_conduct_assessment(patient_id, language="en", input_mode="keyboard", conversation_history=None):
            """Placeholder function that simulates an assessment"""
            # For demo purposes, we'll simulate a conversation
            if conversation_history is None:
                conversation_history = []
            
            # Add placeholder message if none exist
            if not any(msg.get("role") == "assistant" for msg in conversation_history):
                conversation_history.append({
                    "role": "assistant", 
                    "content": "Hello! I'm a placeholder nurse. How are you feeling today?"
                })
            
            # Check if user is asking to finish
            last_messages = [msg for msg in conversation_history if msg.get("role") == "user"]
            if last_messages and "finish" in last_messages[-1].get("content", "").lower():
                # Generate dummy assessment
                return {
                    "patient_id": patient_id,
                    "timestamp": datetime.now().isoformat(),
                    "symptoms": {
                        "fatigue": {
                            "frequency_rating": 3,
                            "severity_rating": 2,
                            "key_indicators": ["I need to rest in the afternoons", "Don't have as much energy as before"]
                        },
                        "pain": {
                            "frequency_rating": 2,
                            "severity_rating": 3,
                            "location": "Lower back and right hip",
                            "key_indicators": ["It comes and goes", "Sometimes I take pain medication"]
                        },
                        "cough": {
                            "frequency_rating": 1,
                            "severity_rating": 1,
                            "key_indicators": ["Just clearing my throat occasionally"]
                        },
                        "nausea": {
                            "frequency_rating": 2,
                            "severity_rating": 2,
                            "key_indicators": ["Sometimes feel queasy after eating", "Passes quickly"]
                        },
                        "lack_of_appetite": {
                            "frequency_rating": 3,
                            "severity_rating": 2,
                            "key_indicators": ["Not as hungry as usual", "Eating smaller portions"]
                        }
                    },
                    "mood_assessment": "Patient appears in good spirits despite symptoms.",
                    "flag_for_oncologist": False,
                    "flag_reason": "",
                    "oncologist_notification_level": "none",
                    "treatment_status": "undergoing_treatment",
                    "conversation_notes": "This is a placeholder assessment generated for testing."
                }
            
            # Add placeholder response to user message
            if last_messages:
                user_msg = last_messages[-1].get("content", "")
                
                # Generate a contextual response based on content
                if "pain" in user_msg.lower():
                    conversation_history.append({
                        "role": "assistant", 
                        "content": "I see you're experiencing some pain. Can you tell me where it hurts and how severe it is on a scale of 1-5?"
                    })
                elif "tired" in user_msg.lower() or "fatigue" in user_msg.lower():
                    conversation_history.append({
                        "role": "assistant", 
                        "content": "You mentioned feeling tired. How has your energy level been this past week?"
                    })
                elif "appetite" in user_msg.lower() or "eating" in user_msg.lower():
                    conversation_history.append({
                        "role": "assistant", 
                        "content": "Tell me more about your appetite. Are you able to eat regular meals?"
                    })
                elif "cough" in user_msg.lower():
                    conversation_history.append({
                        "role": "assistant", 
                        "content": "About that cough - is it dry or are you bringing up any mucus?"
                    })
                elif "sick" in user_msg.lower() or "nausea" in user_msg.lower():
                    conversation_history.append({
                        "role": "assistant", 
                        "content": "Are you experiencing any nausea or upset stomach?"
                    })
                else:
                    conversation_history.append({
                        "role": "assistant", 
                        "content": "Thank you for sharing that. How would you rate your overall comfort today on a scale of 1-5?"
                    })
            
            # Return None to indicate conversation is still ongoing
            return None
        
        conduct_assessment = placeholder_conduct_assessment
    
    # Create placeholder assessment_function
    if assessment_function is None:
        print("\nCreating placeholder assessment_function schema")
        assessment_function = {
            "name": "record_symptom_assessment",
            "description": "Placeholder schema for assessment function",
            "parameters": {
                "type": "object",
                "properties": {
                    "symptoms": {"type": "object"},
                    "flag_for_oncologist": {"type": "boolean"}
                }
            }
        }

# Default system prompt (load from file if exists)
DEFAULT_SYSTEM_PROMPT_PATH = "system_prompt.txt"
DEFAULT_SYSTEM_PROMPT = """
You are a friendly, conversational human nurse designed to assess cancer symptoms in elderly patients through natural conversation. Your goal is to evaluate five key symptoms (fatigue, appetite, nausea, cough, and pain) on a 1-5 scale without making the interaction feel like a medical interrogation.

APPROACH:
- Use a warm, unhurried tone suitable for elderly patients
- Include small personal anecdotes and appropriate humor
- Ask open-ended questions that naturally lead to symptom information
- Listen carefully for keywords indicating symptom severity
- Maintain a conversational flow that feels like catching up with a friend
- Remember details the patient shares to reference later

CONVERSATION STRUCTURE:
1. Begin with genuine small talk about observable items or shared interests, such as asking how their week was, general wellbeing, or family updates.
   - Example: "How was your week? Did you do anything fun with the family?"
   Do not make assumptions about hobbies
2. Transition naturally to topics that reveal symptom information:
   - Energy levels and daily activities (to assess FATIGUE)
   - Food preferences and eating habits (to assess APPETITE and NAUSEA)
   - Weather and respiratory impacts (to assess COUGH)
   - Movement and comfort levels (to assess PAIN)
3. Include fun tidbits and anecdotes throughout to keep engagement high
4. End with an open question about any other concerns
"""

# Try to load system prompt from file
if os.path.exists(DEFAULT_SYSTEM_PROMPT_PATH):
    try:
        with open(DEFAULT_SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
            DEFAULT_SYSTEM_PROMPT = f.read()
            logger.info(f"Loaded system prompt from {DEFAULT_SYSTEM_PROMPT_PATH}")
    except Exception as e:
        logger.error(f"Error loading system prompt from file: {e}")

# Global state for the ongoing assessment
current_assessment = {
    "active": False,
    "thread": None,
    "patient_id": None,
    "language": None,
    "input_mode": None,
    "conversation_history": [],
    "result": None
}

class TeleNurseHandler(SimpleHTTPRequestHandler):
    """Custom HTTP handler for the Telenurse Testing Interface"""
    
    def do_GET(self):
        """Handle GET requests"""
        parsed_path = urlparse(self.path)
        
        # Serve the React app
        if parsed_path.path == '/' or parsed_path.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(html_content.encode())
            return
            
        # Serve the main JavaScript file
        elif parsed_path.path == '/app.js':
            self.send_response(200)
            self.send_header('Content-type', 'application/javascript')
            self.end_headers()
            self.wfile.write(js_content.encode())
            return
            
        # Serve the CSS file
        elif parsed_path.path == '/styles.css':
            self.send_response(200)
            self.send_header('Content-type', 'text/css')
            self.end_headers()
            self.wfile.write(css_content.encode())
            return
            
        # API endpoint for getting the current system prompt
        elif parsed_path.path == '/api/get_system_prompt':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            response = {
                "success": True,
                "system_prompt": DEFAULT_SYSTEM_PROMPT
            }
            
            self.wfile.write(json.dumps(response).encode())
            return
            
        # API endpoint for getting the assessment status
        elif parsed_path.path == '/api/assessment_status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            response = {
                "active": current_assessment["active"],
                "patient_id": current_assessment["patient_id"],
                "language": current_assessment["language"],
                "input_mode": current_assessment["input_mode"],
                "conversation": [msg for msg in current_assessment["conversation_history"] 
                                if msg.get("role") != "system"],
                "result": current_assessment["result"],
                "listening": current_assessment.get("listening", False)  # Add this

            }
            
            self.wfile.write(json.dumps(response).encode())
            return
            
        # API endpoint for getting available saved assessments
        

        elif parsed_path.path == '/api/get_saved_assessments':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            saved_assessments = []
            
            try:
                # Find all JSON files in the assessment_records directory
                for filename in os.listdir("assessment_records"):
                    if filename.startswith('patient_') and filename.endswith('.json'):
                        saved_assessments.append(filename)
            except Exception as e:
                logger.error(f"Error listing saved assessments: {e}")
            
            response = {
                "success": True,
                "assessments": saved_assessments
            }
            
            self.wfile.write(json.dumps(response).encode())
            return
            
        # API endpoint for getting a specific saved assessment
        elif parsed_path.path.startswith('/api/assessment/'):
            filename = parsed_path.path.replace('/api/assessment/', '')
            
            try:
                # Look for the file in the assessment_records directory
                filepath = os.path.join("assessment_records", filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    assessment_data = json.load(f)
                    
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": True,
                    "assessment": assessment_data
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
                
            except Exception as e:
                logger.error(f"Error loading assessment {filename}: {e}")
                
                self.send_response(404)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": False,
                    "error": f"Could not load assessment file: {str(e)}"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return

        # Default: file not found
        self.send_response(404)
        self.send_header('Content-type', 'application/json')
        self.end_headers()

        response = {
            "success": False,
            "error": "Endpoint not found"
        }

        self.wfile.write(json.dumps(response).encode())
    
    def do_POST(self):
        """Handle POST requests"""
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length).decode('utf-8')
        
        # Parse the data based on content type
        if self.headers.get('Content-Type') == 'application/json':
            data = json.loads(post_data)
        else:
            data = parse_qs(post_data)
            # Convert from lists to single values
            data = {k: v[0] if len(v) == 1 else v for k, v in data.items()}
        
        # Update system prompt
        if self.path == '/api/update_system_prompt':
            try:
                new_prompt = data.get('system_prompt')
                if not new_prompt:
                    raise ValueError("System prompt cannot be empty")
                
                # Save to global variable
                global DEFAULT_SYSTEM_PROMPT
                DEFAULT_SYSTEM_PROMPT = new_prompt
                
                # Save to file
                with open(DEFAULT_SYSTEM_PROMPT_PATH, 'w', encoding='utf-8') as f:
                    f.write(new_prompt)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": True,
                    "message": "System prompt updated successfully"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
                
            except Exception as e:
                logger.error(f"Error updating system prompt: {e}")
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": False,
                    "error": f"Failed to update system prompt: {str(e)}"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
        
        # Start an assessment
        elif self.path == '/api/start_assessment':
            try:
                if current_assessment["active"]:
                    raise ValueError("An assessment is already in progress")
                
                # Get parameters
                patient_id = data.get('patient_id')
                language = data.get('language', 'en')
                input_mode = data.get('input_mode', 'keyboard')
                
                if not patient_id:
                    raise ValueError("Patient ID is required")
                
                # Prepare conversation history with system prompt
                conversation_history = [
                    {"role": "system", "content": DEFAULT_SYSTEM_PROMPT}
                ]
                
                # Add language instruction if needed
                if language == "zh":
                    conversation_history.append({"role": "system", "content": "Please respond in Cantonese (廣東話/粵語)."})
                
                # Update current assessment
                current_assessment["active"] = True
                current_assessment["patient_id"] = patient_id
                current_assessment["language"] = language
                current_assessment["input_mode"] = input_mode
                current_assessment["conversation_history"] = conversation_history
                current_assessment["result"] = None
                
                # Start assessment in a separate thread
                # In the run_assessment function inside the /api/start_assessment endpoint
                def run_assessment():
                    try:
                        print("\n[DEBUG] Starting assessment thread for patient:", patient_id)
                        # Run the assessment
                        result = conduct_assessment(
                            patient_id=patient_id,
                            language=language,
                            input_mode=input_mode,
                            conversation_history=conversation_history
                        )
                        print(f"\n[DEBUG] Assessment thread completed with result: {result is not None}")
                        # Update with result
                        current_assessment["result"] = result
                    except Exception as e:
                        print(f"\n[DEBUG] ERROR in assessment thread: {e}")
                        logger.error(f"Error in assessment thread: {e}")
                        traceback.print_exc()  # Add this to see the full stack trace
                    finally:
                        print("\n[DEBUG] Assessment thread marked as inactive")
                        # Mark as inactive when done
                        current_assessment["active"] = False
                
                current_assessment["thread"] = threading.Thread(target=run_assessment)
                current_assessment["thread"].daemon = True
                current_assessment["thread"].start()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": True,
                    "message": f"Started assessment for patient {patient_id}"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
                
            except Exception as e:
                logger.error(f"Error starting assessment: {e}")
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": False,
                    "error": f"Failed to start assessment: {str(e)}"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
        
        # Send user message in the assessment
        # In the /api/send_message endpoint handler
        elif self.path == '/api/send_message':
            try:
                if not current_assessment["active"]:
                    print("\n[DEBUG] Cannot send message - no active assessment")
                    raise ValueError("No active assessment")
                
                message = data.get('message')
                if not message:
                    print("\n[DEBUG] Cannot send message - empty message")
                    raise ValueError("Message cannot be empty")
                
                print(f"\n[DEBUG] Adding message to conversation history: '{message}'")
                #Add message to conversation history
                # current_assessment["conversation_history"].append({
                #     "role": "user",
                #     "content": message
                # })
                
                # Add the message to the input queue
                add_message_to_queue(message)
                
                # Print the current thread status
                if current_assessment["thread"]:
                    print(f"\n[DEBUG] Assessment thread is_alive: {current_assessment['thread'].is_alive()}")
                else:
                    print("\n[DEBUG] No assessment thread exists")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": True,
                    "message": "Message sent"
                }
                
                print("\n[DEBUG] Successful response sent to client for message")
                self.wfile.write(json.dumps(response).encode())
                return
                        
            except Exception as e:
                logger.error(f"Error sending message: {e}")
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": False,
                    "error": f"Failed to send message: {str(e)}"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
        
        # Finish the assessment
        elif self.path == '/api/finish_assessment':
            try:
                if not current_assessment["active"]:
                    raise ValueError("No active assessment")
                
                # Add "finish" message to both the conversation history and the queue
                # current_assessment["conversation_history"].append({
                #     "role": "user",
                #     "content": "finish"
                # })
                
                # Add to queue so the assessment thread can see it
                print("\n[DEBUG] Adding 'finish' message to queue")
                add_message_to_queue("Finishing, please wait...")  
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": True,
                    "message": "Assessment finishing"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return

                
            except Exception as e:
                logger.error(f"Error finishing assessment: {e}")
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": False,
                    "error": f"Failed to finish assessment: {str(e)}"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
        
        # Cancel the assessment
        elif self.path == '/api/cancel_assessment':
            try:
                if not current_assessment["active"]:
                    raise ValueError("No active assessment")
                
                # Reset current assessment
                current_assessment["active"] = False
                current_assessment["patient_id"] = None
                current_assessment["language"] = None
                current_assessment["input_mode"] = None
                current_assessment["conversation_history"] = []
                current_assessment["result"] = None
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": True,
                    "message": "Assessment cancelled"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
                
            except Exception as e:
                logger.error(f"Error cancelling assessment: {e}")
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": False,
                    "error": f"Failed to cancel assessment: {str(e)}"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
        elif self.path == '/api/speech/recognize':
            try:
                if not current_assessment["active"]:
                    print("\n[DEBUG] Cannot recognize speech - no active assessment")
                    raise ValueError("No active assessment")
                
                if current_assessment["input_mode"] != "speech":
                    print("\n[DEBUG] Cannot recognize speech - wrong input mode")
                    raise ValueError("Current input mode is not speech")
                
                # Initialize speech service
                print("\n[DEBUG] Creating speech service")
                speech_service = AzureSpeechService()
                
                # Set speech recognition status
                current_assessment["listening"] = True
                
                # Run speech recognition
                print("\n[DEBUG] Starting speech recognition")
                recognized_text = speech_service.speech_to_text()
                
                # Reset listening status
                current_assessment["listening"] = False
                
                if recognized_text:
                    print(f"\n[DEBUG] Adding recognized speech to queue: '{recognized_text}'")
                    # Add to queue so assessment thread can process it
                    add_message_to_queue(recognized_text)
                    
                    # Add to conversation history
                    # current_assessment["conversation_history"].append({
                    #     "role": "user",
                    #     "content": recognized_text
                    # })
                    
                    # Send successful response
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    
                    response = {
                        "success": True,
                        "message": "Speech recognized",
                        "text": recognized_text
                    }
                else:
                    # No speech recognized
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    
                    response = {
                        "success": False,
                        "message": "No speech recognized",
                        "text": ""
                    }
                
                self.wfile.write(json.dumps(response).encode())
                return
            except Exception as e:
                print(f"\n[DEBUG] Error in speech recognition: {e}")
                logger.error(f"Error in speech recognition: {e}")
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": False,
                    "error": f"Failed to recognize speech: {str(e)}"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return


            except Exception as e:
                print(f"\n[DEBUG] Error in speech recognition: {e}")
                logger.error(f"Error in speech recognition: {e}")
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": False,
                    "error": f"Failed to recognize speech: {str(e)}"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
        elif self.path == '/api/reset_assessment':
            try:
                # Reset current assessment
                current_assessment["active"] = False
                current_assessment["patient_id"] = None
                current_assessment["language"] = None
                current_assessment["input_mode"] = None
                current_assessment["conversation_history"] = []
                current_assessment["result"] = None
                
                # Clear the input queue
                global _input_queue
                _input_queue = []
                
                print("\n[DEBUG] Assessment state reset")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": True,
                    "message": "Assessment state reset"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return
            except Exception as e:
                logger.error(f"Error resetting assessment: {e}")
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                response = {
                    "success": False,
                    "error": f"Failed to reset assessment: {str(e)}"
                }
                
                self.wfile.write(json.dumps(response).encode())
                return

        # Default: endpoint not found
        self.send_response(404)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        response = {
            "success": False,
            "error": "Endpoint not found"
        }
        
        self.wfile.write(json.dumps(response).encode())
        





# Main function to start the server
def start_server(port=8080):
    """Start the test interface server"""
    
    global html_content, js_content, css_content
    
    # HTML for the React app
    try:
        with open('interface/index.html', 'r', encoding='utf-8') as f:
            html_content = f.read()
            logger.info("Successfully loaded HTML from index.html")
    except Exception as e:
        logger.error(f"Error loading HTML from file: {e}")
        # Create a fallback HTML in case of failure
        html_content = """<!DOCTYPE html>
<html>
<head>
    <title>TeleNurse - Error</title>
</head>
<body>
    <h1>Error Loading Interface</h1>
    <p>The interface could not be loaded properly. Please ensure 'index.html' exists in the same directory.</p>
    <p>Error: """ + str(e) + """</p>
</body>
</html>"""


    # JavaScript for the React app
    try:
        with open('app.js', 'r', encoding='utf-8') as f:
            js_content = f.read()
            logger.info(f"Successfully loaded JavaScript from app.js")
    except Exception as e:
        logger.error(f"Error loading JavaScript from file: {e}")
        # Create a fallback JavaScript in case of failure
        js_content = """
console.error('Failed to load app.js. Interface will not function properly.');
document.getElementById('root').innerHTML = '<div style="color: red; padding: 20px;">Error loading JavaScript. Please ensure app.js exists in the "{interface_dir}" directory.</div>';
"""


    # CSS for the React app
    try:
        with open('interface/styles.css', 'r', encoding='utf-8') as f:
            css_content = f.read()
            logger.info(f"Successfully loaded CSS from interface_dir/styles.css")
    except Exception as e:
        logger.error(f"Error loading CSS from file: {e}")
        # Create a minimal fallback CSS in case of failure
        css_content = """
body { font-family: Arial, sans-serif; line-height: 1.6; margin: 0; padding: 20px; }
h1 { color: #0066cc; }
.error-message { color: red; border-left: 4px solid red; padding: 10px; background-color: #ffeeee; }
"""


    # Find an available port
    def find_available_port(start_port=8080, max_attempts=10):
        import socket
        
        for port_attempt in range(start_port, start_port + max_attempts):
            try:
                # Try to create a socket with the port
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('', port_attempt))
                    # If we get here, the port is available
                    return port_attempt
            except OSError:
                # Port is in use, try the next one
                continue
        
        # If we get here, no ports were available
        raise RuntimeError(f"Could not find an available port after {max_attempts} attempts starting from {start_port}")
    
    # Try to find an available port
    try:
        port = find_available_port(port)
    except Exception as e:
        logger.error(f"Error finding available port: {e}")
        port = 8080  # Use default as fallback
    
    # Start the HTTP server
    try:
        server_address = ('', port)
        httpd = ThreadingHTTPServer(server_address, TeleNurseHandler)
        
        # Start server in a separate thread
        server_thread = threading.Thread(target=httpd.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        
        print(f"Server running at http://localhost:{port}/")
        logger.info(f"Server running at http://localhost:{port}/")
        
        # Make browser opening optional
        try:
            webbrowser.open(f"http://localhost:{port}/")
            print(f"Opening browser at http://localhost:{port}/")
        except Exception as e:
            print(f"Could not automatically open browser. Please manually navigate to: http://localhost:{port}/")
            logger.warning(f"Failed to open browser: {e}")
        
        # Show a clear message about how to access the interface
        print("\n" + "=" * 80)
        print(f" TeleNurse Testing Interface running at http://localhost:{port}/")
        print("=" * 80)
        print("If the browser doesn't open automatically, please:")
        print(f"1. Open your web browser manually")
        print(f"2. Go to http://localhost:{port}/")
        print("3. If you see an empty page, try refreshing or checking the browser console (F12)")
        print("\nPress Ctrl+C to stop the server when finished.")
        
        # Keep the main thread running
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nServer stopped by user.")
            httpd.shutdown()
            
    except Exception as e:
        logger.error(f"Error starting server: {e}")
        print(f"Error starting server: {e}")
        sys.exit(1)

if __name__ == "__main__":
    start_server()