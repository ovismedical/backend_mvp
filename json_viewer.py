# json_viewer.py
from http.server import HTTPServer, SimpleHTTPRequestHandler
import os
import json
import sys

# Create a directory for static files if it doesn't exist
if not os.path.exists('static'):
    os.makedirs('static')

# Create the HTML file
html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>OnCallLogist Symptom Assessment</title>
    <link rel="stylesheet" href="style.css">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
</head>
<body>
    <div class="container">
        <header>
            <h1>OnCallLogist</h1>
            <div class="patient-info">
                <p>Patient ID: <span id="patient-id"></span></p>
                <p>Date: <span id="assessment-date"></span></p>
                <p>Status: <span id="treatment-status"></span></p>
            </div>
        </header>
        
        <div class="notification-banner" id="notification-banner">
            <div class="notification-content">
                <p><strong>Notification: <span id="notification-level"></span></strong></p>
                <p id="flag-reason"></p>
            </div>
        </div>
        
        <main>
            <div class="symptoms-container">
                <h2>Symptom Assessment</h2>
                <div class="symptom-cards" id="symptom-cards">
                    <!-- Symptom cards will be inserted here by JavaScript -->
                </div>
            </div>
            
            <div class="notes-section">
                <h2>Additional Notes</h2>
                <div class="notes-box">
                    <h3>Mood Assessment</h3>
                    <p id="mood-assessment"></p>
                </div>
                <div class="notes-box">
                    <h3>Conversation Notes</h3>
                    <p id="conversation-notes"></p>
                </div>
            </div>
        </main>
        
        <footer>
            <p>OnCallLogist &copy; 2025</p>
        </footer>
    </div>

    <script src="script.js"></script>
</body>
</html>"""

# Create the CSS file
css_content = """
:root {
    --primary-color: #b05886;
    --secondary-color: #4abe9b;
    --bg-color: #fce8e5;
    --text-color: #333;
    --card-bg: #fff;
    --header-bg: #f8d7d7;
    --amber-color: #ffc107;
    --red-color: #dc3545;
    --none-color: #28a745;
    --border-radius: 12px;
    --shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen-Sans, Ubuntu, Cantarell, sans-serif;
    -webkit-tap-highlight-color: transparent;
}

html {
    font-size: 16px;
}

body {
    background-color: var(--bg-color);
    color: var(--text-color);
    line-height: 1.6;
    padding-bottom: env(safe-area-inset-bottom);
}

.container {
    width: 100%;
    max-width: 1200px;
    margin: 0 auto;
    padding: 15px;
}

header {
    background-color: var(--header-bg);
    border-radius: var(--border-radius);
    padding: 16px;
    margin-bottom: 16px;
    box-shadow: var(--shadow);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 16px;
}

header h1 {
    color: var(--primary-color);
    font-size: 1.75rem;
    margin-bottom: 0;
    flex: 1;
    min-width: 200px;
}

.patient-info {
    background-color: var(--card-bg);
    padding: 12px;
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
    font-size: 0.9rem;
    flex: 1;
    min-width: 200px;
}

.patient-info p {
    margin-bottom: 4px;
}

.notification-banner {
    padding: 16px;
    border-radius: var(--border-radius);
    margin-bottom: 16px;
    color: white;
}

.notification-content {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.notification-banner.amber {
    background-color: var(--amber-color);
}

.notification-banner.red {
    background-color: var(--red-color);
}

.notification-banner.none {
    background-color: var(--none-color);
}

.notification-banner p {
    margin: 0;
    line-height: 1.4;
}

main {
    margin-top: 16px;
}

h2 {
    font-size: 1.4rem;
    margin-bottom: 16px;
    color: var(--primary-color);
}

.symptom-cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
}

.symptom-card {
    background-color: var(--card-bg);
    border-radius: var(--border-radius);
    padding: 16px;
    box-shadow: var(--shadow);
    transition: transform 0.2s ease;
    touch-action: manipulation;
}

.symptom-card:active {
    transform: scale(0.98);
}

.symptom-card h3 {
    color: var(--primary-color);
    margin-bottom: 12px;
    border-bottom: 2px solid var(--secondary-color);
    padding-bottom: 8px;
    font-size: 1.2rem;
}

.rating-container {
    display: flex;
    justify-content: space-between;
    margin-bottom: 16px;
    flex-wrap: wrap;
    gap: 12px;
}

