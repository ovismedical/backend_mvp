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
                # Find all JSON files in the current directory
                for filename in os.listdir('.'):
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
                with open(filename, 'r', encoding='utf-8') as f:
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
                add_message_to_queue("finish")  # Add this line
                
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
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TeleNurse Testing Interface</title>
    <link rel="stylesheet" href="/styles.css">
    <!-- Use development versions of React for better error messages -->
    <script src="https://unpkg.com/react@18/umd/react.development.js" crossorigin></script>
    <script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js" crossorigin></script>
    <script src="https://unpkg.com/babel-standalone@6/babel.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js"></script>
</head>
<body>
    <div id="root">
        <!-- Loading indicator in case of slow JS loading -->
        <div style="display: flex; justify-content: center; align-items: center; height: 100vh; flex-direction: column;">
            <h2>Loading TeleNurse Testing Interface...</h2>
            <p>If this message persists, please check console for errors (F12)</p>
        </div>
    </div>
    <!-- Use defer to ensure the DOM is ready -->
    <script type="text/babel" src="/app.js" defer></script>
    
    <!-- Fallback script in case main script fails -->
    <script>
        window.addEventListener('load', function() {
            // Check if React rendered anything after 3 seconds
            setTimeout(function() {
                const root = document.getElementById('root');
                if (root && root.children.length === 1 && root.children[0].textContent.includes('Loading')) {
                    console.error('React app failed to render. Showing fallback content.');
                    root.innerHTML = `
                        <div style="max-width: 800px; margin: 40px auto; padding: 20px; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                            <h2 style="color: #0066cc; margin-bottom: 20px;">TeleNurse Testing Interface</h2>
                            <div style="padding: 15px; background: #ffebe6; border-left: 4px solid #dc3545; margin-bottom: 20px;">
                                <h3 style="margin-top: 0; color: #dc3545;">Error Loading Interface</h3>
                                <p>The interface failed to load properly. This might be due to:</p>
                                <ul>
                                    <li>JavaScript being disabled</li>
                                    <li>Network connectivity issues with CDN resources</li>
                                    <li>JavaScript errors in the application code</li>
                                </ul>
                                <p>Please check your browser console (F12) for more details.</p>
                            </div>
                            <p>You can try:</p>
                            <ul>
                                <li>Refreshing the page</li>
                                <li>Using a different browser</li>
                                <li>Checking your internet connection</li>
                            </ul>
                            <div style="margin-top: 20px;">
                                <button onclick="window.location.reload()" style="background: #0066cc; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer;">
                                    Refresh Page
                                </button>
                            </div>
                        </div>
                    `;
                }
            }, 3000);
        });
    </script>
</body>
</html>"""

    # JavaScript for the React app
    js_content = """
// Make sure React and ReactDOM are defined
if (typeof React === 'undefined' || typeof ReactDOM === 'undefined') {
    console.error('React or ReactDOM not loaded. Check network connections.');
    document.getElementById('root').innerHTML = '<div style="color: red; margin: 30px;">Error: React libraries failed to load.</div>';
} else {
    console.log('React version:', React.version);
    console.log('ReactDOM version:', ReactDOM.version);
}

const { useState, useEffect, useRef } = React;

// Utility function for API calls
const api = {
    getSystemPrompt: async () => {
        try {
            const response = await axios.get('/api/get_system_prompt');
            return response.data;
        } catch (error) {
            console.error('API Error:', error);
            return { success: false, error: error.message };
        }
    },
    updateSystemPrompt: async (systemPrompt) => {
        try {
            const response = await axios.post('/api/update_system_prompt', { system_prompt: systemPrompt });
            return response.data;
        } catch (error) {
            console.error('API Error:', error);
            return { success: false, error: error.message };
        }
    },
    getAssessmentStatus: async () => {
        try {
            const response = await axios.get('/api/assessment_status');
            return response.data;
        } catch (error) {
            console.error('API Error:', error);
            return { active: false, error: error.message };
        }
    },
    startAssessment: async (patientId, language, inputMode) => {
        try {
            const response = await axios.post('/api/start_assessment', { 
                patient_id: patientId,
                language,
                input_mode: inputMode
            });
            return response.data;
        } catch (error) {
            console.error('API Error:', error);
            return { success: false, error: error.message };
        }
    },
    sendMessage: async (message) => {
        try {
            const response = await axios.post('/api/send_message', { message });
            return response.data;
        } catch (error) {
            console.error('API Error:', error);
            return { success: false, error: error.message };
        }
    },
    finishAssessment: async () => {
        try {
            const response = await axios.post('/api/finish_assessment');
            return response.data;
        } catch (error) {
            console.error('API Error:', error);
            return { success: false, error: error.message };
        }
    },
    cancelAssessment: async () => {
        try {
            const response = await axios.post('/api/cancel_assessment');
            return response.data;
        } catch (error) {
            console.error('API Error:', error);
            return { success: false, error: error.message };
        }
    },
    getSavedAssessments: async () => {
        try {
            const response = await axios.get('/api/get_saved_assessments');
            return response.data;
        } catch (error) {
            console.error('API Error:', error);
            return { success: false, error: error.message, assessments: [] };
        }
    },
    getAssessment: async (filename) => {
        try {
            const response = await axios.get(`/api/assessment/${filename}`);
            return response.data;
        } catch (error) {
            console.error('API Error:', error);
            return { success: false, error: error.message };
        }
    },
    recognizeSpeech: async () => {
    try {
        const response = await axios.post('/api/speech/recognize');
        return response.data;
    } catch (error) {
        console.error('API Error:', error);
        return { success: false, error: error.message };
    }
},


};

