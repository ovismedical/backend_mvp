
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