.rating {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    min-width: 100px;
}

.rating-label {
    font-size: 0.85rem;
    margin-bottom: 4px;
}

.rating-value {
    font-size: 1.4rem;
    font-weight: bold;
    color: var(--primary-color);
    margin-bottom: 6px;
}

.rating-scale {
    display: flex;
    align-items: center;
}

.scale-point {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    margin: 0 2px;
    background-color: #e0e0e0;
}

@media (min-width: 768px) {
    .scale-point {
        width: 12px;
        height: 12px;
    }
}

.scale-point.active {
    background-color: var(--primary-color);
}

.indicators {
    margin-top: 16px;
}

.indicators h4 {
    color: var(--secondary-color);
    margin-bottom: 8px;
    font-size: 1rem;
}

.indicators ul {
    list-style-type: none;
    padding-left: 8px;
}

.indicators li {
    padding: 6px 0;
    font-style: italic;
    border-left: 3px solid var(--secondary-color);
    padding-left: 10px;
    margin-bottom: 8px;
    font-size: 0.9rem;
    line-height: 1.5;
}

.additional-notes {
    margin-top: 16px;
    padding-top: 12px;
    border-top: 1px dashed #ccc;
}

.additional-notes h4 {
    color: var(--secondary-color);
    margin-bottom: 6px;
    font-size: 1rem;
}

.additional-notes p {
    font-size: 0.9rem;
    line-height: 1.5;
}

.notes-section {
    margin-top: 24px;
}

.notes-box {
    background-color: var(--card-bg);
    border-radius: var(--border-radius);
    padding: 16px;
    margin-bottom: 16px;
    box-shadow: var(--shadow);
}

.notes-box h3 {
    color: var(--primary-color);
    margin-bottom: 10px;
    border-bottom: 2px solid var(--secondary-color);
    padding-bottom: 8px;
    font-size: 1.1rem;
}

.notes-box p {
    font-size: 0.95rem;
    line-height: 1.6;
}

footer {
    text-align: center;
    margin-top: 24px;
    padding: 16px;
    color: #888;
    font-size: 0.85rem;
}

/* Improved file selector styling */
#file-selector {
    background-color: var(--card-bg);
    border-radius: var(--border-radius);
    padding: 20px;
    box-shadow: var(--shadow);
}

#file-selector h2 {
    margin-bottom: 16px;
}

#file-list ul {
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 12px;
}

#file-list a {
    display: block;
    background-color: var(--header-bg);
    color: var(--primary-color);
    padding: 16px;
    border-radius: 8px;
    text-decoration: none;
    font-weight: 500;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    transition: all 0.2s ease;
}

#file-list a:hover,
#file-list a:active {
    background-color: #f3c2c2;
    transform: translateY(-2px);
}

/* Mobile optimizations */
@media (max-width: 768px) {
    html {
        font-size: 15px;
    }
    
    .container {
        padding: 10px;
    }
    
    header {
        flex-direction: column;
        align-items: stretch;
    }
    
    header h1 {
        text-align: center;
        margin-bottom: 8px;
    }
    
    .symptom-cards {
        grid-template-columns: 1fr;
    }
    
    .rating-container {
        justify-content: flex-start;
        gap: 24px;
    }
}

/* Small mobile optimizations */
@media (max-width: 375px) {
    html {
        font-size: 14px;
    }
    
    .container {
        padding: 8px;
    }
    
    .symptom-card {
        padding: 12px;
    }
    
    .indicators li {
        font-size: 0.85rem;
    }
}

