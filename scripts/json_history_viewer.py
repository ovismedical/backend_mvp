# json_history_viewer_web.py
import os
import json
import datetime
import calendar
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
import tempfile
import shutil

def create_json_history_viewer():
    """Create a web-based JSON history viewer for patient symptom history"""
    # Create a temporary directory for the web app
    web_dir = tempfile.mkdtemp()
    
    # Create HTML, CSS, and JavaScript files
    create_html_file(web_dir)
    create_css_file(web_dir)
    create_js_file(web_dir)
    
    # Collect and copy patient data
    copy_patient_data(web_dir)
    
    # Start the web server
    start_server(web_dir)

def create_html_file(directory):
    """Create the HTML file for the web app"""
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OnCallLogist Patient History Viewer</title>
    <link rel="stylesheet" href="styles.css">
    <!-- Include Chart.js for visualizations -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <!-- Include date-fns for date manipulation -->
    <script src="https://cdn.jsdelivr.net/npm/date-fns@2.30.0/index.min.js"></script>
</head>
<body>
    <header>
        <h1>OnCallLogist Patient History Viewer</h1>
    </header>
    
    <main>
        <div class="controls">
            <div class="patient-selector">
                <label for="patient-id">Patient ID:</label>
                <select id="patient-id"></select>
            </div>
            
            <div class="date-navigation">
                <button id="prev-period">◀ Previous</button>
                <button id="today">Today</button>
                <button id="next-period">Next ▶</button>
            </div>
            
            <div class="view-selector">
                <label for="view-type">View:</label>
                <select id="view-type">
                    <option value="week">Week</option>
                    <option value="month">Month</option>
                    <option value="year">Year</option>
                </select>
            </div>
        </div>
        
        <h2 id="date-range-title">Loading...</h2>
        
        <div class="content">
            <div class="calendar" id="calendar-container">
                <!-- Calendar will be generated here -->
            </div>
            
            <div class="details" id="details-container">
                <!-- Details will be shown here -->
            </div>
        </div>
    </main>
    
    <footer>
        <p>OnCallLogist App &copy; 2025</p>
    </footer>
    
    <!-- Modal for assessment details -->
    <div id="assessment-modal" class="modal">
        <div class="modal-content">
            <span class="close">&times;</span>
            <h2>Assessment Details</h2>
            <div id="modal-content"></div>
        </div>
    </div>
    
    <script src="app.js"></script>
</body>
</html>
"""
    
    with open(os.path.join(directory, 'index.html'), 'w') as f:
        f.write(html_content)

def create_css_file(directory):
    """Create the CSS file for the web app"""
    css_content = """
:root {
    --primary-color: #b05886;
    --secondary-color: #4abe9b;
    --bg-color: #fce8e5;
    --text-color: #333333;
    --header-bg: #f8d7d7;
    --low-severity: #28a745;
    --medium-severity: #ffc107;
    --high-severity: #dc3545;
    --calendar-bg: white;
    --border-color: #ddd;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
}

body {
    background-color: var(--bg-color);
    color: var(--text-color);
    line-height: 1.6;
}

header {
    background-color: var(--header-bg);
    padding: 1rem;
    text-align: center;
    box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);
}

header h1 {
    color: var(--primary-color);
}

main {
    max-width: 1200px;
    margin: 0 auto;
    padding: 1rem;
}

.controls {
    display: flex;
    justify-content: space-between;
    margin-bottom: 1rem;
    flex-wrap: wrap;
}

.controls > div {
    margin: 0.5rem;
}

select, button {
    padding: 0.5rem;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    background-color: white;
}

button {
    background-color: var(--primary-color);
    color: white;
    cursor: pointer;
    transition: background-color 0.3s;
}

button:hover {
    background-color: #8e4469;
}

#date-range-title {
    text-align: center;
    margin: 1rem 0;
    color: var(--primary-color);
}

.content {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
    margin-bottom: 2rem;
}

.calendar, .details {
    background-color: white;
    padding: 1rem;
    border-radius: 8px;
    box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);
}

/* Week View Styles */
.week-grid {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 5px;
}

.week-day-header {
    text-align: center;
    padding: 0.5rem;
    font-weight: bold;
    background-color: var(--header-bg);
    border-radius: 4px 4px 0 0;
}

.week-day {
    min-height: 100px;
    border: 1px solid var(--border-color);
    padding: 0.5rem;
    background-color: var(--calendar-bg);
}

.week-day.today {
    background-color: var(--header-bg);
}

.day-number {
    font-weight: bold;
    margin-bottom: 0.5rem;
}

.assessment-item {
    margin: 5px 0;
    padding: 5px;
    border-radius: 4px;
    cursor: pointer;
    color: white;
    text-align: center;
}

