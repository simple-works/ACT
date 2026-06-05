# import re
# from abc import ABC, abstractmethod
# from asyncio import get_event_loop
# from pathlib import Path
# from time import time

# import yt_dlp
# from pydantic import BaseModel

# from utils.log import logger

# log = logger(__name__)


# # ----------------------------------------------------------------------------------------------------
# # * Audio Track
# # ----------------------------------------------------------------------------------------------------
# class AudioTrack(BaseModel):
#     """Represents a single audio track in the queue."""

#     title: str
#     url: str
#     stream_url: str
#     duration: float | None = None
#     thumbnail: str | None = None
#     artist: str | None = None
#     source: str = "unknown"  # e.g., youtube, soundcloud


# # ----------------------------------------------------------------------------------------------------
# # * Audio Queue
# # ----------------------------------------------------------------------------------------------------
# class AudioQueue(BaseModel):
#     """Represents a queue of audio tracks with metadata."""

#     tracks: list[AudioTrack]
#     source_type: str  # e.g., track, playlist, search
#     source_name: str  # e.g., YouTube, SoundCloud
#     title: str | None = None  # Playlist title or search query


# # ----------------------------------------------------------------------------------------------------
# # * Audio Provider
# # ----------------------------------------------------------------------------------------------------
# class AudioProvider(ABC):
#     """Abstract base class for audio providers."""

#     platform_name: str
#     url_patterns: list[re.Pattern] = []

#     @classmethod
#     def can_handle_url(cls, url: str) -> bool:
#         """Check if the provider can handle the given URL."""
#         return any(pattern.match(url) for pattern in cls.url_patterns)

#     @abstractmethod
#     async def fetch_audio(self, input_text: str) -> AudioQueue | None:
#         """Fetch audio data from input (URL or query) and return an audio queue."""
#         pass


# # ----------------------------------------------------------------------------------------------------
# # * YtDlp Provider
# # ----------------------------------------------------------------------------------------------------
# class YtDlpProvider(AudioProvider):
#     """Provider for platforms supported by yt-dlp."""

#     platform_name = "yt_dlp"
#     url_patterns = [re.compile(r"https?://[^\s/$.?#].[^\s]*")]

#     YTDL_COOKIES_PATH = Path(__file__).parent.parent / "db" / "data" / "cookies.txt"
#     BASE_YTDL_OPTS = {
#         "cookiefile": str(YTDL_COOKIES_PATH),
#         "format": "bestaudio[acodec=opus]/bestaudio[acodec=aac]/bestaudio/best",  # Prefer opus or aac for Discord
#         "noplaylist": False,  # Allow playlists
#         "quiet": True,  # Suppress console output
#         "default_search": "auto",  # Enable search queries
#         "extract_flat": True,  # Faster playlist processing
#         "retries": 3,  # Retry failed requests
#         "fragment_retries": 3,  # Retry failed fragments
#         "http_chunk_size": 1048576,  # 1MB chunks for streaming
#         "ignoreerrors": True,  # Skip invalid playlist entries
#         "socket_timeout": 10,  # Timeout for slow connections
#         "no_cache_dir": True,  # Avoid disk I/O
#         "outtmpl": "%(title)s.%(ext)s",  # Consistent output naming (if downloading)
#         "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",  # Bypass some restrictions
#         "force_generic_extractor": True,  # Support non-YouTube platforms
#         "max_downloads": 50,  # Limit playlist size to avoid overload
#     }

#     def __init__(self):
#         if not self.YTDL_COOKIES_PATH.exists():
#             log.warning(
#                 f"Cookies file not found at '{self.YTDL_COOKIES_PATH}'. "
#                 "Some videos may require authentication. "
#                 "Place cookies.txt in './db/data/cookies.txt' or set YTDL_COOKIES_PATH. "
#                 "See https://github.com/yt-dlp/yt-dlp#authentication-with-cookies."
#             )

#     def _create_track(
#         self, info: dict, source_name: str, fallback_url: str
#     ) -> AudioTrack:
#         """Create an AudioTrack from yt-dlp info dict."""
#         return AudioTrack(
#             title=info.get("title", "Unknown Title"),
#             url=info.get("webpage_url", fallback_url),
#             stream_url=info["url"],
#             duration=info.get("duration"),
#             thumbnail=info.get("thumbnail"),
#             artist=info.get("uploader"),
#             source=source_name,
#         )