/* Handle iPhone notch and dynamic island */
@supports (padding-top: env(safe-area-inset-top)) {
    body {
        padding-top: env(safe-area-inset-top);
        padding-left: env(safe-area-inset-left);
        padding-right: env(safe-area-inset-right);
    }
}
"""

# Create the JavaScript file
js_content = """
// Function to load and display JSON data
function loadAssessmentData() {
    // Get URL parameter for the JSON file
    const urlParams = new URLSearchParams(window.location.search);
    const jsonFile = urlParams.get('file');
    
    if (!jsonFile) {
        // Show file selector if no file specified
        document.body.innerHTML = `
            <div class="container">
                <header>
                    <h1>OnCallLogist</h1>
                </header>
                <div id="file-selector">
                    <h2>Select an assessment file:</h2>
                    <div id="file-list">Loading files...</div>
                </div>
            </div>
        `;
        
        // Load available JSON files
        fetch('/list-json-files')
            .then(response => response.json())
            .then(files => {
                const fileList = document.getElementById('file-list');
                if (files.length === 0) {
                    fileList.innerHTML = '<p>No assessment files found.</p>';
                    return;
                }
                
                let html = '<ul>';
                files.forEach(file => {
                    html += `<li><a href="?file=${encodeURIComponent(file)}">${file}</a></li>`;
                });
                html += '</ul>';
                fileList.innerHTML = html;
            })
            .catch(error => {
                console.error('Error loading file list:', error);
                document.getElementById('file-list').innerHTML = '<p>Error loading files. Please try again.</p>';
            });
        
        return;
    }
    
    // Load the specified JSON file
    fetch(`/json/${jsonFile}`)
        .then(response => {
            if (!response.ok) {
                throw new Error('File not found');
            }
            return response.json();
        })
        .then(data => displayAssessmentData(data))
        .catch(error => {
            console.error('Error loading assessment data:', error);
            document.body.innerHTML = `
                <div class="container">
                    <header>
                        <h1>Error</h1>
                    </header>
                    <p>Could not load assessment file. <a href="/">Go back to file selection</a></p>
                </div>
            `;
        });
}

// Function to display the assessment data
function displayAssessmentData(data) {
    // Set patient info
    document.getElementById('patient-id').textContent = data.patient_id;
    
    // Format the date in a shorter way for mobile
    const date = new Date(data.timestamp);
    const formattedDate = formatDateForDisplay(date);
    document.getElementById('assessment-date').textContent = formattedDate;
    
    // Set treatment status with shorter text
    const statusText = data.treatment_status === 'undergoing_treatment' ? 
        'In Treatment' : 'In Remission';
    document.getElementById('treatment-status').textContent = statusText;
    
    // Set notification banner
    const notificationBanner = document.getElementById('notification-banner');
    document.getElementById('notification-level').textContent = 
        data.oncologist_notification_level.toUpperCase();
    
    notificationBanner.classList.add(data.oncologist_notification_level);
    
    if (data.flag_for_oncologist) {
        document.getElementById('flag-reason').textContent = data.flag_reason;
    } else {
        document.getElementById('flag-reason').textContent = 'No immediate attention required';
    }
    
    // Display symptom cards
    const symptomCardsContainer = document.getElementById('symptom-cards');
    
    for (const [symptomName, symptomData] of Object.entries(data.symptoms)) {
        // Create card HTML
        const formattedName = formatSymptomName(symptomName);
        
        let cardHTML = `
            <div class="symptom-card">
                <h3>${formattedName}</h3>
                <div class="rating-container">
                    <div class="rating">
                        <span class="rating-label">Frequency</span>
                        <span class="rating-value">${symptomData.frequency_rating}/5</span>
                        <div class="rating-scale">
                            ${createRatingScale(symptomData.frequency_rating)}
                        </div>
                    </div>
                    <div class="rating">
                        <span class="rating-label">Severity</span>
                        <span class="rating-value">${symptomData.severity_rating}/5</span>
                        <div class="rating-scale">
                            ${createRatingScale(symptomData.severity_rating)}
                        </div>
                    </div>
                </div>
                
                <div class="indicators">
                    <h4>Key Indicators:</h4>
                    <ul>
                        ${symptomData.key_indicators.map(indicator => `<li>"${indicator}"</li>`).join('')}
                    </ul>
                </div>
        `;
        
        // Add location for pain if available
        if (symptomName === 'pain' && symptomData.location) {
            cardHTML += `
                <div class="additional-notes">
                    <h4>Location:</h4>
                    <p>${symptomData.location}</p>
                </div>
            `;
        }
        
        // Add additional notes if available
        if (symptomData.additional_notes) {
            cardHTML += `
                <div class="additional-notes">
                    <h4>Notes:</h4>
                    <p>${symptomData.additional_notes}</p>
                </div>
            `;
        }
        
        // Close the card
        cardHTML += `</div>`;
        
        // Add card to container
        symptomCardsContainer.innerHTML += cardHTML;
    }
    
    // Set mood assessment and conversation notes
    document.getElementById('mood-assessment').textContent = data.mood_assessment || 'No mood assessment provided';
    document.getElementById('conversation-notes').textContent = data.conversation_notes || 'No additional notes';
    
    // Add touch event listeners for mobile
    setupMobileInteractions();
}