// Error Boundary Component to catch rendering errors
class ErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, error: null, errorInfo: null };
    }
    
    static getDerivedStateFromError(error) {
        return { hasError: true };
    }
    
    componentDidCatch(error, errorInfo) {
        console.error("React error:", error, errorInfo);
        this.setState({ error, errorInfo });
    }
    
    render() {
        if (this.state.hasError) {
            return (
                <div style={{ padding: '20px', margin: '20px', borderRadius: '8px', backgroundColor: '#ffebee', border: '1px solid #f44336' }}>
                    <h2 style={{ color: '#d32f2f' }}>Something went wrong</h2>
                    <p>The application encountered an error. Please try refreshing the page.</p>
                    <details style={{ whiteSpace: 'pre-wrap', marginTop: '10px' }}>
                        <summary>Error Details</summary>
                        <p style={{ color: '#d32f2f' }}>{this.state.error && this.state.error.toString()}</p>
                        <p style={{ fontSize: '0.8em', marginTop: '10px' }}>
                            {this.state.errorInfo && this.state.errorInfo.componentStack}
                        </p>
                    </details>
                    <button 
                        onClick={() => window.location.reload()} 
                        style={{ 
                            marginTop: '15px', 
                            padding: '8px 16px', 
                            backgroundColor: '#2196f3', 
                            color: 'white',
                            border: 'none',
                            borderRadius: '4px',
                            cursor: 'pointer'
                        }}
                    >
                        Refresh Page
                    </button>
                </div>
            );
        }
        
        return this.props.children;
    }
}

