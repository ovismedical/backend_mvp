from tts_config import AzureSpeechService

def test_tts_only():
    """Test just the text-to-speech functionality"""
    speech_service = AzureSpeechService()
    
    # Test with Cantonese phrases
    phrases = [
        "您好，我是您的健康助理。",  # Hello, I am your health assistant.
        "請問您今天感覺如何？",      # How are you feeling today?
        "您有沒有任何不適？",        # Do you have any discomfort?
        "謝謝您的回答。"             # Thank you for your answer.
    ]
    
    for phrase in phrases:
        speech_service.text_to_speech(phrase)

def test_conversation():
    """Test a basic back-and-forth conversation flow"""
    speech_service = AzureSpeechService()
    
    # Introduction
    speech_service.text_to_speech("您好，我是您的健康助理。請問您今天感覺如何？")
    
    # Listen for patient response
    patient_response = speech_service.speech_to_text()
    
    # Follow-up question
    if patient_response:
        speech_service.text_to_speech("謝謝您的回答。您有沒有任何不適或疼痛？")
        
        # Listen for symptoms
        symptoms_response = speech_service.speech_to_text()
        
        # Ask about duration if symptoms were mentioned
        if symptoms_response:
            speech_service.text_to_speech("這些症狀持續了多久？")
            speech_service.speech_to_text()
    
    # Conclusion
    speech_service.text_to_speech("謝謝您的回答。希望您早日康復。")

def main():
    print("Azure Speech Service Test")
    print("-----------------------")
    print("1. Test TTS only")
    print("2. Test conversation flow")
    
    choice = input("Enter your choice (1 or 2): ")
    
    if choice == "1":
        test_tts_only()
    elif choice == "2":
        test_conversation()
    else:
        print("Invalid choice. Please enter 1 or 2.")

if __name__ == "__main__":
    main()