// Helper function to create rating scale visualization
function createRatingScale(rating) {
    let scaleHTML = '';
    for (let i = 1; i <= 5; i++) {
        scaleHTML += `<div class="scale-point ${i <= rating ? 'active' : ''}"></div>`;
    }
    return scaleHTML;
}

// Format symptom name to be more readable
function formatSymptomName(name) {
    return name
        .replace(/_/g, ' ')
        .split(' ')
        .map(word => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
}

// Format date in a more mobile-friendly way
function formatDateForDisplay(date) {
    const today = new Date();
    
    // If it's today, just show the time
    if (date.toDateString() === today.toDateString()) {
        return `Today, ${date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}`;
    }
    
    // If it's yesterday
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) {
        return `Yesterday, ${date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}`;
    }
    
    // If it's within the last week, show day of week
    const oneWeekAgo = new Date(today);
    oneWeekAgo.setDate(today.getDate() - 7);
    if (date > oneWeekAgo) {
        const options = { weekday: 'short', hour: '2-digit', minute: '2-digit' };
        return date.toLocaleString([], options);
    }
    
    // Otherwise show short date
    return date.toLocaleString([], {
        month: 'short', 
        day: 'numeric',
        hour: '2-digit', 
        minute: '2-digit'
    });
}

// Add mobile-specific event listeners
function setupMobileInteractions() {
    // Add tap feedback to cards
    const cards = document.querySelectorAll('.symptom-card');
    cards.forEach(card => {
        card.addEventListener('touchstart', function() {
            this.style.transform = 'scale(0.98)';
        }, { passive: true });
        
        card.addEventListener('touchend', function() {
            this.style.transform = '';
        }, { passive: true });
    });
    
    // Add viewport height fix for mobile browsers
    function setViewportHeight() {
        const vh = window.innerHeight * 0.01;
        document.documentElement.style.setProperty('--vh', `${vh}px`);
    }
    
    setViewportHeight();
    window.addEventListener('resize', setViewportHeight);
}

// Load data when the page loads
document.addEventListener('DOMContentLoaded', loadAssessmentData);
"""

# Write the files to the static directory
with open('static/index.html', 'w') as f:
    f.write(html_content)

with open('static/style.css', 'w') as f:
    f.write(css_content)

with open('static/script.js', 'w') as f:
    f.write(js_content)

# Create a custom HTTP request handler
class JSONViewerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory='static', **kwargs)
    
    def do_GET(self):
        # Handle JSON file listing request
        if self.path == '/list-json-files':
            try:
                # Get all JSON files in the current directory
                json_files = [f for f in os.listdir('.') if f.endswith('.json') and 'patient_' in f]
                
                # Send the list as JSON
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(json_files).encode())
                return
            except Exception as e:
                self.send_error(500, str(e))
                return
        
        # Handle JSON file request
        elif self.path.startswith('/json/'):
            filename = self.path[6:]  # Remove '/json/' prefix
            try:
                with open(filename, 'r') as f:
                    json_data = f.read()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json_data.encode())
                return
            except FileNotFoundError:
                self.send_error(404, 'File not found')
                return
            except Exception as e:
                self.send_error(500, str(e))
                return
        
        # Default handler for static files
        return super().do_GET()

# Function to find an available port
def find_available_port(start_port, max_attempts=10):
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

# Function to start the server
def run_server(port=8000):
    try:
        server_address = ('', port)
        httpd = HTTPServer(server_address, JSONViewerHandler)
        print(f'Server running at http://localhost:{port}/')
        httpd.serve_forever()
    except OSError as e:
        if e.errno == 98:  # Address already in use
            print(f"Port {port} is already in use. Trying to find another available port...")
            available_port = find_available_port(port + 1)
            print(f"Found available port: {available_port}")
            run_server(available_port)
        else:
            # Other OSError, re-raise it
            raise

if __name__ == '__main__':
    try:
        # Get port from command line argument or use default
        port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
        run_server(port)
    except KeyboardInterrupt:
        print("\nServer stopped by user.")
    except Exception as e:
        print(f"Error starting server: {e}")
        sys.exit(1)