#     async def fetch_audio(self, input_text: str) -> AudioQueue | None:
#         """Fetch audio data from input (URL or query) and return an audio queue."""
#         loop = get_event_loop()
#         # Initial processing with extract_flat to get metadata
#         ydl_opts = {**self.BASE_YTDL_OPTS, "extract_flat": True}
#         with yt_dlp.YoutubeDL(ydl_opts) as ydl:
#             try:
#                 info = await loop.run_in_executor(
#                     None, lambda: ydl.extract_info(input_text, download=False)
#                 )
#                 if not info:
#                     log.warning(f"No info returned for input: {input_text}")
#                     return None
#             except Exception as e:
#                 log.exception(f"Failed to fetch metadata for input: {input_text}")
#                 return None

#         source_name = info.get("extractor_key", "unknown").lower()
#         tracks = []
#         source_type = "track"
#         title = info.get("title", input_text)

#         if "entries" in info and info.get("entries"):
#             # Playlist or search results
#             source_type = (
#                 "playlist"
#                 if info.get("webpage_url", "").startswith("http")
#                 else "search"
#             )
#             track_opts = {**self.BASE_YTDL_OPTS, "noplaylist": True}
#             with yt_dlp.YoutubeDL(track_opts) as ydl:
#                 for entry in info["entries"]:
#                     track_url = entry.get("url", entry.get("webpage_url"))
#                     if not track_url:
#                         continue
#                     try:
#                         track_info = await loop.run_in_executor(
#                             None, lambda: ydl.extract_info(track_url, download=False)
#                         )
#                         if not track_info:
#                             log.warning(f"No track info for URL: {track_url}")
#                             continue
#                         tracks.append(
#                             self._create_track(track_info, source_name, track_url)
#                         )
#                     except Exception as e:
#                         log.exception(
#                             f"Failed to fetch track info for URL: {track_url}"
#                         )
#                         continue
#         else:
#             # Single track
#             track_opts = {**self.BASE_YTDL_OPTS, "noplaylist": True}
#             with yt_dlp.YoutubeDL(track_opts) as ydl:
#                 try:
#                     track_info = await loop.run_in_executor(
#                         None, lambda: ydl.extract_info(input_text, download=False)
#                     )
#                     if not track_info:
#                         log.warning(f"No track info returned for input: {input_text}")
#                         return None
#                     if "entries" in track_info and track_info["entries"]:
#                         track_info = track_info["entries"][0]
#                     tracks.append(
#                         self._create_track(track_info, source_name, input_text)
#                     )
#                 except Exception as e:
#                     log.exception(
#                         f"Failed to fetch single track for input: {input_text}"
#                     )
#                     return None

#         if not tracks:
#             log.warning(f"No valid tracks found for input: {input_text}")
#             return None

#         return AudioQueue(
#             tracks=tracks, source_type=source_type, source_name=source_name, title=title
#         )


# # ----------------------------------------------------------------------------------------------------
# # * Audio Manager
# # ----------------------------------------------------------------------------------------------------
# class AudioManager:
#     """Manages audio providers and handles input processing."""

#     CACHE_DURATION = 3600  # Cache entries for 1 hour (in seconds)

#     def __init__(self):
#         self.providers: list[AudioProvider] = [YtDlpProvider()]
#         self._cache: dict[str, tuple[AudioQueue, float]] = (
#             {}
#         )  # {input_text: (AudioQueue, timestamp)}

#     def register_provider(self, provider: AudioProvider) -> None:
#         """Register a new audio provider."""
#         self.providers.append(provider)

#     def clear_cache(self) -> None:
#         """Clear the audio cache."""
#         self._cache.clear()
#         log.info("Audio cache cleared")

#     async def get_audio(self, input_text: str) -> AudioQueue | None:
#         """Fetch audio data from input (URL or query) using the appropriate provider."""
#         # Check cache
#         if input_text in self._cache:
#             queue, timestamp = self._cache[input_text]
#             if time() - timestamp < self.CACHE_DURATION:
#                 log.debug(f"Cache hit for input: {input_text}")
#                 return queue
#             else:
#                 log.debug(f"Cache expired for input: {input_text}")
#                 del self._cache[input_text]

#         # Cache miss: fetch from providers
#         for provider in self.providers:
#             result = await provider.fetch_audio(input_text)
#             if result:
#                 # Store in cache with current timestamp
#                 self._cache[input_text] = (result, time())
#                 log.debug(f"Cache miss, stored result for input: {input_text}")
#                 return result

#         log.warning(f"No provider could handle input: {input_text}")
#         return None


# # ----------------------------------------------------------------------------------------------------
