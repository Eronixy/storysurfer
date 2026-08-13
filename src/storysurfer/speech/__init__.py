"""Speech providers, alignment, caching, and audio composition."""

from storysurfer.speech.base import RelativeWord, SegmentSpeech, SpeechProvider
from storysurfer.speech.service import synthesize_script

__all__ = ["RelativeWord", "SegmentSpeech", "SpeechProvider", "synthesize_script"]
