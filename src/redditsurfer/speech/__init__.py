"""Speech providers, alignment, caching, and audio composition."""

from redditsurfer.speech.base import RelativeWord, SegmentSpeech, SpeechProvider
from redditsurfer.speech.service import synthesize_script

__all__ = ["RelativeWord", "SegmentSpeech", "SpeechProvider", "synthesize_script"]
