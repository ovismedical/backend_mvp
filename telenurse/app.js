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
    resetAssessment: async () => {
        try {
            const response = await axios.post('/api/reset_assessment');
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
                                                onClick={async () => {
                                                    try {
                                                        setLoading(true);
                                                        await api.resetAssessment();
                                                        // Reset the local state too
                                                        setAssessmentStatus({ 
                                                            active: false, 
                                                            patient_id: '', 
                                                            language: 'en', 
                                                            input_mode: 'keyboard',
                                                            conversation: [],
                                                            result: null
                                                        });
                                                        setLoading(false);
                                                    } catch (err) {
                                                        console.error('Error resetting assessment:', err);
                                                        setError('Failed to reset assessment');
                                                        setLoading(false);
                                                    }
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