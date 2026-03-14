import os, re, json, base64, threading, time, collections
from flask import Flask, request, render_template_string, Response, abort
from curl_cffi import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

app = Flask(__name__)

# --- CONFIG & FAIL-SAFE GLOBALS ---
# We use multiple mirrors for Gogo. If one fails, the app survives.
GOGO_MIRRORS = ["https://anitaku.pe", "https://gogoanime3.co", "https://gogoanime.hu"]
PAHE_BASE = "https://animepahe.ru"

# Latest 2026 AES Keys for the "Wall"
KEYS = {'key': b'3791144120345713', 'iv': b'3134003220102143'}

# Memory-Safe Cache (Max 100 segments to prevent OOM Crashes on free tiers)
SEGMENT_CACHE = collections.deque(maxlen=100)
CACHE_DATA = {}

# --- SURGICAL UTILS ---

def ghost_get(url, stream=False, referer=None, retries=2):
    """Bypasses Cloudflare using Chrome 124 TLS Fingerprinting."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": referer or url,
        "Accept-Language": "en-US,en;q=0.9"
    }
    for i in range(retries):
        try:
            return requests.get(url, impersonate="chrome124", stream=stream, headers=headers, timeout=10)
        except:
            if i == retries - 1: raise
            time.sleep(1)

def decrypt_gogo(data):
    """Cracks the AES encryption used by Gogo/Anitaku."""
    cipher = AES.new(KEYS['key'], AES.MODE_CBC, iv=KEYS['iv'])
    return unpad(cipher.decrypt(base64.b64decode(data)), AES.block_size).decode('utf-8')

# --- THE PROXY ENGINE ---

@app.route('/proxy')
def proxy():
    url = request.args.get('url')
    if not url: abort(400)
    
    # 1. Return from Memory-Safe Cache if exists
    if url in CACHE_DATA:
        return Response(CACHE_DATA[url], content_type='video/mp2t')

    # 2. Fetch and Rewrite Playlists
    res = ghost_get(url, stream=True)
    if '.m3u8' in url:
        content = res.text
        base = url.rsplit('/', 1)[0]
        # Rewrite every segment to go through THIS server (Bypasses CORS/Region Blocks)
        rewritten = re.sub(r'^(?!http)(.+)$', lambda m: f"/proxy?url={base}/{m.group(1)}", content, flags=re.M)
        return Response(rewritten, mimetype='application/vnd.apple.mpegurl')
    
    # 3. Stream Video Segments
    return Response(res.iter_content(chunk_size=1024*256), content_type=res.headers.get('Content-Type'))

# --- PROVIDER MODULES ---

class MultiProvider:
    @staticmethod
    def search(query):
        """Unified Search across all active providers."""
        results = []
        # Try Gogo mirrors first
        for base in GOGO_MIRRORS:
            try:
                res = ghost_get(f"{base}/filter?keyword={query.replace(' ', '-')}")
                matches = re.findall(r'<a href="/category/([^"]+)" title="([^"]+)">', res.text)
                results.extend([{"id": m[0], "title": m[1], "source": "Gogo"} for m in matches])
                if results: break # Stop if we found stuff
            except: continue
        return results

    @staticmethod
    def get_stream(ep_id):
        """Finds the best available m3u8 link."""
        for base in GOGO_MIRRORS:
            try:
                page = ghost_get(f"{base}/{ep_id}")
                embed = re.search(r'data-video="([^"]+)"', page.text).group(1)
                v_id = embed.split('id=')[-1].split('&')[0]
                ajax = ghost_get(f"https://tt-api.com/encrypt-ajax.php?id={v_id}&alias={v_id}").json()
                dec = decrypt_gogo(ajax['data'])
                return re.search(r'"(https://[^"]+\.m3u8)"', dec).group(1)
            except: continue
        return None

# --- APP UI & ROUTES ---

@app.route('/')
def home():
    return render_template_string('''
    <body style="background:#080808; color:#00ffcc; font-family:'Courier New', monospace; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; margin:0; text-align:center;">
        <h1 style="text-shadow: 0 0 20px #00ffcc; font-size:3rem;">GHOST_V4</h1>
        <p style="color:#666;">MULTI-FAILOVER ENABLED | PROXY BYPASS ACTIVE</p>
        <form action="/search" style="margin-top:20px;">
            <input name="q" placeholder="SEARCH SYSTEM..." style="padding:15px; width:350px; background:#111; border:2px solid #00ffcc; color:#fff; border-radius:30px; outline:none; font-weight:bold; text-align:center;">
            <br><button style="margin-top:20px; padding:12px 40px; background:#00ffcc; border:none; color:#000; font-weight:bold; border-radius:30px; cursor:pointer; box-shadow: 0 0 15px #00ffcc;">EXECUTE BYPASS</button>
        </form>
    </body>
    ''')

@app.route('/search')
def search():
    q = request.args.get('q')
    results = MultiProvider.search(q)
    html = '<body style="background:#080808; color:#fff; font-family:sans-serif; padding:20px;">'
    html += f'<h2>TARGETS FOUND: {len(results)}</h2>'
    for r in results:
        html += f'''<div style="margin:15px 0; padding:20px; background:#111; border-radius:10px; border-left:5px solid #00ffcc;">
                    <a href="/info/{r["id"]}" style="color:#00ffcc; text-decoration:none; font-size:1.2rem; font-weight:bold;">{r["title"]}</a>
                    <span style="float:right; color:#444;">via {r["source"]}</span></div>'''
    return html

@app.route('/info/<slug>')
def info(slug):
    for base in GOGO_MIRRORS:
        try:
            res = ghost_get(f"{base}/category/{slug}")
            m_id = re.search(r'id="movie_id" value="([^"]+)"', res.text).group(1)
            ep_res = ghost_get(f"https://ajax.gogocdn.com/ajax/load-list-episode?ep_start=0&ep_end=3000&id={m_id}")
            eps = re.findall(r'href="/([^"]+)"', ep_res.text)
            html = f'<body style="background:#080808; color:#fff; font-family:sans-serif; padding:20px;"><h3>EPISODE_INDEX: {slug}</h3>'
            html += '<div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(80px, 1fr)); gap:10px;">'
            for e in reversed(eps):
                num = e.split("-episode-")[-1]
                html += f'<a href="/watch/{e}" style="padding:15px; background:#111; color:#00ffcc; border:1px solid #333; text-decoration:none; text-align:center; border-radius:5px;">{num}</a>'
            return html + '</div></body>'
        except: continue
    return "FAIL: NO MIRROR RESPONDING"

@app.route('/watch/<ep_id>')
def watch(ep_id):
    m3u8 = MultiProvider.get_stream(ep_id)
    if not m3u8: return "BYPASS_FAILED: STREAM ENCRYPTED OR DOWN"
    
    # We feed the M3U8 into our proxy for absolute functionality
    proxy_url = f"/proxy?url={m3u8}"
    
    return render_template_string('''
    <html>
        <head><script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script></head>
        <body style="margin:0; background:#000;">
            <video id="v" controls autoplay style="width:100vw; height:100vh;"></video>
            <script>
                var video = document.getElementById('v');
                if (Hls.isSupported()) {
                    var hls = new Hls();
                    hls.loadSource("{{url}}");
                    hls.attachMedia(video);
                }
            </script>
        </body>
    </html>
    ''', url=proxy_url)

# Health check for Deployment Platforms (Render/Railway/Koyeb)
@app.route('/health')
def health():
    return "ALIVE", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
