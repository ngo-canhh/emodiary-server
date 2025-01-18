import json
from yt_dlp import YoutubeDL
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import aiohttp
import re
import asyncio
import subprocess
from urllib.parse import urlparse, parse_qs

app = FastAPI()

# Cấu hình các hằng số
BATCH_SIZE = 10
DELAY_BETWEEN_BATCHES = 60
CACHE_TRACK_INFORMATION = {}

# Helpers
async def fetch_html_content(url: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                return await response.text()
            else:
                print(f"Error fetching HTML. Status code: {response.status}")
                return ""

def extract_spotify_track_ids(html_content: str) -> list:
    matches = re.findall(r'"/track/[\w]+"', html_content)
    return [match.strip('"').split('/')[-1] for match in matches]

async def fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    async with session.get(url, timeout=10) as response:
        if response.status == 200:
            return await response.json()
        return {}

def filter_track_info(track: dict) -> dict:
    return {
        "id": track["id"],
        "title": track["title"],
        "artist": track["artistName"],
        "thumbnailUrl": track["thumbnailUrl"],
        "thumbnailWidth": track["thumbnailWidth"],
        "thumbnailHeight": track["thumbnailHeight"],
    }

def get_audio_duration(stream_url: str) -> float:
    query_params = parse_qs(urlparse(stream_url).query)
    return float(query_params.get("dur", [0])[0])

# Spotify-related functions
async def get_trending_tracks_id() -> list:
    url = "https://open.spotify.com/playlist/6hdAmeVk15lP6EmTc5vXvG"
    html_content = await fetch_html_content(url)
    return extract_spotify_track_ids(html_content)

async def get_ytb_track_info(session, spotify_id: str):
    url = f'https://api.song.link/v1-alpha.1/links?url=spotify%3Atrack%3A{spotify_id}&userCountry=VI&songIfSingle=true'
    data = await fetch_json(session, url)
    try:
        entity_id = data["linksByPlatform"]["youtube"]["entityUniqueId"]
        return data["entitiesByUniqueId"][entity_id]
    except KeyError:
        return None

# YouTube-related functions
async def fetch_audio_stream_link(ytb_id: str):
    def extract_audio_link():
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'extract_flat': True,
        }
        video_url = f"https://www.youtube.com/watch?v={ytb_id}"
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            return info.get('url')

    return await asyncio.to_thread(extract_audio_link)

# Batch Processing
async def process_batch(batch, sem, session):
    async def process_track(spotify_id):
        async with sem:
            if spotify_id in CACHE_TRACK_INFORMATION:
                return CACHE_TRACK_INFORMATION[spotify_id]
            track_info = await get_ytb_track_info(session, spotify_id)
            if track_info:
                filtered_info = filter_track_info(track_info)
                filtered_info["streamUrl"] = f'http://127.0.0.1:5001/stream/{filtered_info["id"]}'
                CACHE_TRACK_INFORMATION[spotify_id] = filtered_info
                return filtered_info
            return None

    tasks = [process_track(spotify_id) for spotify_id in batch]
    results = await asyncio.gather(*tasks)
    return [result for result in results if result]

async def get_trending_tracks_information():
    spotify_tracks_id = await get_trending_tracks_id()
    print(f"Total tracks: {len(spotify_tracks_id)}")

    sem = asyncio.Semaphore(3)
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(spotify_tracks_id), BATCH_SIZE):
            batch = spotify_tracks_id[i:i + BATCH_SIZE]
            print(f"Processing batch {i // BATCH_SIZE + 1} with {len(batch)} tracks")
            tracks = await process_batch(batch, sem, session)
            for track in tracks:
                yield json.dumps(track) + '\n'
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

# Streaming Audio
async def stream_audio(stream_url: str):
    duration = get_audio_duration(stream_url)

    def generate_ffmpeg_process():
        command = [
            "ffmpeg",
            "-i", stream_url,
            "-vn",
            "-acodec", "libmp3lame",
            "-f", "mp3",
            "pipe:1"
        ]
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    process = await asyncio.to_thread(generate_ffmpeg_process)
    response = StreamingResponse(process.stdout, media_type="audio/mpeg")
    response.headers["duration"] = str(duration)
    return response

# API Endpoints
@app.get('/trending')
async def get_trending():
    return StreamingResponse(get_trending_tracks_information(), media_type="application/x-ndjson")

@app.get("/stream/{ytb_id}")
async def stream(ytb_id: str):
    stream_url = await fetch_audio_stream_link(ytb_id)
    return await stream_audio(stream_url)
