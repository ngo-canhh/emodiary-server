import json
from yt_dlp import YoutubeDL
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import aiohttp
import re
import asyncio
import subprocess

app = FastAPI()


# Hàm lấy danh sách track từ Spotify
async def get_trending_tracks_id():
    url = "https://open.spotify.com/playlist/37i9dQZF1DWT2oR9BciC32"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                html_content = await response.text()
                match = re.findall(r'"/track/[\w]+"', html_content)
                tracks = [track.strip('"').split('/')[-1] for track in match]
                return tracks # Giới hạn 10 track đầu tiên
            else:
                print(f"Không thể lấy HTML. Mã lỗi: {response.status}")
                return []

# Hàm lấy thông tin từ song.link
async def get_ytb_id(session, spotify_id: str):
    url = f'https://api.song.link/v1-alpha.1/links?url=spotify%3Atrack%3A{spotify_id}&userCountry=VI&songIfSingle=true'
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                informations = await response.json()
                entityUniqueId = informations["linksByPlatform"]["youtube"]["entityUniqueId"]
                return informations["entitiesByUniqueId"][entityUniqueId]
    except Exception as e:
        print(f"Lỗi khi xử lý track {spotify_id}: {e}")
        return None

# Chuyển sang thread để xử lý YoutubeDL
async def get_audio_stream_link(ytb_id):
    def fetch_audio_link():
        video_url = f"https://www.youtube.com/watch?v={ytb_id}"
        ydl_opts = {
            'format': 'bestaudio/best',  # Lấy định dạng audio tốt nhất có thể
            'quiet': True,
            'extract_flat': True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            return info.get('url')

    try:
        return await asyncio.to_thread(fetch_audio_link)
    except Exception as e:
        print(f"Lỗi khi lấy link audio: {e}")
        return None

def filter_track(track: dict):
    return {
        "id": track["id"],
        "title": track["title"],
        "artist": track["artistName"],
        "thumbnailUrl": track["thumbnailUrl"],
        "thumbnailWidth": track["thumbnailWidth"],
        "thumbnailHeight": track["thumbnailHeight"],
    }

cache_track_information_by_spotify_id = {}
# async def get_trending_tracks_information():
#     spotify_tracks_id = await get_trending_tracks_id()
#     print(len(spotify_tracks_id))
#     async with aiohttp.ClientSession() as session:
#         sem = asyncio.Semaphore(3)        
#         async def process_track(spotify_id):
#             print(f"handle {spotify_id}")
#             if spotify_id in cache_track_information_by_id:
#                 return cache_track_information_by_id[spotify_id]
#             async with sem:
#                 try:
#                     track_information = await get_ytb_id(session, spotify_id)
#                     if not track_information:
#                         return None

#                     track_information = filter_track(track_information)
#                     track_information["streamUrl"] = f'http://127.0.0.1:5001/stream/{track_information["id"]}'
#                     cache_track_information_by_id[spotify_id] = track_information
#                     await asyncio.sleep(18)
#                     return track_information
#                 except Exception as e:
#                     print(f"Lỗi khi xử lý track {spotify_id}: {e}")
#                     return None

#         tasks = [process_track(spotify_id) for spotify_id in spotify_tracks_id]
        
#         # Sử dụng asyncio.as_completed để yield ngay khi task hoàn thành
#         for task in asyncio.as_completed(tasks):
#             try:
#                 track_information = await task
#                 if track_information:
#                     print(track_information["streamUrl"])
#                     yield json.dumps(track_information) + '\n'
#             except Exception as e:
#                 print(f"Lỗi khi xử lý task: {e}")

BATCH_SIZE = 10  # Giới hạn số lượng request mỗi batch
DELAY_BETWEEN_BATCHES = 60  # Độ trễ giữa các batch

async def process_batch(batch, sem, session):
    results = []
    async def process_track(spotify_id):
        async with sem:
            try:
                if spotify_id in cache_track_information_by_spotify_id:
                    return cache_track_information_by_spotify_id[spotify_id]
                track_information = await get_ytb_id(session, spotify_id)
                if track_information:
                    track_information = filter_track(track_information)
                    track_information["streamUrl"] = f'http://127.0.0.1:5001/stream/{track_information["id"]}'
                    cache_track_information_by_spotify_id[spotify_id] = track_information
                    return track_information
            except Exception as e:
                print(f"Lỗi khi xử lý track {spotify_id}: {e}")
        return None

    tasks = [process_track(spotify_id) for spotify_id in batch]
    results = await asyncio.gather(*tasks)
    return [result for result in results if result]

async def get_trending_tracks_information():
    spotify_tracks_id = await get_trending_tracks_id()
    print(f"Tổng số track: {len(spotify_tracks_id)}")
    cached_tracks = []
    for track in spotify_tracks_id:
        if track in cache_track_information_by_spotify_id:
            yield json.dumps(cache_track_information_by_spotify_id[track]) + '\n'
            cached_tracks.append(track)
    for cached_track in cached_tracks:
        spotify_tracks_id.remove(cached_track)
            

    sem = asyncio.Semaphore(3)  # Giới hạn số lượng request song song
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(spotify_tracks_id), BATCH_SIZE):
            batch = spotify_tracks_id[i:i + BATCH_SIZE]
            print(f"Xử lý batch {i // BATCH_SIZE + 1} với {len(batch)} track")
            tracks = await process_batch(batch, sem, session)

            for track in tracks:
                yield json.dumps(track) + '\n'


            await asyncio.sleep(DELAY_BETWEEN_BATCHES)  # Đợi trước khi xử lý batch tiếp theo


# Endpoint chính
@app.get('/trending')
async def get_trending():
    return StreamingResponse(get_trending_tracks_information(), media_type="application/x-ndjson")






import subprocess
from fastapi.responses import StreamingResponse
from urllib.parse import urlparse, parse_qs

def get_audio_duration(stream_url: str) -> float:
    # Phân tích URL và lấy giá trị tham số `dur`
    query_params = parse_qs(urlparse(stream_url).query)
    duration = query_params.get("dur", [0])[0]  # Lấy giá trị `dur`, mặc định là 0
    return float(duration)


async def stream_audio(stream_url: str):    
    duration = get_audio_duration(stream_url)
    # Sử dụng FFmpeg để transcoding dữ liệu và stream trực tiếp
    def generate_ffmpeg_process():
        # Chạy FFmpeg để stream audio
        command = [
            "ffmpeg", 
            "-i", stream_url,                  # Đầu vào là video URL
            "-vn",                            # Không xử lý video
            "-acodec", "libmp3lame",           # Sử dụng codec MP3
            "-f", "mp3",                      # Định dạng MP3
            "pipe:1"                           # Đưa kết quả qua pipe (stdout)
        ]
        
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Sử dụng `await asyncio.to_thread` để chạy quá trình FFmpeg ngoài thread chính
    process = await asyncio.to_thread(generate_ffmpeg_process)
    
    # Trả về dữ liệu audio trực tiếp tới client thông qua StreamingResponse
    # Trả về stream và đính kèm header
    response = StreamingResponse(process.stdout, media_type="audio/mpeg")
    response.headers["duration"] = str(duration)  # Gửi thời lượng dưới dạng header
    return response


@app.get("/stream/{ytb_id}")
async def stream(ytb_id: str):
    stream_url = await get_audio_stream_link(ytb_id)
    print(stream_url)
    # Gọi hàm để bắt đầu stream
    return await stream_audio(stream_url)


