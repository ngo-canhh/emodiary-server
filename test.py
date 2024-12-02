from yt_dlp import YoutubeDL

def get_audio_stream_link(video_url):
    ydl_opts = {
        'format': 'bestaudio/best',  # Lấy stream chất lượng audio tốt nhất
        'quiet': True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)  # Chỉ lấy thông tin, không tải
        audio_url = info['url']
        return audio_url

# Sử dụng
video_url = 'https://www.youtube.com/watch?v=T07qs184QrY'
audio_stream_link = get_audio_stream_link(video_url)
print("Link stream audio:", audio_stream_link)
