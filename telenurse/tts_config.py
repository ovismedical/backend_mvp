import azure.cognitiveservices.speech as speechsdk

# STT setup
STT_API_KEY = "tCg1r8HgLWvXMOPcjSz2EtBsScO5ppM83ckzhaNaH8L3GWaVGzavJQQJ99BCACYeBjFXJ3w3AAAYACOGeF1U"
STT_SERVER_REGION = "eastus"
AZURE_REGION = "eastasia"  # Best region for Cantonese
SPEECH_LANGUAGE = "en-US"  # Cantonese (Hong Kong)
VOICE_NAME = "en-US-AriaNeural"  # Best female Cantonese voice

# Audio settings
SAMPLE_RATE = 16000  # Hz
CHANNELS = 1  # Mono

class AzureSpeechService:
    
    def __init__(self, subscription_key=STT_API_KEY, region=STT_SERVER_REGION):
        """Initialize speech service with Azure credentials"""
        self.speech_config = speechsdk.SpeechConfig(
            subscription=subscription_key, 
            region=region
        )
        
        # Configure for Cantonese
        self.speech_config.speech_recognition_language = SPEECH_LANGUAGE
        self.speech_config.speech_synthesis_language = SPEECH_LANGUAGE
        self.speech_config.speech_synthesis_voice_name = VOICE_NAME
    
    def text_to_speech(self, text):
        """
        Convert text to speech and play it
        
        Args:
            text (str): Text to be converted to speech
            
        Returns:
            bool: True if successful, False otherwise
        """
        speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=self.speech_config)
        result = speech_synthesizer.speak_text_async(text).get()
        
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            print(f"TTS Success: '{text}'")
            return True
        else:
            print(f"TTS Error: {result.reason}")
            if result.reason == speechsdk.ResultReason.Canceled:
                cancellation_details = speechsdk.SpeechSynthesisCancellationDetails(result)
                print(f"TTS Error details: {cancellation_details.reason}")
            return False
    
    def speech_to_text(self):
        """
        Capture speech from microphone and convert to text
        
        Returns:
            str: Recognized text or None if recognition failed
        """
        # Use default microphone for audio input
        audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
        
        # Create speech recognizer
        speech_recognizer = speechsdk.SpeechRecognizer(
            speech_config=self.speech_config, 
            audio_config=audio_config
        )
        
        print("Listening... (speak now)")
        
        # Start speech recognition
        result = speech_recognizer.recognize_once_async().get()
        
        # Process the result
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            print(f"STT Success: '{result.text}'")
            return result.text
        elif result.reason == speechsdk.ResultReason.NoMatch:
            print("STT Error: No speech could be recognized")
        elif result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = speechsdk.CancellationDetails(result)
            print(f"STT Error: {cancellation_details.reason}")
            if cancellation_details.reason == speechsdk.CancellationReason.Error:
                print(f"STT Error details: {cancellation_details.error_details}")
        
        return None
