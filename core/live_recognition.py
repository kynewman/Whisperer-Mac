"""
Live speech recognition using a lightweight Vosk model.
Automatically downloads 'vosk-model-small-en-us-0.15' on first run for zero-setup live dictation previews.
"""

import os
import json
import threading
import queue
import zipfile
import urllib.request
import numpy as np

import config

VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
# More reliable fallback mirrors for the model
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

class LiveRecognizer:
    """
    Real-time speech recognizer that streams word-by-word transcription.
    Runs in a dedicated thread and processes audio passed from AudioRecorder.
    """
    
    def __init__(self, text_callback=None):
        self.text_callback = text_callback
        self.queue = queue.Queue(maxsize=64)
        self._stop_event = threading.Event()
        self._accepting_audio = False
        self._thread = None
        self.model = None
        self.recognizer = None
        self.finalized_text = ""
        
        # Download and load model in background so it doesn't block UI start
        threading.Thread(target=self._ensure_model, daemon=True).start()
        
    def _ensure_model(self):
        """Ensures the lightweight Vosk model exists, downloads if missing, then loads it."""
        try:
            from vosk import Model, KaldiRecognizer
        except ImportError:
            print("Vosk not installed.")
            return

        vosk_base_dir = os.path.join(config.MODEL_CACHE_DIR, "vosk")
        os.makedirs(vosk_base_dir, exist_ok=True)
        model_dir = os.path.join(vosk_base_dir, VOSK_MODEL_NAME)
        
        if not os.path.exists(model_dir) or not os.listdir(model_dir):
            print(f"Downloading Vosk live preview model ({VOSK_MODEL_NAME})...")
            zip_path = os.path.join(vosk_base_dir, "vosk_model.zip")
            
            try:
                urllib.request.urlretrieve(VOSK_MODEL_URL, zip_path)
            except Exception as e:
                print(f"Failed to download Vosk model: {e}")
                return
                
            print("Extracting Vosk model...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(vosk_base_dir)
                
            if os.path.exists(zip_path):
                os.remove(zip_path)
            print("Vosk model ready.")
            
        try:
            self.model = Model(model_dir)
            self.recognizer = KaldiRecognizer(self.model, config.AUDIO_SAMPLE_RATE)
            self.recognizer.SetWords(False) # Disable word timestamps for max performance
            print("Live Recognizer engine loaded successfully.")
        except Exception as e:
            print(f"Failed to initialize Vosk recognizer: {e}")

    def start(self):
        """Start processing audio for live recognition."""
        if not self.model or not self.recognizer:
            self._accepting_audio = False
            return
            
        self.finalized_text = ""
        
        # Clear out any old chunks
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
                
        self._stop_event.clear()
        self._accepting_audio = True
        self.recognizer.Reset()
        
        if self.text_callback:
            self.text_callback("")
            
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()
        
    def stop(self):
        """Stop processing audio."""
        self._accepting_audio = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
            
    def feed_audio(self, float_array: np.ndarray):
        """Receive audio chunk from AudioRecorder."""
        if not self._accepting_audio or self._stop_event.is_set():
            return
        try:
            self.queue.put_nowait(float_array.copy())
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(float_array.copy())
            except queue.Full:
                pass
            
    def _process_loop(self):
        """Continuously pulls from the queue and transcribes via Vosk."""
        while not self._stop_event.is_set() or not self.queue.empty():
            try:
                chunk = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
                
            # Convert float32 [-1.0, 1.0] to int16 PCM format for Vosk
            int16_data = (chunk * 32767).astype(np.int16).tobytes()
            
            if self.recognizer.AcceptWaveform(int16_data):
                res = json.loads(self.recognizer.Result())
                text = res.get("text", "")
                if text:
                    self.finalized_text += " " + text
                current_text = self.finalized_text.strip()
            else:
                res = json.loads(self.recognizer.PartialResult())
                partial = res.get("partial", "")
                current_text = (self.finalized_text + " " + partial).strip()
                
            if self.text_callback:
                self.text_callback(current_text)