// Main App Component
const App = () => {
    console.log('Rendering App component');
    const [listening, setListening] = useState(false);

    const [activeTab, setActiveTab] = useState('test');
    const [systemPrompt, setSystemPrompt] = useState('');
    const [isEditing, setIsEditing] = useState(false);
    const [saving, setSaving] = useState(false);
    const [savingMessage, setSavingMessage] = useState('');
    const [assessmentStatus, setAssessmentStatus] = useState({ 
        active: false, 
        patient_id: '', 
        language: 'en', 
        input_mode: 'keyboard',
        conversation: [],
        result: null
    });
    const [patientId, setPatientId] = useState('');
    const [language, setLanguage] = useState('en');
    const [inputMode, setInputMode] = useState('keyboard');
    const [userMessage, setUserMessage] = useState('');
    const [savedAssessments, setSavedAssessments] = useState([]);
    const [selectedAssessment, setSelectedAssessment] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    
    const conversationEndRef = useRef(null);
    const statusInterval = useRef(null);
    
    // Polling function for assessment status
    const pollAssessmentStatus = async () => {
        try {
            const status = await api.getAssessmentStatus();
            setAssessmentStatus(status);
            
            // Scroll to bottom of conversation
            if (conversationEndRef.current) {
                conversationEndRef.current.scrollIntoView({ behavior: 'smooth' });
            }
            
            // Load saved assessments if assessment is not active
            if (!status.active && activeTab === 'results' && savedAssessments.length === 0) {
                loadSavedAssessments();
            }
        } catch (err) {
            console.error('Error polling assessment status:', err);
        }
    };
    
    // Load saved assessments
    const loadSavedAssessments = async () => {
        try {
            const result = await api.getSavedAssessments();
            if (result.success) {
                setSavedAssessments(result.assessments);
            }
        } catch (err) {
            console.error('Error loading saved assessments:', err);
            setError('Failed to load saved assessments');
        }
    };
    
    // Load a specific assessment
    const loadAssessment = async (filename) => {
        try {
            setLoading(true);
            const result = await api.getAssessment(filename);
            if (result.success) {
                setSelectedAssessment(result.assessment);
            } else {
                setError('Failed to load assessment');
            }
        } catch (err) {
            console.error('Error loading assessment:', err);
            setError('Failed to load assessment');
        } finally {
            setLoading(false);
        }
    };
    
    // Initialize
    useEffect(() => {
        console.log('App component mounted');
        
        const fetchSystemPrompt = async () => {
            try {
                setLoading(true);
                const result = await api.getSystemPrompt();
                if (result.success) {
                    setSystemPrompt(result.system_prompt);
                }
            } catch (err) {
                console.error('Error fetching system prompt:', err);
                setError('Failed to load system prompt');
            } finally {
                setLoading(false);
            }
        };
        
        fetchSystemPrompt();
        pollAssessmentStatus();
        
        // Start polling
        statusInterval.current = setInterval(pollAssessmentStatus, 1000);
        
        return () => {
            clearInterval(statusInterval.current);
        };
    }, []);
    
    // Handle tab change
    useEffect(() => {
        if (activeTab === 'results') {
            loadSavedAssessments();
        }
    }, [activeTab]);
    
    // Save system prompt
    const handleSavePrompt = async () => {
        try {
            setSaving(true);
            setSavingMessage('');
            
            const result = await api.updateSystemPrompt(systemPrompt);
            if (result.success) {
                setIsEditing(false);
                setSavingMessage('System prompt saved successfully!');
                setTimeout(() => {
                    setSavingMessage('');
                }, 3000);
            } else {
                setError('Failed to save system prompt');
            }
        } catch (err) {
            console.error('Error saving system prompt:', err);
            setError('Failed to save system prompt');
        } finally {
            setSaving(false);
        }
    };
    
    // Start assessment
    const handleStartAssessment = async () => {
        try {
            if (!patientId) {
                setError('Patient ID is required');
                return;
            }
            
            setLoading(true);
            setError('');
            
            const result = await api.startAssessment(patientId, language, inputMode);
            if (!result.success) {
                setError(result.error || 'Failed to start assessment');
            }
        } catch (err) {
            console.error('Error starting assessment:', err);
            setError('Failed to start assessment');
        } finally {
            setLoading(false);
        }
    };
    
    // Send message
    const handleSendMessage = async (e) => {
        e.preventDefault();
        
        if (!userMessage.trim()) return;
        
        try {
            await api.sendMessage(userMessage);
            setUserMessage('');
        } catch (err) {
            console.error('Error sending message:', err);
            setError('Failed to send message');
        }
    };
    // Handle speech recognition
    const handleSpeechRecognition = async () => {
        try {
            setListening(true);
            setError('');
            
            const result = await api.recognizeSpeech();
            
            if (result.success && result.text) {
                console.log('Speech recognized:', result.text);
                // No need to set userMessage - it goes directly to the queue
            } else {
                setError('Failed to recognize speech. Please try again.');
            }
        } catch (err) {
            console.error('Error with speech recognition:', err);
            setError('Error with speech recognition. Please try again.');
        } finally {
            setListening(false);
        }
    };

    
    // Finish assessment
    const handleFinishAssessment = async () => {
        try {
            setLoading(true);
            await api.finishAssessment();
        } catch (err) {
            console.error('Error finishing assessment:', err);
            setError('Failed to finish assessment');
        } finally {
            setLoading(false);
        }
    };
    
    // Cancel assessment
    const handleCancelAssessment = async () => {
        try {
            if (!window.confirm('Are you sure you want to cancel this assessment? All progress will be lost.')) {
                return;
            }
            
            setLoading(true);
            await api.cancelAssessment();
        } catch (err) {
            console.error('Error cancelling assessment:', err);
            setError('Failed to cancel assessment');
        } finally {
            setLoading(false);
        }
    };
    
    // View JSON
    const handleViewJson = () => {
        setActiveTab('results');
        if (assessmentStatus.result) {
            setSelectedAssessment(assessmentStatus.result);
        } else {
            loadSavedAssessments();
        }
    };
    
    console.log('Rendering UI with activeTab:', activeTab);
    console.log('Assessment status active:', assessmentStatus.active);
    
    return (
        <div className="app">
            <header className="header">
                <h1>TeleNurse Testing Interface</h1>
                <div className="tabs">
                    <button 
                        className={activeTab === 'test' ? 'active' : ''}
                        onClick={() => setActiveTab('test')}
                    >
                        Test Interface
                    </button>
                    <button 
                        className={activeTab === 'prompt' ? 'active' : ''}
                        onClick={() => setActiveTab('prompt')}
                    >
                        Edit Prompt
                    </button>
                    <button 
                        className={activeTab === 'results' ? 'active' : ''}
                        onClick={() => setActiveTab('results')}
                    >
                        View Results
                    </button>
                </div>
            </header>
            
            {error && (
                <div className="error-message">
                    <p>{error}</p>
                    <button onClick={() => setError('')}>Dismiss</button>
                </div>
            )}
            
            {loading && (
                <div className="loading-overlay">
                    <div className="loading-spinner"></div>
                </div>
            )}
            
            {activeTab === 'test' && (
                <div className="test-container">
                    {!assessmentStatus.active && !assessmentStatus.result ? (
                        <div className="test-setup">
                            <h2>Start New Test</h2>
                            <div className="form-group">
                                <label htmlFor="patientId">Patient ID:</label>
                                <input 
                                    type="text" 
                                    id="patientId" 
                                    value={patientId}
                                    onChange={(e) => setPatientId(e.target.value)}
                                    placeholder="Enter Patient ID"
                                />
                            </div>
                            
                            <div className="form-group">
                                <label htmlFor="language">Language:</label>
                                <select 
                                    id="language" 
                                    value={language}
                                    onChange={(e) => setLanguage(e.target.value)}
                                >
                                    <option value="en">English</option>
                                    <option value="zh">Cantonese</option>
                                </select>
                            </div>
                            
                            <div className="form-group">
                                <label htmlFor="inputMode">Input Mode:</label>
                                <select 
                                    id="inputMode" 
                                    value={inputMode}
                                    onChange={(e) => setInputMode(e.target.value)}
                                >
                                    <option value="keyboard">Keyboard</option>
                                    <option value="speech">Speech</option>
                                </select>
                            </div>
                            
                            <button 
                                className="primary-button"
                                onClick={handleStartAssessment}
                                disabled={loading}
                            >
                                Start Conversation
                            </button>
                        </div>
                    ) : (
                        <div className="conversation-container">
                            <div className="conversation-header">
                                <h2>
                                    Patient: {assessmentStatus.patient_id} 
                                    {assessmentStatus.language === 'zh' ? ' (Cantonese)' : ' (English)'}
                                </h2>
                                <p>
                                    Input Mode: {assessmentStatus.input_mode === 'speech' ? 'Speech' : 'Keyboard'}
                                </p>
                                <div className="conversation-actions">
                                    {assessmentStatus.active ? (
                                        <React.Fragment>
                                            <button 
                                                className="secondary-button"
                                                onClick={handleCancelAssessment}
                                            >
                                                Cancel Test
                                            </button>
                                            <button 
                                                className="primary-button"
                                                onClick={handleFinishAssessment}
                                            >
                                                Finish Conversation
                                            </button>
                                        </React.Fragment>
                                    ) : (
                                        <React.Fragment>
                                            <button 
                                                className="secondary-button"
                                                onClick={() => {
                                                    setAssessmentStatus({ 
                                                        active: false, 
                                                        patient_id: '', 
                                                        language: 'en', 
                                                        input_mode: 'keyboard',
                                                        conversation: [],
                                                        result: null
                                                    });
                                                }}
                                            >
                                                New Test
                                            </button>
                                            {assessmentStatus.result && (
                                                <button 
                                                    className="primary-button"
                                                    onClick={handleViewJson}
                                                >
                                                    View Assessment Data
                                                </button>
                                            )}
                                        </React.Fragment>
                                    )}
                                </div>
                            </div>
                            
                            <div className="conversation-messages">
                                {assessmentStatus.conversation.map((msg, index) => (
                                    <div 
                                        key={index}
                                        className={`message ${msg.role === 'user' ? 'user-message' : 'assistant-message'}`}
                                    >
                                        <div className="message-content">
                                            <p>{msg.content}</p>
                                        </div>
                                    </div>
                                ))}
                                <div ref={conversationEndRef} />
                            </div>
                            
                            {assessmentStatus.active && (
                                <div className="message-input">
                                    {assessmentStatus.input_mode === 'keyboard' ? (
                                        <form onSubmit={handleSendMessage}>
                                            <input
                                                type="text"
                                                value={userMessage}
                                                onChange={(e) => setUserMessage(e.target.value)}
                                                placeholder="Type your message here..."
                                                disabled={!assessmentStatus.active}
                                            />
                                            <button 
                                                type="submit"
                                                disabled={!userMessage.trim() || !assessmentStatus.active}
                                            >
                                                Send
                                            </button>
                                        </form>
                                    ) : (
                                        <div className="speech-input">
                                            <button 
                                                className={`speech-button ${listening ? 'listening' : ''}`}
                                                onClick={handleSpeechRecognition}
                                                disabled={!assessmentStatus.active || listening}
                                            >
                                                {listening ? 'Listening...' : 'Press to Speak'}
                                            </button>
                                        </div>
                                    )}
                                </div>
                            )}
                            
                            {!assessmentStatus.active && assessmentStatus.result && (
                                <div className="assessment-complete">
                                    <h3>Assessment Complete!</h3>
                                    <p>Click "View Assessment Data" to see the results.</p>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}
            
            {activeTab === 'prompt' && (
                <div className="prompt-container">
                    <h2>System Prompt</h2>
                    <p className="prompt-description">
                        This prompt controls how the AI nurse behaves during conversations.
                    </p>
                    
                    {isEditing ? (
                        <React.Fragment>
                            <textarea
                                value={systemPrompt}
                                onChange={(e) => setSystemPrompt(e.target.value)}
                                rows={20}
                                className="prompt-editor"
                            />
                            <div className="prompt-actions">
                                <button 
                                    className="secondary-button"
                                    onClick={() => setIsEditing(false)}
                                    disabled={saving}
                                >
                                    Cancel
                                </button>
                                <button 
                                    className="primary-button"
                                    onClick={handleSavePrompt}
                                    disabled={saving}
                                >
                                    {saving ? 'Saving...' : 'Save Prompt'}
                                </button>
                            </div>
                            {savingMessage && <p className="success-message">{savingMessage}</p>}
                        </React.Fragment>
                    ) : (
                        <React.Fragment>
                            <div className="prompt-display">
                                <pre>{systemPrompt}</pre>
                            </div>
                            <div className="prompt-actions">
                                <button 
                                    className="primary-button"
                                    onClick={() => setIsEditing(true)}
                                >
                                    Edit Prompt
                                </button>
                            </div>
                        </React.Fragment>
                    )}
                </div>
            )}
            
            {activeTab === 'results' && (
                <div className="results-container">
                    <div className="results-sidebar">
                        <h2>Saved Assessments</h2>
                        {savedAssessments.length === 0 ? (
                            <p>No saved assessments found.</p>
                        ) : (
                            <ul className="assessment-list">
                                {savedAssessments.map((filename, index) => (
                                    <li 
                                        key={index} 
                                        className={selectedAssessment && filename.includes(selectedAssessment.patient_id) ? 'selected' : ''}
                                        onClick={() => loadAssessment(filename)}
                                    >
                                        {filename}
                                    </li>
                                ))}
                            </ul>
                        )}
                        
                        <button 
                            className="secondary-button refresh-button"
                            onClick={loadSavedAssessments}
                        >
                            Refresh List
                        </button>
                    </div>
                    
                    <div className="results-display">
                        {selectedAssessment ? (
                            <React.Fragment>
                                <h2>Assessment Results</h2>
                                <div className="result-header">
                                    <div>
                                        <h3>Patient ID: {selectedAssessment.patient_id}</h3>
                                        <p>Date: {new Date(selectedAssessment.timestamp).toLocaleString()}</p>
                                    </div>
                                    <div>
                                        <p className={`notification-level ${selectedAssessment.oncologist_notification_level}`}>
                                            Notification Level: {selectedAssessment.oncologist_notification_level.toUpperCase()}
                                        </p>
                                    </div>
                                </div>
                                
                                <div className="symptoms-container">
                                    <h3>Symptoms</h3>
                                    <div className="symptoms-grid">
                                        {Object.entries(selectedAssessment.symptoms).map(([name, data]) => (
                                            <div className="symptom-card" key={name}>
                                                <h4>{name.replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase())}</h4>
                                                <div className="ratings">
                                                    <div className="rating">
                                                        <span>Frequency:</span>
                                                        <span className="rating-value">{data.frequency_rating}/5</span>
                                                    </div>
                                                    <div className="rating">
                                                        <span>Severity:</span>
                                                        <span className="rating-value">{data.severity_rating}/5</span>
                                                    </div>
                                                </div>
                                                
                                                <div className="indicators">
                                                    <h5>Key Indicators:</h5>
                                                    <ul>
                                                        {data.key_indicators.map((indicator, i) => (
                                                            <li key={i}>"{indicator}"</li>
                                                        ))}
                                                    </ul>
                                                </div>
                                                
                                                {data.additional_notes && (
                                                    <div className="notes">
                                                        <h5>Notes:</h5>
                                                        <p>{data.additional_notes}</p>
                                                    </div>
                                                )}
                                                
                                                {name === 'pain' && data.location && (
                                                    <div className="notes">
                                                        <h5>Location:</h5>
                                                        <p>{data.location}</p>
                                                    </div>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                </div>
                                
                                <div className="additional-notes">
                                    {selectedAssessment.mood_assessment && (
                                        <div className="note-section">
                                            <h3>Mood Assessment</h3>
                                            <p>{selectedAssessment.mood_assessment}</p>
                                        </div>
                                    )}
                                    
                                    {selectedAssessment.conversation_notes && (
                                        <div className="note-section">
                                            <h3>Conversation Notes</h3>
                                            <p>{selectedAssessment.conversation_notes}</p>
                                        </div>
                                    )}
                                    
                                    {selectedAssessment.flag_for_oncologist && (
                                        <div className="note-section flag-section">
                                            <h3>Flagged for Oncologist</h3>
                                            <p>{selectedAssessment.flag_reason}</p>
                                        </div>
                                    )}
                                </div>
                                
                                <div className="raw-json">
                                    <h3>Raw JSON Data</h3>
                                    <pre>{JSON.stringify(selectedAssessment, null, 2)}</pre>
                                </div>
                            </React.Fragment>
                        ) : (
                            <div className="no-result-selected">
                                <h3>No Assessment Selected</h3>
                                <p>Please select an assessment from the list on the left.</p>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};

// Render the app with error boundary
try {
    console.log('Attempting to render App component');
    // Use createElement instead of JSX for the top-level component
    const appWithErrorBoundary = React.createElement(
        ErrorBoundary,
        null,
        React.createElement(App, null)
    );
    
    ReactDOM.render(
        appWithErrorBoundary, 
        document.getElementById('root')
    );
    console.log('App rendered successfully');
} catch (error) {
    console.error('Error rendering React app:', error);
    document.getElementById('root').innerHTML = `
        <div style="color: red; padding: 20px; margin: 20px; border: 1px solid red;">
            <h2>Error Rendering Application</h2>
            <p>${error.message}</p>
            <button onclick="window.location.reload()">Refresh Page</button>
        </div>
    `;
}
"""

    # CSS for the React app
    css_content = """
:root {
    --primary-color: #0066cc;
    --primary-light: #4d94ff;
    --primary-dark: #004c99;
    --secondary-color: #28a745;
    --secondary-light: #48c763;
    --secondary-dark: #1e7e34;
    --danger-color: #dc3545;
    --warning-color: #ffc107;
    --success-color: #28a745;
    --info-color: #17a2b8;
    --light-color: #f8f9fa;
    --dark-color: #343a40;
    --gray-color: #6c757d;
    --gray-light: #e9ecef;
    --gray-dark: #495057;
    --font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    --border-radius: 4px;
    --box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: var(--font-family);
    line-height: 1.6;
    color: var(--dark-color);
    background-color: #f5f5f5;
}

.app {
    max-width: 1200px;
    margin: 0 auto;
    padding: 20px;
}

/* Header styles */
.header {
    margin-bottom: 30px;
    padding-bottom: 15px;
    border-bottom: 1px solid var(--gray-light);
}

.header h1 {
    font-size: 2rem;
    color: var(--primary-color);
    margin-bottom: 15px;
}

.tabs {
    display: flex;
    gap: 10px;
}

.tabs button {
    padding: 10px 20px;
    background-color: transparent;
    border: none;
    border-radius: var(--border-radius);
    cursor: pointer;
    font-size: 1rem;
    font-weight: 500;
    color: var(--gray-color);
    transition: all 0.2s ease;
}

.tabs button:hover {
    background-color: var(--gray-light);
    color: var(--dark-color);
}

.tabs button.active {
    background-color: var(--primary-color);
    color: white;
}

/* Form styles */
.form-group {
    margin-bottom: 20px;
}

.form-group label {
    display: block;
    margin-bottom: 8px;
    font-weight: 500;
}

.form-group input,
.form-group select,
.form-group textarea {
    width: 100%;
    padding: 10px;
    border: 1px solid var(--gray-light);
    border-radius: var(--border-radius);
    font-size: 1rem;
    font-family: var(--font-family);
}

.form-group input:focus,
.form-group select:focus,
.form-group textarea:focus {
    outline: none;
    border-color: var(--primary-light);
    box-shadow: 0 0 0 3px rgba(0, 102, 204, 0.1);
}

/* Button styles */
button {
    cursor: pointer;
    font-size: 1rem;
    padding: 10px 20px;
    border-radius: var(--border-radius);
    transition: all 0.2s ease;
    border: none;
}

.primary-button {
    background-color: var(--primary-color);
    color: white;
}

.primary-button:hover {
    background-color: var(--primary-dark);
}

.secondary-button {
    background-color: var(--gray-light);
    color: var(--dark-color);
}

.secondary-button:hover {
    background-color: var(--gray-color);
    color: white;
}

button:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

/* Test setup styles */
.test-setup {
    background-color: white;
    padding: 30px;
    border-radius: var(--border-radius);
    box-shadow: var(--box-shadow);
    max-width: 600px;
    margin: 0 auto;
}

.test-setup h2 {
    margin-bottom: 20px;
    color: var(--primary-color);
}

/* Conversation styles */
.conversation-container {
    background-color: white;
    border-radius: var(--border-radius);
    box-shadow: var(--box-shadow);
    display: flex;
    flex-direction: column;
    height: 80vh;
}

.conversation-header {
    padding: 15px 20px;
    border-bottom: 1px solid var(--gray-light);
    background-color: var(--light-color);
    border-top-left-radius: var(--border-radius);
    border-top-right-radius: var(--border-radius);
}

.conversation-header h2 {
    color: var(--primary-color);
    margin-bottom: 5px;
}

.conversation-actions {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
    margin-top: 10px;
}

.conversation-messages {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
}

.message {
    margin-bottom: 15px;
    display: flex;
    flex-direction: column;
}

.user-message {
    align-items: flex-end;
}

.assistant-message {
    align-items: flex-start;
}

.message-content {
    max-width: 80%;
    padding: 12px 16px;
    border-radius: 18px;
    box-shadow: var(--box-shadow);
}

.user-message .message-content {
    background-color: var(--primary-light);
    color: white;
    border-bottom-right-radius: 4px;
}

.assistant-message .message-content {
    background-color: var(--gray-light);
    color: var(--dark-color);
    border-bottom-left-radius: 4px;
}

.message-input {
    padding: 15px;
    border-top: 1px solid var(--gray-light);
}

.message-input form {
    display: flex;
    gap: 10px;
}

.message-input input {
    flex: 1;
    padding: 12px 16px;
    border: 1px solid var(--gray-light);
    border-radius: 20px;
    font-size: 1rem;
}

.message-input input:focus {
    outline: none;
    border-color: var(--primary-light);
}

.message-input button {
    background-color: var(--primary-color);
    color: white;
    border-radius: 20px;
    padding: 8px 20px;
}

.message-input button:hover {
    background-color: var(--primary-dark);
}

.speech-input {
    display: flex;
    justify-content: center;
    padding: 15px;
}

.speech-button {
    background-color: var(--primary-color);
    color: white;
    border-radius: 50%;
    width: 80px;
    height: 80px;
    font-weight: bold;
    transition: all 0.2s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    line-height: 1.2;
    padding: 0;
}

.speech-button:hover:not(:disabled) {
    background-color: var(--primary-dark);
    transform: scale(1.05);
}

.speech-button.listening {
    background-color: var(--danger-color);
    animation: pulse 1.5s infinite;
}

@keyframes pulse {
    0% {
        transform: scale(0.95);
        box-shadow: 0 0 0 0 rgba(220, 53, 69, 0.7);
    }
    
    70% {
        transform: scale(1);
        box-shadow: 0 0 0 10px rgba(220, 53, 69, 0);
    }
    
    100% {
        transform: scale(0.95);
        box-shadow: 0 0 0 0 rgba(220, 53, 69, 0);
    }
}

.assessment-complete {
    padding: 20px;
    text-align: center;
    background-color: var(--gray-light);
    border-radius: var(--border-radius);
    margin: 20px;
}

.assessment-complete h3 {
    color: var(--success-color);
    margin-bottom: 10px;
}

/* Prompt styles */
.prompt-container {
    background-color: white;
    padding: 30px;
    border-radius: var(--border-radius);
    box-shadow: var(--box-shadow);
}

.prompt-container h2 {
    color: var(--primary-color);
    margin-bottom: 10px;
}

.prompt-description {
    margin-bottom: 20px;
    color: var(--gray-color);
}

.prompt-editor {
    width: 100%;
    min-height: 300px;
    font-family: monospace;
    padding: 15px;
    border: 1px solid var(--gray-light);
    border-radius: var(--border-radius);
    font-size: 14px;
    line-height: 1.5;
    margin-bottom: 20px;
    resize: vertical;
}

.prompt-editor:focus {
    outline: none;
    border-color: var(--primary-light);
}

.prompt-display {
    background-color: var(--gray-light);
    padding: 20px;
    border-radius: var(--border-radius);
    margin-bottom: 20px;
    overflow-x: auto;
}

.prompt-display pre {
    font-family: monospace;
    white-space: pre-wrap;
    font-size: 14px;
    line-height: 1.5;
}

.prompt-actions {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
}

.success-message {
    color: var(--success-color);
    margin-top: 10px;
    text-align: center;
}

/* Results styles */
.results-container {
    display: flex;
    gap: 20px;
    height: 80vh;
}

.results-sidebar {
    width: 300px;
    background-color: white;
    padding: 20px;
    border-radius: var(--border-radius);
    box-shadow: var(--box-shadow);
    overflow-y: auto;
}

.results-sidebar h2 {
    color: var(--primary-color);
    margin-bottom: 15px;
}

.assessment-list {
    list-style: none;
    margin-bottom: 20px;
}

.assessment-list li {
    padding: 10px;
    border-radius: var(--border-radius);
    margin-bottom: 5px;
    cursor: pointer;
    transition: all 0.2s ease;
    font-size: 0.9rem;
}

.assessment-list li:hover {
    background-color: var(--gray-light);
}

.assessment-list li.selected {
    background-color: var(--primary-light);
    color: white;
}

.refresh-button {
    width: 100%;
}

.results-display {
    flex: 1;
    background-color: white;
    padding: 30px;
    border-radius: var(--border-radius);
    box-shadow: var(--box-shadow);
    overflow-y: auto;
}

.results-display h2 {
    color: var(--primary-color);
    margin-bottom: 20px;
}

.result-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 30px;
    padding-bottom: 15px;
    border-bottom: 1px solid var(--gray-light);
}

.result-header h3 {
    margin-bottom: 5px;
}

.notification-level {
    display: inline-block;
    padding: 5px 10px;
    border-radius: var(--border-radius);
    font-weight: 600;
}

.notification-level.none {
    background-color: var(--success-color);
    color: white;
}

.notification-level.amber {
    background-color: var(--warning-color);
    color: var(--dark-color);
}

.notification-level.red {
    background-color: var(--danger-color);
    color: white;
}

.symptoms-container {
    margin-bottom: 30px;
}

.symptoms-container h3 {
    margin-bottom: 15px;
    color: var(--primary-dark);
}

.symptoms-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 20px;
}

.symptom-card {
    background-color: var(--gray-light);
    padding: 20px;
    border-radius: var(--border-radius);
    box-shadow: var(--box-shadow);
}

.symptom-card h4 {
    color: var(--primary-color);
    margin-bottom: 15px;
    border-bottom: 2px solid var(--primary-light);
    padding-bottom: 5px;
}

.ratings {
    display: flex;
    justify-content: space-between;
    margin-bottom: 15px;
}

.rating {
    display: flex;
    flex-direction: column;
}

.rating-value {
    font-weight: 600;
    font-size: 1.2rem;
    color: var(--primary-color);
}

.indicators {
    margin-bottom: 15px;
}

.indicators h5,
.notes h5 {
    margin-bottom: 5px;
    color: var(--primary-dark);
}

.indicators ul {
    list-style: none;
}

.indicators li {
    margin-bottom: 5px;
    font-style: italic;
    padding-left: 10px;
    border-left: 3px solid var(--primary-light);
}

.additional-notes {
    margin-top: 30px;
}

.note-section {
    background-color: var(--gray-light);
    padding: 20px;
    border-radius: var(--border-radius);
    margin-bottom: 15px;
}

.note-section h3 {
    color: var(--primary-dark);
    margin-bottom: 10px;
}

.flag-section {
    background-color: rgba(220, 53, 69, 0.1);
    border-left: 5px solid var(--danger-color);
}

.raw-json {
    margin-top: 30px;
}

.raw-json h3 {
    margin-bottom: 15px;
    color: var(--primary-dark);
}

.raw-json pre {
    background-color: var(--gray-light);
    padding: 20px;
    border-radius: var(--border-radius);
    overflow-x: auto;
    font-family: monospace;
    font-size: 0.9rem;
    line-height: 1.5;
}

.no-result-selected {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--gray-color);
}

.no-result-selected h3 {
    margin-bottom: 10px;
}

/* Error message styles */
.error-message {
    background-color: rgba(220, 53, 69, 0.1);
    border-left: 5px solid var(--danger-color);
    padding: 15px;
    margin-bottom: 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.error-message p {
    color: var(--danger-color);
    font-weight: 500;
}

.error-message button {
    background-color: transparent;
    color: var(--danger-color);
    padding: 5px 10px;
    font-size: 0.9rem;
}

.error-message button:hover {
    text-decoration: underline;
}

/* Loading styles */
.loading-overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: rgba(255, 255, 255, 0.7);
    display: flex;
    justify-content: center;
    align-items: center;
    z-index: 1000;
}

.loading-spinner {
    width: 50px;
    height: 50px;
    border: 5px solid var(--gray-light);
    border-top: 5px solid var(--primary-color);
    border-radius: 50%;
    animation: spin 1s linear infinite;
}

@keyframes spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
}

/* Responsive styles */
@media (max-width: 768px) {
    .app {
        padding: 10px;
    }
    
    .results-container {
        flex-direction: column;
        height: auto;
    }
    
    .results-sidebar {
        width: 100%;
        margin-bottom: 20px;
    }
    
    .symptoms-grid {
        grid-template-columns: 1fr;
    }
    
    .tabs {
        flex-wrap: wrap;
    }
    
    .tabs button {
        flex: 1;
        min-width: 120px;
    }
    
    .result-header {
        flex-direction: column;
    }
    
    .result-header div:last-child {
        margin-top: 10px;
    }
}
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