.severity-1 { background-color: #4caf50; color: black; }
.severity-2 { background-color: #8bc34a; color: black; }
.severity-3 { background-color: #ffc107; color: black; }
.severity-4 { background-color: #ff9800; color: white; }
.severity-5 { background-color: #f44336; color: white; }

/* Month View Styles */
.month-grid {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 5px;
}

.month-day-header {
    text-align: center;
    padding: 0.25rem;
    font-weight: bold;
    background-color: var(--header-bg);
}

.month-day {
    aspect-ratio: 1;
    border: 1px solid var(--border-color);
    padding: 0.25rem;
    background-color: var(--calendar-bg);
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.month-day.today {
    background-color: var(--header-bg);
}

.month-day.outside-month {
    background-color: #f5f5f5;
    color: #999;
}

.month-day-number {
    font-weight: bold;
}

.month-day-indicator {
    width: 100%;
    height: 10px;
    margin-top: auto;
}

/* Year View Styles */
.year-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
}

.month-card {
    border: 1px solid var(--border-color);
    border-radius: 4px;
    overflow: hidden;
}

.month-title {
    text-align: center;
    padding: 0.5rem;
    background-color: var(--header-bg);
    font-weight: bold;
}

.mini-month-grid {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    grid-auto-rows: 1fr;
    gap: 2px;
    padding: 5px;
}

.mini-month-day {
    width: 100%;
    aspect-ratio: 1;
    font-size: 0.7rem;
    display: flex;
    justify-content: center;
    align-items: center;
    border-radius: 50%;
}

.mini-month-day.assessment {
    color: white;
}

/* Trend Chart Styles */
.chart-container {
    width: 100%;
    margin-top: 1rem;
}

.chart-title {
    text-align: center;
    margin-bottom: 0.5rem;
    font-weight: bold;
}

/* Modal Styles */
.modal {
    display: none;
    position: fixed;
    z-index: 1000;
    left: 0;
    top: 0;
    width: 100%;
    height: 100%;
    background-color: rgba(0, 0, 0, 0.7);
}

.modal-content {
    background-color: white;
    margin: 10% auto;
    padding: 20px;
    border-radius: 8px;
    max-width: 600px;
    max-height: 80vh;
    overflow-y: auto;
}

.close {
    color: #aaa;
    float: right;
    font-size: 28px;
    font-weight: bold;
    cursor: pointer;
}

.close:hover {
    color: black;
}

.symptom-card {
    margin: 1rem 0;
    padding: 1rem;
    border: 1px solid var(--border-color);
    border-radius: 8px;
    background-color: #f9f9f9;
}

.symptom-name {
    color: var(--primary-color);
    font-weight: bold;
    margin-bottom: 0.5rem;
    border-bottom: 2px solid var(--secondary-color);
    padding-bottom: 0.25rem;
}

.symptom-ratings {
    display: flex;
    justify-content: space-between;
    margin-bottom: 0.5rem;
}

.symptom-rating {
    text-align: center;
}

.rating-label {
    font-size: 0.8rem;
    color: #666;
}

.rating-value {
    font-size: 1.2rem;
    font-weight: bold;
    color: var(--primary-color);
}

.rating-scale {
    display: flex;
    margin-top: 5px;
}

.scale-point {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    margin: 0 2px;
    background-color: #e0e0e0;
}

.scale-point.active {
    background-color: var(--primary-color);
}

.key-indicators {
    margin-top: 0.5rem;
}

.key-indicators h4 {
    color: var(--secondary-color);
    margin-bottom: 0.25rem;
}

.key-indicators ul {
    list-style-type: none;
    padding-left: 0.5rem;
}

.key-indicators li {
    padding: 0.25rem 0;
    font-style: italic;
    border-left: 3px solid var(--secondary-color);
    padding-left: 0.5rem;
    margin-bottom: 0.25rem;
}

.summary-panel {
    margin-top: 1rem;
    padding: 1rem;
    background-color: var(--header-bg);
    border-radius: 8px;
}

.summary-title {
    font-weight: bold;
    margin-bottom: 0.5rem;
}

.severity-legend {
    display: flex;
    margin-top: 1rem;
    justify-content: center;
}

.severity-item {
    display: flex;
    align-items: center;
    margin: 0 0.5rem;
}

.severity-color {
    width: 15px;
    height: 15px;
    border-radius: 50%;
    margin-right: 5px;
}

footer {
    text-align: center;
    padding: 1rem;
    background-color: var(--header-bg);
    margin-top: 2rem;
    color: #666;
}

/* Responsive styles */
@media (max-width: 768px) {
    .content {
        grid-template-columns: 1fr;
    }
    
    .controls {
        flex-direction: column;
    }
    
    .week-grid {
        font-size: 0.8rem;
    }
    
    .year-grid {
        grid-template-columns: repeat(2, 1fr);
    }
}
"""
    
    with open(os.path.join(directory, 'styles.css'), 'w') as f:
        f.write(css_content)

def create_js_file(directory):
    """Create the JavaScript file for the web app"""
    js_content = """
// Global variables
let allPatientData = {};
let selectedPatientId = null;
let currentDate = new Date();
let currentView = "week";

// DOM elements
const patientSelector = document.getElementById('patient-id');
const viewTypeSelector = document.getElementById('view-type');
const prevButton = document.getElementById('prev-period');
const nextButton = document.getElementById('next-period');
const todayButton = document.getElementById('today');
const dateRangeTitle = document.getElementById('date-range-title');
const calendarContainer = document.getElementById('calendar-container');
const detailsContainer = document.getElementById('details-container');
const modal = document.getElementById('assessment-modal');
const modalClose = document.querySelector('.close');
const modalContent = document.getElementById('modal-content');

// Initialize the app when the page loads
document.addEventListener('DOMContentLoaded', initApp);

// Event listeners
patientSelector.addEventListener('change', onPatientChange);
viewTypeSelector.addEventListener('change', onViewChange);
prevButton.addEventListener('click', previousPeriod);
nextButton.addEventListener('click', nextPeriod);
todayButton.addEventListener('click', goToToday);
modalClose.addEventListener('click', closeModal);
window.addEventListener('click', event => {
    if (event.target === modal) {
        closeModal();
    }
});

// Initialize the application
async function initApp() {
    try {
        // Fetch list of patients
        const patientList = await fetchPatientList();
        populatePatientSelector(patientList);
        
        // If we have patients, load the first one
        if (patientList.length > 0) {
            selectedPatientId = patientList[0];
            patientSelector.value = selectedPatientId;
            await loadPatientData(selectedPatientId);
        }
        
        updateDisplay();
    } catch (error) {
        console.error('Error initializing app:', error);
        showError('Failed to initialize app. Please try again.');
    }
}

// Fetch the list of patient IDs from assessment files
async function fetchPatientList() {
    try {
        const response = await fetch('/api/patients');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Error fetching patient list:', error);
        return [];
    }
}

// Populate the patient selector dropdown
function populatePatientSelector(patientList) {
    patientSelector.innerHTML = '';
    
    patientList.forEach(patientId => {
        const option = document.createElement('option');
        option.value = patientId;
        option.textContent = `Patient ${patientId}`;
        patientSelector.appendChild(option);
    });
}

// Load assessment data for a specific patient
async function loadPatientData(patientId) {
    try {
        const response = await fetch(`/api/patient/${patientId}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        allPatientData[patientId] = await response.json();
        // Sort assessments by date
        allPatientData[patientId].sort((a, b) => 
            new Date(a.timestamp) - new Date(b.timestamp)
        );
        
        return allPatientData[patientId];
    } catch (error) {
        console.error(`Error loading data for patient ${patientId}:`, error);
        return [];
    }
}

// Handle patient change
async function onPatientChange() {
    selectedPatientId = patientSelector.value;
    
    // Load data if we don't have it yet
    if (!allPatientData[selectedPatientId]) {
        await loadPatientData(selectedPatientId);
    }
    
    updateDisplay();
}

// Handle view type change
function onViewChange() {
    currentView = viewTypeSelector.value;
    updateDisplay();
}

// Navigate to previous period
function previousPeriod() {
    if (currentView === 'week') {
        currentDate = new Date(currentDate.getTime() - 7 * 24 * 60 * 60 * 1000);
    } else if (currentView === 'month') {
        currentDate = new Date(currentDate.getFullYear(), currentDate.getMonth() - 1, 1);
    } else if (currentView === 'year') {
        currentDate = new Date(currentDate.getFullYear() - 1, 0, 1);
    }
    
    updateDisplay();
}

// Navigate to next period
function nextPeriod() {
    if (currentView === 'week') {
        currentDate = new Date(currentDate.getTime() + 7 * 24 * 60 * 60 * 1000);
    } else if (currentView === 'month') {
        currentDate = new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 1);
    } else if (currentView === 'year') {
        currentDate = new Date(currentDate.getFullYear() + 1, 0, 1);
    }
    
    updateDisplay();
}

// Go to today
function goToToday() {
    currentDate = new Date();
    updateDisplay();
}

// Update the display based on current view and date
function updateDisplay() {
    if (!selectedPatientId || !allPatientData[selectedPatientId]) {
        showError('No patient data available');
        return;
    }
    
    updateDateRangeTitle();
    
    if (currentView === 'week') {
        renderWeekView();
    } else if (currentView === 'month') {
        renderMonthView();
    } else if (currentView === 'year') {
        renderYearView();
    }
}

// Update the date range title
function updateDateRangeTitle() {
    let title = '';
    
    if (currentView === 'week') {
        // Get start of week (Monday)
        const startOfWeek = new Date(currentDate);
        startOfWeek.setDate(currentDate.getDate() - currentDate.getDay() + (currentDate.getDay() === 0 ? -6 : 1));
        
        // Get end of week (Sunday)
        const endOfWeek = new Date(startOfWeek);
        endOfWeek.setDate(startOfWeek.getDate() + 6);
        
        title = `Week of ${formatDate(startOfWeek, 'MMM d')} - ${formatDate(endOfWeek, 'MMM d, yyyy')}`;
    } else if (currentView === 'month') {
        title = formatDate(currentDate, 'MMMM yyyy');
    } else if (currentView === 'year') {
        title = formatDate(currentDate, 'yyyy');
    }
    
    dateRangeTitle.textContent = title;
}

// Render week view
function renderWeekView() {
    // Clear containers
    calendarContainer.innerHTML = '';
    detailsContainer.innerHTML = '';
    
    // Get start of week (Monday)
    const startOfWeek = getStartOfWeek(currentDate);
    
    // Create week grid
    const weekGrid = document.createElement('div');
    weekGrid.className = 'week-grid';
    
    // Add day headers
    const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    dayNames.forEach(day => {
        const dayHeader = document.createElement('div');
        dayHeader.className = 'week-day-header';
        dayHeader.textContent = day;
        weekGrid.appendChild(dayHeader);
    });
    
    // Get assessments for the week
    const weekAssessments = getAssessmentsForDateRange(
        startOfWeek,
        new Date(startOfWeek.getTime() + 6 * 24 * 60 * 60 * 1000)
    );
    
    // Add day cells
    for (let i = 0; i < 7; i++) {
        const cellDate = new Date(startOfWeek);
        cellDate.setDate(startOfWeek.getDate() + i);
        
        const dayCell = document.createElement('div');
        dayCell.className = 'week-day';
        if (isSameDay(cellDate, new Date())) {
            dayCell.classList.add('today');
        }
        
        // Add day number
        const dayNumber = document.createElement('div');
        dayNumber.className = 'day-number';
        dayNumber.textContent = cellDate.getDate();
        dayCell.appendChild(dayNumber);
        
        // Add assessments for this day
        const dayAssessments = weekAssessments.filter(a => 
            isSameDay(new Date(a.timestamp), cellDate)
        );
        
        dayAssessments.forEach(assessment => {
            const highestSeverity = getHighestSeverity(assessment);
            
            const assessmentItem = document.createElement('div');
            assessmentItem.className = `assessment-item severity-${highestSeverity}`;
            
            const time = new Date(assessment.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            assessmentItem.textContent = `${time} - Assessment`;
            
            assessmentItem.addEventListener('click', () => {
                showAssessmentDetails(assessment);
            });
            
            dayCell.appendChild(assessmentItem);
        });
        
        weekGrid.appendChild(dayCell);
    }
    
    calendarContainer.appendChild(weekGrid);
    
    // Create trend chart for the week
    createTrendChart(weekAssessments);
}

// Render month view
function renderMonthView() {
    // Clear containers
    calendarContainer.innerHTML = '';
    detailsContainer.innerHTML = '';
    
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();
    
    // Get first day of month and last day
    const firstDay = new Date(year, month, 1);
    const lastDay = new Date(year, month + 1, 0);
    
    // Get first day of calendar (might be in previous month)
    const firstDayOfCalendar = new Date(firstDay);
    const dayOfWeek = firstDay.getDay() || 7; // Convert Sunday (0) to 7
    firstDayOfCalendar.setDate(firstDay.getDate() - (dayOfWeek - 1));
    
    // Get assessments for the month
    const monthAssessments = getAssessmentsForDateRange(firstDay, lastDay);
    
    // Create month grid
    const monthGrid = document.createElement('div');
    monthGrid.className = 'month-grid';
    
    // Add day headers
    const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    dayNames.forEach(day => {
        const dayHeader = document.createElement('div');
        dayHeader.className = 'month-day-header';
        dayHeader.textContent = day;
        monthGrid.appendChild(dayHeader);
    });
    
    // Add day cells (6 weeks x 7 days)
    const today = new Date();
    let currentDateInGrid = new Date(firstDayOfCalendar);
    
    // Group assessments by date
    const assessmentsByDate = {};
    monthAssessments.forEach(assessment => {
        const date = new Date(assessment.timestamp).toISOString().split('T')[0];
        if (!assessmentsByDate[date]) {
            assessmentsByDate[date] = [];
        }
        assessmentsByDate[date].push(assessment);
    });
    
    for (let i = 0; i < 42; i++) {
        const isCurrentMonth = currentDateInGrid.getMonth() === month;
        
        const dayCell = document.createElement('div');
        dayCell.className = 'month-day';
        if (!isCurrentMonth) {
            dayCell.classList.add('outside-month');
        }
        if (isSameDay(currentDateInGrid, today)) {
            dayCell.classList.add('today');
        }
        
        // Add day number
        const dayNumber = document.createElement('div');
        dayNumber.className = 'month-day-number';
        dayNumber.textContent = currentDateInGrid.getDate();
        dayCell.appendChild(dayNumber);
        
        // Add indicator if there are assessments on this day
        const dateStr = currentDateInGrid.toISOString().split('T')[0];
        if (assessmentsByDate[dateStr] && assessmentsByDate[dateStr].length > 0) {
            // Find highest severity for this day
            let highestSeverity = 0;
            assessmentsByDate[dateStr].forEach(assessment => {
                const severity = getHighestSeverity(assessment);
                if (severity > highestSeverity) {
                    highestSeverity = severity;
                }
            });
            
            const indicator = document.createElement('div');
            indicator.className = `month-day-indicator severity-${highestSeverity}`;
            dayCell.appendChild(indicator);
            
            // Make day clickable
            dayCell.addEventListener('click', () => {
                showDayDetails(dateStr, assessmentsByDate[dateStr]);
            });
            dayCell.style.cursor = 'pointer';
        }
        
        monthGrid.appendChild(dayCell);
        
        // Move to next day
        currentDateInGrid.setDate(currentDateInGrid.getDate() + 1);
    }
    
    calendarContainer.appendChild(monthGrid);
    
    // Create monthly summary
    createMonthlySummary(monthAssessments);
}

// Render year view
function renderYearView() {
    // Clear containers
    calendarContainer.innerHTML = '';
    detailsContainer.innerHTML = '';
    
    const year = currentDate.getFullYear();
    
    // Get assessments for the year
    const yearAssessments = getAssessmentsForDateRange(
        new Date(year, 0, 1),
        new Date(year, 11, 31)
    );
    
    // Create year grid with 4 columns x 3 rows
    const yearGrid = document.createElement('div');
    yearGrid.className = 'year-grid';
    
    // Process assessments by month
    const assessmentsByMonth = {};
    for (let month = 0; month < 12; month++) {
        assessmentsByMonth[month] = {
            count: 0,
            assessments: []
        };
    }
    
    yearAssessments.forEach(assessment => {
        const month = new Date(assessment.timestamp).getMonth();
        assessmentsByMonth[month].count++;
        assessmentsByMonth[month].assessments.push(assessment);
    });
    
    // Create a mini-calendar for each month
    const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                         'July', 'August', 'September', 'October', 'November', 'December'];
                         
    for (let month = 0; month < 12; month++) {
        const monthCard = document.createElement('div');
        monthCard.className = 'month-card';
        
        // Month title
        const monthTitle = document.createElement('div');
        monthTitle.className = 'month-title';
        monthTitle.textContent = monthNames[month];
        monthCard.appendChild(monthTitle);
        
        // Make the month clickable to switch to month view
        monthTitle.style.cursor = 'pointer';
        monthTitle.addEventListener('click', () => {
            currentDate = new Date(year, month, 1);
            currentView = 'month';
            viewTypeSelector.value = 'month';
            updateDisplay();
        });
        
        // Mini month grid
        const miniGrid = document.createElement('div');
        miniGrid.className = 'mini-month-grid';
        
        // Get first day of month and last day
        const firstDay = new Date(year, month, 1);
        const lastDay = new Date(year, month + 1, 0);
        
        // Get first day of calendar (might be in previous month)
        const firstDayOfCalendar = new Date(firstDay);
        const dayOfWeek = firstDay.getDay() || 7; // Convert Sunday (0) to 7
        firstDayOfCalendar.setDate(firstDay.getDate() - (dayOfWeek - 1));
        
        // Group assessments by date for this month
        const assessmentsByDate = {};
        assessmentsByMonth[month].assessments.forEach(assessment => {
            const date = new Date(assessment.timestamp).toISOString().split('T')[0];
            if (!assessmentsByDate[date]) {
                assessmentsByDate[date] = [];
            }
            assessmentsByDate[date].push(assessment);
        });
        
        // Add day cells
        let currentDateInGrid = new Date(firstDayOfCalendar);
        
        // First add day headers (Mo-Su)
        for (let i = 0; i < 7; i++) {
            const dayHeader = document.createElement('div');
            dayHeader.className = 'mini-month-day';
            dayHeader.style.fontSize = '0.6rem';
            dayHeader.textContent = ['M', 'T', 'W', 'T', 'F', 'S', 'S'][i];
            miniGrid.appendChild(dayHeader);
        }
        
        // Then add day cells
        for (let i = 0; i < 42; i++) {
            const isCurrentMonth = currentDateInGrid.getMonth() === month;
            
            const dayCell = document.createElement('div');
            dayCell.className = 'mini-month-day';
            if (!isCurrentMonth) {
                dayCell.style.color = '#ccc';
            }
            
            // Add date number
            dayCell.textContent = currentDateInGrid.getDate();
            
            // Check if there are assessments on this date
            const dateStr = currentDateInGrid.toISOString().split('T')[0];
            if (assessmentsByDate[dateStr] && assessmentsByDate[dateStr].length > 0) {
                // Find highest severity for this day
                let highestSeverity = 0;
                assessmentsByDate[dateStr].forEach(assessment => {
                    const severity = getHighestSeverity(assessment);
                    if (severity > highestSeverity) {
                        highestSeverity = severity;
                    }
                });
                
                dayCell.classList.add('assessment', `severity-${highestSeverity}`);
                
                // Make day clickable
                dayCell.addEventListener('click', () => {
                    currentDate = new Date(currentDateInGrid);
                    currentView = 'day';
                    updateDisplay();
                });
                dayCell.style.cursor = 'pointer';
            }
            
            miniGrid.appendChild(dayCell);
            
            // Move to next day
            currentDateInGrid.setDate(currentDateInGrid.getDate() + 1);
        }
        
        monthCard.appendChild(miniGrid);
        yearGrid.appendChild(monthCard);
    }
    
    calendarContainer.appendChild(yearGrid);
    
    // Create year summary chart
    createYearSummary(yearAssessments);
}

// Create the trend chart for week view
function createTrendChart(assessments) {
    if (assessments.length === 0) {
        detailsContainer.innerHTML = '<div class="no-data">No assessments for this period</div>';
        return;
    }
    
    // Create chart container
    const chartContainer = document.createElement('div');
    chartContainer.className = 'chart-container';
    
    // Add title
    const chartTitle = document.createElement('div');
    chartTitle.className = 'chart-title';
    chartTitle.textContent = 'Symptom Trend for the Week';
    chartContainer.appendChild(chartTitle);
    
    // Create canvas for the chart
    const canvas = document.createElement('canvas');
    chartContainer.appendChild(canvas);
    
    // Add to details container
    detailsContainer.appendChild(chartContainer);
    
    // Prepare data for the chart
    const symptomTypes = ['fatigue', 'pain', 'cough', 'nausea', 'lack_of_appetite'];
    const datasets = [];
    const labels = [];
    
    // Sort assessments by date
    assessments.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    
    // Create labels (dates)
    assessments.forEach(assessment => {
        const date = new Date(assessment.timestamp);
        labels.push(formatDate(date, 'MMM d, HH:mm'));
    });
    
    // Create datasets for each symptom
    const colors = {
        fatigue: 'rgb(255, 99, 132)',
        pain: 'rgb(54, 162, 235)',
        cough: 'rgb(255, 206, 86)',
        nausea: 'rgb(75, 192, 192)',
        lack_of_appetite: 'rgb(153, 102, 255)'
    };
    
    symptomTypes.forEach(symptom => {
        const data = [];
        
        assessments.forEach(assessment => {
            if (assessment.symptoms && assessment.symptoms[symptom]) {
                data.push(assessment.symptoms[symptom].severity_rating);
            } else {
                data.push(null);
            }
        });
        
        // Only add dataset if there's at least one non-null value
        if (data.some(value => value !== null)) {
            datasets.push({
                label: symptom.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase()),
                data: data,
                borderColor: colors[symptom],
                backgroundColor: colors[symptom] + '20',
                tension: 0.1
            });
        }
    });
    
    // Create the chart
    new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            scales: {
                y: {
                    min: 0,
                    max: 6,
                    stepSize: 1,
                    title: {
                        display: true,
                        text: 'Severity (1-5)'
                    }
                }
            },
            plugins: {
                title: {
                    display: true,
                    text: 'Symptom Severity Trend'
                }
            }
        }
    });
    
    // Add severity legend
    const legendContainer = document.createElement('div');
    legendContainer.className = 'severity-legend';
    
    for (let level = 1; level <= 5; level++) {
        const item = document.createElement('div');
        item.className = 'severity-item';
        
        const color = document.createElement('div');
        color.className = 'severity-color';
        color.style.backgroundColor = getSeverityColor(level);
        item.appendChild(color);
        
        const label = document.createElement('span');
        label.textContent = getLevelDescription(level);
        item.appendChild(label);
        
        legendContainer.appendChild(item);
    }
    
    detailsContainer.appendChild(legendContainer);
}

// Create monthly summary
function createMonthlySummary(assessments) {
    if (assessments.length === 0) {
        detailsContainer.innerHTML = '<div class="no-data">No assessments for this month</div>';
        return;
    }
    
    // Create summary container
    const summaryContainer = document.createElement('div');
    summaryContainer.className = 'summary-panel';
    
    // Add title
    const title = document.createElement('h3');
    title.textContent = 'Monthly Summary';
    summaryContainer.appendChild(title);
    
    // Total assessments
    const totalAssessments = document.createElement('p');
    totalAssessments.textContent = `Total Assessments: ${assessments.length}`;
    summaryContainer.appendChild(totalAssessments);
    
    // Calculate symptom frequencies and average severities
    const symptomCounts = {};
    const symptomTotalSeverity = {};
    
    assessments.forEach(assessment => {
        for (const [symptom, data] of Object.entries(assessment.symptoms)) {
            if (!symptomCounts[symptom]) {
                symptomCounts[symptom] = 0;
                symptomTotalSeverity[symptom] = 0;
            }
            
            symptomCounts[symptom]++;
            symptomTotalSeverity[symptom] += data.severity_rating;
        }
    });
    
    // Create chart container
    const chartContainer = document.createElement('div');
    chartContainer.className = 'chart-container';
    
    // Create canvas for the chart
    const canvas = document.createElement('canvas');
    chartContainer.appendChild(canvas);
    
    // Add to summary container
    summaryContainer.appendChild(chartContainer);
    
    // Prepare data for the chart
    const symptoms = Object.keys(symptomCounts);
    const avgSeverities = symptoms.map(symptom => 
        symptomTotalSeverity[symptom] / symptomCounts[symptom]
    );
    
    // Create the chart
    new Chart(canvas, {
        type: 'bar',
        data: {
            labels: symptoms.map(s => s.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())),
            datasets: [{
                label: 'Average Severity',
                data: avgSeverities,
                backgroundColor: symptoms.map(s => 
                    getSeverityColor(Math.round(symptomTotalSeverity[s] / symptomCounts[s]))
                )
            }]
        },
        options: {
            responsive: true,
            scales: {
                y: {
                    min: 0,
                    max: 5,
                    stepSize: 1,
                    title: {
                        display: true,
                        text: 'Average Severity (1-5)'
                    }
                }
            },
            plugins: {
                title: {
                    display: true,
                    text: 'Average Symptom Severity'
                }
            }
        }
    });
    
    // Add average severity list
    const severityTitle = document.createElement('h4');
    severityTitle.textContent = 'Average Symptom Severity:';
    severityTitle.style.marginTop = '1rem';
    summaryContainer.appendChild(severityTitle);
    
    const severityList = document.createElement('ul');
    
    // Sort symptoms by severity
    const sortedSymptoms = [...symptoms].sort((a, b) => 
        (symptomTotalSeverity[b] / symptomCounts[b]) - 
        (symptomTotalSeverity[a] / symptomCounts[a])
    );
    
    sortedSymptoms.forEach(symptom => {
        const avgSeverity = symptomTotalSeverity[symptom] / symptomCounts[symptom];
        
        const item = document.createElement('li');
        item.textContent = `${symptom.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}: ${avgSeverity.toFixed(1)}/5`;
        severityList.appendChild(item);
    });
    
    summaryContainer.appendChild(severityList);
    
    detailsContainer.appendChild(summaryContainer);
}

// Create year summary
function createYearSummary(assessments) {
    if (assessments.length === 0) {
        detailsContainer.innerHTML = '<div class="no-data">No assessments for this year</div>';
        return;
    }
    
    const year = currentDate.getFullYear();
    
    // Create summary container
    const summaryContainer = document.createElement('div');
    summaryContainer.className = 'summary-panel';
    
    // Add title
    const title = document.createElement('h3');
    title.textContent = `${year} Annual Summary`;
    summaryContainer.appendChild(title);
    
    // Total assessments
    const totalAssessments = document.createElement('p');
    totalAssessments.textContent = `Total Assessments: ${assessments.length}`;
    summaryContainer.appendChild(totalAssessments);
    
    // Process data by month
    const monthlyData = Array(12).fill().map(() => ({
        count: 0,
        severities: {
            fatigue: [],
            pain: [],
            cough: [],
            nausea: [],
            lack_of_appetite: []
        }
    }));
    
    assessments.forEach(assessment => {
        const month = new Date(assessment.timestamp).getMonth();
        monthlyData[month].count++;
        
        for (const [symptom, data] of Object.entries(assessment.symptoms)) {
            if (monthlyData[month].severities[symptom]) {
                monthlyData[month].severities[symptom].push(data.severity_rating);
            }
        }
    });
    
    // Create chart container
    const chartContainer = document.createElement('div');
    chartContainer.className = 'chart-container';
    
    // Create canvas for the chart
    const canvas = document.createElement('canvas');
    chartContainer.appendChild(canvas);
    
    // Add to summary container
    summaryContainer.appendChild(chartContainer);
    
    // Prepare data for chart
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    
    const datasets = [];
    const symptomTypes = ['fatigue', 'pain', 'cough', 'nausea', 'lack_of_appetite'];
    
    const colors = {
        fatigue: 'rgb(255, 99, 132)',
        pain: 'rgb(54, 162, 235)',
        cough: 'rgb(255, 206, 86)',
        nausea: 'rgb(75, 192, 192)',
        lack_of_appetite: 'rgb(153, 102, 255)'
    };
    
    symptomTypes.forEach(symptom => {
        const data = monthlyData.map(month => {
            const severities = month.severities[symptom];
            return severities.length > 0 
                ? severities.reduce((sum, val) => sum + val, 0) / severities.length 
                : null;
        });
        
        // Only add dataset if there's at least one non-null value
        if (data.some(value => value !== null)) {
            datasets.push({
                label: symptom.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase()),
                data: data,
                borderColor: colors[symptom],
                backgroundColor: colors[symptom] + '20',
                tension: 0.1
            });
        }
    });
    
    // Create the chart
    new Chart(canvas, {
        type: 'line',
        data: {
            labels: months,
            datasets: datasets
        },
        options: {
            responsive: true,
            scales: {
                y: {
                    min: 0,
                    max: 5,
                    stepSize: 1,
                    title: {
                        display: true,
                        text: 'Average Severity (1-5)'
                    }
                }
            },
            plugins: {
                title: {
                    display: true,
                    text: 'Monthly Average Symptom Severity'
                }
            }
        }
    });
    
    // Add assessment counts by month
    const countsTitle = document.createElement('h4');
    countsTitle.textContent = 'Assessments by Month:';
    countsTitle.style.marginTop = '1rem';
    summaryContainer.appendChild(countsTitle);
    
    // Create another chart for assessment counts
    const countChartContainer = document.createElement('div');
    countChartContainer.className = 'chart-container';
    countChartContainer.style.height = '200px';
    
    const countCanvas = document.createElement('canvas');
    countChartContainer.appendChild(countCanvas);
    summaryContainer.appendChild(countChartContainer);
    
    new Chart(countCanvas, {
        type: 'bar',
        data: {
            labels: months,
            datasets: [{
                label: 'Number of Assessments',
                data: monthlyData.map(m => m.count),
                backgroundColor: 'rgba(176, 88, 134, 0.6)'
            }]
        },
        options: {
            responsive: true,
            plugins: {
                title: {
                    display: true,
                    text: 'Assessment Frequency by Month'
                }
            }
        }
    });
    
    detailsContainer.appendChild(summaryContainer);
}

// Show details for a specific assessment
function showAssessmentDetails(assessment) {
    modalContent.innerHTML = '';
    
    // Create timestamp
    const timestamp = document.createElement('p');
    const date = new Date(assessment.timestamp);
    timestamp.innerHTML = `<strong>Date:</strong> ${formatDate(date, 'MMMM d, yyyy')} at ${formatDate(date, 'HH:mm')}`;
    modalContent.appendChild(timestamp);
    
    // Create symptom cards
    for (const [symptomName, symptomData] of Object.entries(assessment.symptoms)) {
        const card = document.createElement('div');
        card.className = 'symptom-card';
        
        // Symptom name
        const name = document.createElement('div');
        name.className = 'symptom-name';
        name.textContent = symptomName.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
        card.appendChild(name);
        
        // Ratings
        const ratings = document.createElement('div');
        ratings.className = 'symptom-ratings';
        
        // Frequency rating
        const frequency = document.createElement('div');
        frequency.className = 'symptom-rating';
        
        const freqLabel = document.createElement('div');
        freqLabel.className = 'rating-label';
        freqLabel.textContent = 'Frequency';
        frequency.appendChild(freqLabel);
        
        const freqValue = document.createElement('div');
        freqValue.className = 'rating-value';
        freqValue.textContent = `${symptomData.frequency_rating}/5`;
        frequency.appendChild(freqValue);
        
        const freqScale = document.createElement('div');
        freqScale.className = 'rating-scale';
        for (let i = 1; i <= 5; i++) {
            const point = document.createElement('div');
            point.className = `scale-point ${i <= symptomData.frequency_rating ? 'active' : ''}`;
            freqScale.appendChild(point);
        }
        frequency.appendChild(freqScale);
        
        ratings.appendChild(frequency);
        
        // Severity rating
        const severity = document.createElement('div');
        severity.className = 'symptom-rating';
        
        const sevLabel = document.createElement('div');
        sevLabel.className = 'rating-label';
        sevLabel.textContent = 'Severity';
        severity.appendChild(sevLabel);
        
        const sevValue = document.createElement('div');
        sevValue.className = 'rating-value';
        sevValue.textContent = `${symptomData.severity_rating}/5`;
        severity.appendChild(sevValue);
        
        const sevScale = document.createElement('div');
        sevScale.className = 'rating-scale';
        for (let i = 1; i <= 5; i++) {
            const point = document.createElement('div');
            point.className = `scale-point ${i <= symptomData.severity_rating ? 'active' : ''}`;
            sevScale.appendChild(point);
        }
        severity.appendChild(sevScale);
        
        ratings.appendChild(severity);
        
        card.appendChild(ratings);
        
        // Key indicators
        if (symptomData.key_indicators && symptomData.key_indicators.length > 0) {
            const indicators = document.createElement('div');
            indicators.className = 'key-indicators';
            
            const indicatorsTitle = document.createElement('h4');
            indicatorsTitle.textContent = 'Key Indicators:';
            indicators.appendChild(indicatorsTitle);
            
            const indicatorsList = document.createElement('ul');
            symptomData.key_indicators.forEach(indicator => {
                const item = document.createElement('li');
                item.textContent = indicator;
                indicatorsList.appendChild(item);
            });
            
            indicators.appendChild(indicatorsList);
            card.appendChild(indicators);
        }
        
        // Additional notes
        if (symptomData.additional_notes) {
            const notes = document.createElement('div');
            notes.className = 'additional-notes';
            
            const notesTitle = document.createElement('h4');
            notesTitle.textContent = 'Notes:';
            notes.appendChild(notesTitle);
            
            const notesText = document.createElement('p');
            notesText.textContent = symptomData.additional_notes;
            notes.appendChild(notesText);
            
            card.appendChild(notes);
        }
        
        modalContent.appendChild(card);
    }
    
    // Add flag information if present
    if (assessment.flag_for_oncologist) {
        const flagInfo = document.createElement('div');
        flagInfo.className = 'flag-info';
        flagInfo.style.marginTop = '1rem';
        flagInfo.style.padding = '0.5rem';
        flagInfo.style.backgroundColor = '#ffe0e0';
        flagInfo.style.borderRadius = '4px';
        
        const flagTitle = document.createElement('strong');
        flagTitle.textContent = `Flagged for Oncologist (${assessment.oncologist_notification_level})`;
        flagInfo.appendChild(flagTitle);
        
        if (assessment.flag_reason) {
            const flagReason = document.createElement('p');
            flagReason.textContent = assessment.flag_reason;
            flagInfo.appendChild(flagReason);
        }
        
        modalContent.appendChild(flagInfo);
    }
    
    // Show the modal
    modal.style.display = 'block';
}

// Show details for a specific day
function showDayDetails(dateStr, assessments) {
    modalContent.innerHTML = '';
    
    // Create day title
    const title = document.createElement('h3');
    title.textContent = formatDate(new Date(dateStr), 'MMMM d, yyyy');
    modalContent.appendChild(title);
    
    // Create list of assessments
    const list = document.createElement('div');
    list.className = 'assessment-list';
    
    // Sort assessments by time
    assessments.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    
    assessments.forEach(assessment => {
        const time = new Date(assessment.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const highestSeverity = getHighestSeverity(assessment);
        
        const item = document.createElement('div');
        item.className = 'assessment-list-item';
        item.style.padding = '0.5rem';
        item.style.margin = '0.5rem 0';
        item.style.borderLeft = `5px solid ${getSeverityColor(highestSeverity)}`;
        item.style.backgroundColor = '#f5f5f5';
        item.style.cursor = 'pointer';
        
        item.innerHTML = `<strong>${time}</strong> - Assessment`;
        
        item.addEventListener('click', () => {
            showAssessmentDetails(assessment);
        });
        
        list.appendChild(item);
    });
    
    modalContent.appendChild(list);
    
    // Show the modal
    modal.style.display = 'block';
}

// Close the modal
function closeModal() {
    modal.style.display = 'none';
}

// Helper function: Get assessments for a date range
function getAssessmentsForDateRange(startDate, endDate) {
    if (!selectedPatientId || !allPatientData[selectedPatientId]) {
        return [];
    }
    
    return allPatientData[selectedPatientId].filter(assessment => {
        const assessmentDate = new Date(assessment.timestamp);
        return assessmentDate >= startDate && assessmentDate <= endDate;
    });
}

// Helper function: Get start of week (Monday)
function getStartOfWeek(date) {
    const d = new Date(date);
    const day = d.getDay();
    const diff = d.getDate() - day + (day === 0 ? -6 : 1); // Adjust for Sunday
    return new Date(d.setDate(diff));
}

// Helper function: Check if two dates are the same day
function isSameDay(date1, date2) {
    return date1.getFullYear() === date2.getFullYear() &&
           date1.getMonth() === date2.getMonth() &&
           date1.getDate() === date2.getDate();
}

// Helper function: Get highest symptom severity from assessment
function getHighestSeverity(assessment) {
    let highest = 0;
    for (const symptom in assessment.symptoms) {
        if (assessment.symptoms[symptom].severity_rating > highest) {
            highest = assessment.symptoms[symptom].severity_rating;
        }
    }
    return highest;
}

// Helper function: Get color for severity level
function getSeverityColor(level) {
    const colors = [
        '#4caf50', // Level 1 - Green
        '#8bc34a', // Level 2 - Light green
        '#ffc107', // Level 3 - Yellow
        '#ff9800', // Level 4 - Orange
        '#f44336'  // Level 5 - Red
    ];
    
    return colors[level - 1] || '#999';
}

// Helper function: Get level description
function getLevelDescription(level) {
    const descriptions = [
        'Minimal',
        'Mild',
        'Moderate',
        'Severe',
        'Extreme'
    ];
    
    return descriptions[level - 1] || '';
}

// Helper function: Format date
function formatDate(date, format) {
    return window.dateFns.format(date, format);
}

// Helper function: Show error message
function showError(message) {
    calendarContainer.innerHTML = `<div class="error">${message}</div>`;
    detailsContainer.innerHTML = '';
}

// Handle clicks on heatmap
function onHeatmapClick(event) {
    const monthIndex = event.target._index;
    if (monthIndex !== undefined) {
        // Update current date to selected month
        currentDate = new Date(currentDate.getFullYear(), monthIndex, 1);
        currentView = 'month';
        viewTypeSelector.value = 'month';
        updateDisplay();
    }
}"""

# Create HTTP server handler to handle API requests
class JSONHistoryViewerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=kwargs.pop('directory', '.'), **kwargs)
    
    def do_GET(self):
        # Handle API requests
        if self.path.startswith('/api/'):
            # Get list of patients
            if self.path == '/api/patients':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                # Find unique patient IDs
                patient_ids = set()
                for filename in os.listdir('.'):
                    if filename.startswith('patient_') and filename.endswith('.json'):
                        parts = filename.split('_')
                        if len(parts) > 1:
                            patient_id = parts[1]
                            patient_ids.add(patient_id)
                
                self.wfile.write(json.dumps(list(patient_ids)).encode())
                return
            
            # Get assessments for a specific patient
            elif self.path.startswith('/api/patient/'):
                patient_id = self.path.split('/')[3]
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                assessments = []
                
                # Find all assessment files for this patient
                for filename in os.listdir('.'):
                    if filename.startswith(f'patient_{patient_id}_assessment_') and filename.endswith('.json'):
                        try:
                            with open(filename, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                                assessments.append(data)
                        except Exception as e:
                            print(f"Error loading {filename}: {e}")
                
                self.wfile.write(json.dumps(assessments).encode())
                return
        
        # Fall back to serving static files
        return super().do_GET()

def copy_patient_data(directory):
    """Copy patient assessment JSON files to the web directory"""
    # Find all assessment files
    for filename in os.listdir('.'):
        if 'patient_' in filename and filename.endswith('.json'):
            try:
                # Copy file to web directory
                shutil.copy(filename, os.path.join(directory, filename))
            except Exception as e:
                print(f"Error copying {filename}: {e}")

def start_server(directory):
    """Start the HTTP server"""
    port = 8000
    handler = lambda *args, **kwargs: JSONHistoryViewerHandler(*args, directory=directory, **kwargs)
    
    server = HTTPServer(('', port), handler)
    print(f"Server started at http://localhost:{port}")
    print("Press Ctrl+C to stop the server")
    
    # Open browser automatically
    webbrowser.open(f"http://localhost:{port}")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped")
    finally:
        # Clean up the temporary directory
        shutil.rmtree(directory, ignore_errors=True)

# Main function
def main():
    """Main function"""
    print("Starting OnCallLogist Patient History Viewer...")
    create_json_history_viewer()

if __name__ == "__main__":
    main()