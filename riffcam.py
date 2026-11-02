"""RiffCam: film yourself playing along with a song, in one window.

REAPER is the amp and the recorder: it plays the song from the point you pick and records your dry guitar in
sync with it (REAPER compensates its own latency). OBS films the webcam and also records the same dry guitar;
matching the two guitar recordings lines the video up with REAPER. After a take you keep or bin it, trim it,
and export -> Videos\\RiffCam\\*.mp4.

python riffcam.py --test   checks the alignment math and renders the template project through REAPER.
"""
import base64, ctypes, functools, hashlib, json, os, re, secrets, shutil, subprocess, sys, threading, time, wave, winreg, winsound
import tkinter as tk
from tkinter import filedialog, ttk
import numpy as np
import imageio_ffmpeg
import websocket
import yt_dlp

APP = "RiffCam"
BUNDLE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))  # template.rpp (+ deno.exe in the .exe build)
DATA = os.path.join(os.environ["APPDATA"], APP)
AMP = os.path.join(DATA, "amp.rpp")  # the live REAPER project: your amp plugin lives on its armed track
SONGS, TMP = os.path.join(DATA, "songs"), os.path.join(DATA, "tmp")
SETTINGS = os.path.join(DATA, "settings.json")
OUT = os.path.join(os.environ["USERPROFILE"], "Videos", APP)
RAW = os.path.join(OUT, "raw")
WS_CFG = os.path.join(os.environ["APPDATA"], "obs-studio", "plugin_config", "obs-websocket", "config.json")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
DENO = os.path.join(BUNDLE, "deno.exe")  # JS runtime yt-dlp needs for YouTube; on PATH when run from source
NOWIN = subprocess.CREATE_NO_WINDOW
CAM, GTR = "RiffCam Camera", "RiffCam Guitar"  # OBS source names
SR = 48000
W, H = 560, 315
BG = "#111"


def installed(key, exe, default):
    """Program path from the registry (REAPER's and OBS's installers both write it), else the default location."""
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, rf"SOFTWARE\{key}") as k:
                path = os.path.join(winreg.QueryValue(k, None), exe)
                if os.path.exists(path):
                    return path
        except OSError:
            pass
    return default if os.path.exists(default) else None


REAPER = installed("REAPER", "reaper.exe", r"C:\Program Files\REAPER (x64)\reaper.exe")
OBS = installed("OBS Studio", r"bin\64bit\obs64.exe", r"C:\Program Files\obs-studio\bin\64bit\obs64.exe")

# Lua run inside the open REAPER (reaper.exe -nonewinst script.lua). "Armed" track = the live amp.
LUA_HEAD = (
    'local function armed() for i = 0, reaper.CountTracks(0) - 1 do local t = reaper.GetTrack(0, i) '
    'if reaper.GetMediaTrackInfo_Value(t, "I_RECARM") == 1 then return t end end end\n'
    'local function backing() for i = 0, reaper.CountTracks(0) - 1 do local t = reaper.GetTrack(0, i) '
    'local _, n = reaper.GetTrackName(t) if n == "Backing" then return t end end end\n'
    'local function clear(t) for i = reaper.CountTrackMediaItems(t) - 1, 0, -1 do '
    'reaper.DeleteTrackMediaItem(t, reaper.GetTrackMediaItem(t, i)) end end\n'
    'local function put(path, s) local f = io.open(path .. ".tmp", "w") f:write(s) f:close() os.remove(path) '
    'os.rename(path .. ".tmp", path) end\n')
LUA_SHOW = LUA_HEAD + ('local t = armed() or reaper.GetTrack(0, 0) '
                       'if t then reaper.TrackFX_Show(t, 0, reaper.TrackFX_GetCount(t) > 0 and 3 or 1) end\n')
LUA_RECORD = LUA_HEAD + """
local song, start = [[{song}]], {start}
local g, b = armed(), backing()
if not g then return end
clear(g)
if song ~= "" then
  if not b then
    reaper.InsertTrackAtIndex(0, true) b = reaper.GetTrack(0, 0)
    reaper.GetSetMediaTrackInfo_String(b, "P_NAME", "Backing", true)
  end
  clear(b)
  local it = reaper.AddMediaItemToTrack(b) local tk = reaper.AddTakeToMediaItem(it)
  local src = reaper.PCM_Source_CreateFromFile(song) reaper.SetMediaItemTake_Source(tk, src)
  reaper.SetMediaItemInfo_Value(it, "D_POSITION", 0)
  reaper.SetMediaItemInfo_Value(it, "D_LENGTH", (reaper.GetMediaSourceLength(src)))
elseif b then clear(b) end
reaper.UpdateArrange()
reaper.SetEditCurPos(start, true, false)
reaper.Main_OnCommand(1013, 0) -- Transport: Record
put([[{ok}]], "ok")
"""
LUA_STOP = LUA_HEAD + """
reaper.Main_OnCommand(40667, 0) -- Transport: Stop (save all recorded media), no prompt
local g = armed()
local it, info = g and reaper.GetTrackMediaItem(g, 0), "||||"
reaper.Main_SaveProject(0, false)
if it then
  -- copy of the project WITH the take: rendering it gives exactly what you heard (faders, pan, master FX...)
  local _, projfn = reaper.EnumProjects(-1)
  local copy = (projfn:gsub("[^\\\\/]+$", "_render.rpp")) -- same folder: recorded media paths are relative
  local f = io.open(projfn, "rb") local s = f:read("a") f:close()
  f = io.open(copy, "wb") f:write(s) f:close()
  local tk = reaper.GetActiveTake(it)
  info = reaper.GetMediaSourceFileName(reaper.GetMediaItemTake_Source(tk)) .. "|" ..
         reaper.GetMediaItemInfo_Value(it, "D_POSITION") .. "|" .. reaper.GetMediaItemInfo_Value(it, "D_LENGTH") ..
         "|" .. reaper.GetMediaItemTakeInfo_Value(tk, "D_STARTOFFS") .. "|" .. copy
  reaper.DeleteTrackMediaItem(g, it) -- the wav stays on disk; the live project stays clean
  reaper.UpdateArrange()
  reaper.Main_SaveProject(0, false)
end
put([[{out}]], info)
"""


class OBSClient:
    """Minimal obs-websocket v5 client: requests only, no events."""

    def __init__(self, port, password):
        self.ws = websocket.create_connection(f"ws://localhost:{port}", timeout=10)
        hello = json.loads(self.ws.recv())["d"]
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        if "authentication" in hello:
            a = hello["authentication"]
            secret = base64.b64encode(hashlib.sha256((password + a["salt"]).encode()).digest()).decode()
            ident["authentication"] = base64.b64encode(hashlib.sha256((secret + a["challenge"]).encode()).digest()).decode()
        self.ws.send(json.dumps({"op": 1, "d": ident}))
        if json.loads(self.ws.recv())["op"] != 2:
            raise ConnectionError("OBS refused the connection")
        self.n = 0

    def __call__(self, request, **data):
        self.n += 1
        rid = str(self.n)
        self.ws.send(json.dumps({"op": 6, "d": {"requestType": request, "requestId": rid, "requestData": data}}))
        while (m := json.loads(self.ws.recv()))["op"] != 7 or m["d"]["requestId"] != rid:
            pass
        status = m["d"]["requestStatus"]
        if not status["result"]:
            raise RuntimeError(f"OBS {request}: {status.get('comment') or status['code']}")
        return m["d"].get("responseData", {})


def ws_config():
    """Turn on OBS's built-in websocket server. OBS must be closed: it rewrites this file on exit."""
    try:
        cfg = json.load(open(WS_CFG, encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {"server_port": 4455, "auth_required": True, "server_password": secrets.token_urlsafe(12),
               "alerts_enabled": False, "first_load": False}
    cfg["server_enabled"] = True
    os.makedirs(os.path.dirname(WS_CFG), exist_ok=True)
    json.dump(cfg, open(WS_CFG, "w", encoding="utf-8"), indent=2)
    return cfg


def obs_connect():
    cfg = json.load(open(WS_CFG, encoding="utf-8"))
    pw = cfg.get("server_password", "") if cfg.get("auth_required") else ""
    for _ in range(60):
        try:
            obs = OBSClient(cfg.get("server_port", 4455), pw)
            obs("GetProfileList")  # the socket opens before OBS can take requests ("not ready", code 207)
            return obs
        except Exception:
            time.sleep(1)


def obs_prepare(obs):
    """RiffCam profile + scene collection with the camera and the guitar input (created once, reused after).
    Returns the cameras and audio inputs OBS can see, as [(name, id)]."""
    p = obs("GetProfileList")
    if APP not in p["profiles"]:
        obs("CreateProfile", profileName=APP)
    elif p["currentProfileName"] != APP:
        obs("SetCurrentProfile", profileName=APP)
    for cat, name, val in (("Output", "Mode", "Simple"), ("SimpleOutput", "FilePath", RAW),
                           ("SimpleOutput", "RecFormat2", "mkv"), ("SimpleOutput", "RecQuality", "Small"),
                           ("SimpleOutput", "RecTracks", "1")):
        obs("SetProfileParameter", parameterCategory=cat, parameterName=name, parameterValue=val)
    c = obs("GetSceneCollectionList")
    if APP not in c["sceneCollections"]:
        obs("CreateSceneCollection", sceneCollectionName=APP)
    elif c["currentSceneCollectionName"] != APP:
        obs("SetCurrentSceneCollection", sceneCollectionName=APP)
    scene = obs("GetCurrentProgramScene")["currentProgramSceneName"]
    have = {i["inputName"] for i in obs("GetInputList")["inputs"]}
    for name, kind in ((CAM, "dshow_input"), (GTR, "wasapi_input_capture")):
        if name not in have:
            obs("CreateInput", sceneName=scene, inputName=name, inputKind=kind, inputSettings={})
    obs("SetInputAudioTracks", inputName=GTR, inputAudioTracks={str(i): i == 1 for i in range(1, 7)})
    for name in obs("GetSpecialInputs").values():  # default desktop/mic sources would leak into the guitar track
        if name:
            obs("SetInputMute", inputName=name, inputMuted=True)
    v = obs("GetVideoSettings")
    item = obs("GetSceneItemId", sceneName=scene, sourceName=CAM)["sceneItemId"]
    obs("SetSceneItemTransform", sceneName=scene, sceneItemId=item, sceneItemTransform={
        "positionX": 0, "positionY": 0, "alignment": 5, "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
        "boundsWidth": v["baseWidth"], "boundsHeight": v["baseHeight"]})

    def items(src, prop):
        found = obs("GetInputPropertiesListPropertyItems", inputName=src, propertyName=prop)["propertyItems"]
        return [(i["itemName"], i["itemValue"]) for i in found if i["itemValue"]]
    return items(CAM, "video_device_id"), items(GTR, "device_id")


def obs_apply(obs, s):
    obs("SetInputSettings", inputName=CAM, inputSettings={"video_device_id": s["camera"]})
    obs("SetInputSettings", inputName=GTR, inputSettings={"device_id": s["audio"]})


def close_obs():
    """Polite close of OBS's main window only. A forced kill makes OBS show "crash detected" next start,
    and taskkill also closes its hidden docks, each of which then pops a "close this dock?" dialog."""
    u, buf = ctypes.windll.user32, ctypes.create_unicode_buffer(256)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def each(hwnd, _):
        u.GetWindowTextW(hwnd, buf, 256)
        if re.match(r"OBS \d+\.\d+.* - ", buf.value):  # "OBS 32.2.2 - Profile: RiffCam - Scenes: RiffCam"
            u.PostMessageW(hwnd, 0x10, 0, 0)  # WM_CLOSE
        return True
    u.EnumWindows(each, 0)


def load_settings():
    try:
        return json.load(open(SETTINGS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ff(*args):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *map(str, args)], check=True, creationflags=NOWIN)


def running(exe):
    out = subprocess.run(["tasklist", "/fi", f"imagename eq {exe}"], capture_output=True, text=True, creationflags=NOWIN).stdout
    return exe.lower() in out.lower()


def reaper_lua(name, code):
    path = os.path.join(TMP, f"{name}.lua")
    open(path, "w", encoding="utf-8").write(code)
    subprocess.Popen([REAPER, "-nonewinst", path])


def wait_for(path, secs):
    for _ in range(int(secs * 10)):
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


def fresh(path):
    if os.path.exists(path):
        os.remove(path)
    return path


def fmt(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def block(text, tag):
    """The <TAG ... > block from REAPER chunk text, nested blocks included (works indented or flat)."""
    lines = text.splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.lstrip().startswith("<" + tag))
    depth = 0
    for j in range(start, len(lines)):
        s = lines[j].strip()
        depth += s.startswith("<") - (s == ">")
        if depth == 0:
            return "".join(lines[start:j + 1])


def fetch_song(url):
    """YouTube (or any site yt-dlp knows) link -> (title, songs/<id>.wav). Downloads are cached."""
    opts = {"format": "bestaudio/best", "outtmpl": os.path.join(SONGS, "%(id)s.%(ext)s"), "noplaylist": True,
            "quiet": True, "no_warnings": True, "noprogress": True}
    if os.path.exists(DENO):
        opts["js_runtimes"] = {"deno": {"path": DENO}}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
    wav = os.path.join(SONGS, info["id"] + ".wav")
    if not os.path.exists(wav):
        ff("-i", info["requested_downloads"][0]["filepath"], "-ar", SR, "-ac", 2, wav)
    return info["title"], wav


def song_from_file(path):
    wav = os.path.join(SONGS, os.path.splitext(os.path.basename(path))[0] + ".wav")
    ff("-i", path, "-ar", SR, "-ac", 2, wav)
    return os.path.basename(path), wav


def wav_len(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


def play(wav, start, length):
    """Snippet through Windows audio. Returns at once."""
    winsound.PlaySound(None, 0)  # stop the previous one, frees the file
    snip = os.path.join(TMP, "snip.wav")
    ff("-ss", max(0, start), "-t", length, "-i", wav, snip)
    winsound.PlaySound(snip, winsound.SND_FILENAME | winsound.SND_ASYNC)


def mono8k(path):
    b = subprocess.run([FFMPEG, "-loglevel", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "8000",
                        "-f", "f32le", "-"], capture_output=True, check=True, creationflags=NOWIN).stdout
    return np.frombuffer(b, np.float32)


def find_offset(obs, take, rate=8000):
    """Seconds into `obs` where `take` starts (both hold the same dry guitar) and how clear the match is."""
    n = 1 << int(np.ceil(np.log2(len(obs) + len(take))))
    c = np.fft.irfft(np.fft.rfft(obs, n) * np.conj(np.fft.rfft(take, n)), n)[:len(obs)]
    i = int(np.argmax(c))
    return i / rate, float(c[i] / (np.std(c) + 1e-12))


def frame_ppm(video, t):
    return subprocess.run([FFMPEG, "-loglevel", "error", "-ss", str(max(0.0, t)), "-i", video, "-frames:v", "1",
                           "-vf", f"scale={W}:{H}", "-f", "image2pipe", "-vcodec", "ppm", "-"],
                          capture_output=True, creationflags=NOWIN).stdout


def render(project, start, end, mix):
    """REAPER, headless, renders `project` (a copy of the live one) from `start` to `end` seconds into `mix`."""
    rpp = open(project, encoding="utf-8", errors="replace").read()
    if "<RENDER_CFG" in rpp:
        rpp = rpp.replace(block(rpp, "RENDER_CFG"), "", 1)
    rpp = re.sub(r"^\s*RENDER_(FILE|PATTERN|FMT|RANGE|STEMS|ADDTOPROJ|1X)\b.*\n", "", rpp, flags=re.M)
    head, rest = rpp.split("\n", 1)
    # RENDER_RANGE 0 = custom time bounds; RENDER_STEMS 0 = master mix only
    rpp = (f'{head}\n  RENDER_FILE "{mix}"\n  RENDER_PATTERN ""\n  RENDER_FMT 0 2 {SR}\n  RENDER_RANGE 0 {start} {end} 0 1000\n'
           f"  RENDER_STEMS 0\n  RENDER_ADDTOPROJ 0\n  <RENDER_CFG\n    ZXZhdxgAAQ==\n  >\n{rest}")
    open(project, "w", encoding="utf-8").write(rpp)
    fresh(mix)
    subprocess.run([REAPER, "-newinst", "-nosplash", "-renderproject", project], timeout=900)
    if not os.path.exists(mix):
        raise RuntimeError("REAPER couldn't render the sound")


def peaks(path, cols):
    """Per-column peak of `path` on an absolute scale (1.0 = 0 dBFS), so it reads like REAPER's waveform."""
    b = subprocess.run([FFMPEG, "-loglevel", "error", "-i", path, "-ac", "2", "-ar", "8000", "-f", "f32le", "-"],
                       capture_output=True, check=True, creationflags=NOWIN).stdout
    a = np.abs(np.frombuffer(b, np.float32)).reshape(-1, 2).max(1)
    return [float(c.max()) if len(c) else 0.0 for c in np.array_split(a, cols)]


@functools.cache
def video_encoder():
    """First hardware H.264 encoder that works on this PC (NVIDIA, AMD, Intel), else x264 on the CPU."""
    for enc in ("h264_nvenc", "h264_amf", "h264_qsv"):
        test = subprocess.run([FFMPEG, "-loglevel", "quiet", "-f", "lavfi", "-i", "color=size=640x360:duration=0.2",
                               "-c:v", enc, "-f", "null", "-"], creationflags=NOWIN)
        if test.returncode == 0:
            return [enc, "-b:v", "8M"]
    return ["libx264", "-preset", "veryfast", "-crf", "20"]


def wait_file_done(path):
    size = -1
    while size != os.path.getsize(path):  # OBS may still be finalizing the file
        size = os.path.getsize(path)
        time.sleep(1)


def to_recycle_bin(*paths):
    for p in paths:
        if p and os.path.exists(p):
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                            f"'{p}', 'OnlyErrorDialogs', 'SendToRecycleBin')"], check=True, creationflags=NOWIN)


def F(size, weight="normal"):
    return ("Segoe UI", size, weight)


class App:
    def __init__(self, root):
        self.root, self.obs, self.started, self.live = root, None, 0, True
        self.song, self.song_len, self.take = None, 0.0, None
        self.settings = load_settings()
        root.title(APP)
        root.resizable(False, False)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        for d in (DATA, SONGS, TMP, RAW):
            os.makedirs(d, exist_ok=True)

        self.blank = tk.PhotoImage(width=W, height=H)  # fixes the label size in pixels
        self.view = tk.Label(root, bg="#000", fg="#888", text="camera…", image=self.blank, compound="center", font=F(12))
        self.view.pack(padx=12, pady=(12, 6))

        small = dict(font=F(10), bg="#333", fg="white", activebackground="#444", activeforeground="white", relief="flat", padx=8)
        self.small = small
        lbl = dict(bg=BG, fg="#ddd", font=F(10))

        # song: where it comes from and where to start
        self.songbar = tk.Frame(root, bg=BG)
        r1 = tk.Frame(self.songbar, bg=BG)
        r1.pack(fill="x", pady=2)
        self.b_link = tk.Button(r1, text="📋 Paste link", command=self.paste_link, **small)
        self.b_file = tk.Button(r1, text="📂 File", command=self.pick_file, **small)
        self.b_link.pack(side="left")
        self.b_file.pack(side="left", padx=6)
        self.song_lbl = tk.Label(r1, text="No song: guitar only", anchor="w", width=44, **lbl)
        self.song_lbl.pack(side="left", padx=6)
        r2 = tk.Frame(self.songbar, bg=BG)
        r2.pack(fill="x", pady=2)
        tk.Label(r2, text="Start at", **lbl).pack(side="left")
        self.pos = tk.Scale(r2, from_=0, to=0, resolution=0.5, orient="horizontal", showvalue=False, length=370,
                            bg=BG, fg="white", troughcolor="#333", highlightthickness=0, bd=0,
                            command=lambda v: self.pos_lbl.config(text=fmt(float(v))))
        self.pos.pack(side="left", padx=6)
        self.pos_lbl = tk.Label(r2, text="0:00.0", width=7, **lbl)
        self.pos_lbl.pack(side="left")
        self.b_listen = tk.Button(r2, text="▶ 5 s", command=self.listen, **small)
        self.b_listen.pack(side="left", padx=6)
        self.song_widgets = (self.b_link, self.b_file, self.pos, self.b_listen)

        # trim: waveform with a playhead and start/end markers, plays the video with the sound
        self.trimbar = tk.Frame(root, bg=BG)
        self.wave = tk.Canvas(self.trimbar, width=W, height=70, bg="#1b1b1b", highlightthickness=0)
        self.wave.pack()
        self.wave.bind("<Button-1>", self.wave_press)
        self.wave.bind("<B1-Motion>", self.wave_move)
        self.wave.bind("<ButtonRelease-1>", self.wave_release)
        self.wave.bind("<Motion>", lambda e: self.wave.config(cursor="sb_h_double_arrow" if self.near_mark(e.x) else "hand2"))
        r3 = tk.Frame(self.trimbar, bg=BG)
        r3.pack(fill="x", pady=4)
        self.b_play = tk.Button(r3, text="▶", width=3, command=self.toggle_play, **small)
        self.b_play.pack(side="left")
        tk.Button(r3, text="⟦ Start here", command=lambda: self.set_mark("a", self.ph), **small).pack(side="left", padx=6)
        tk.Button(r3, text="End here ⟧", command=lambda: self.set_mark("b", self.ph), **small).pack(side="left")
        self.trim_lbl = tk.Label(r3, text="", **lbl)
        self.trim_lbl.pack(side="left", padx=10)
        root.bind("<space>", lambda e: self.cur_mode == "trim" and self.toggle_play())
        self.playing, self.vid, self.frame_busy, self.snip_n = None, None, False, 0

        bar = tk.Frame(root, bg=BG)
        self.bar = bar
        big = dict(font=F(18, "bold"), fg="white", relief="flat", activeforeground="white", width=9)
        self.rec = tk.Button(bar, text="● REC", bg="#c62828", activebackground="#8e0000", command=self.toggle, **big)
        self.tone = tk.Button(bar, text="🎛 Amp", bg="#37474f", activebackground="#263238", command=self.show_tone, **big)
        self.gear = tk.Button(bar, text="⚙", bg="#37474f", activebackground="#263238", command=self.reconfigure,
                              **{**big, "width": 2})
        self.keep = tk.Button(bar, text="✓ Keep", bg="#2e7d32", activebackground="#1b5e20", command=self.process, **big)
        self.drop = tk.Button(bar, text="✗ Delete", bg="#555", activebackground="#333", command=self.delete, **big)
        self.done = tk.Button(bar, text="✓ Export", bg="#2e7d32", activebackground="#1b5e20", command=self.export, **big)
        self.msg = tk.Label(root, text="Starting REAPER and OBS…", font=F(11), bg=BG, fg="#ddd", wraplength=W, justify="center")
        self.mode("busy")
        threading.Thread(target=self.connect, daemon=True).start()

    # ---------- plumbing ----------
    def ui(self, fn):
        self.root.after(0, fn)

    def say(self, text):
        self.ui(lambda: self.msg.config(text=text))

    def mode(self, m):
        """idle (song + record) / stopped (keep or bin) / trim (cut and export) / busy."""
        for w in (self.songbar, self.trimbar, self.bar, self.msg, self.rec, self.tone, self.gear, self.keep, self.drop, self.done):
            w.pack_forget()
        self.stop_play()
        self.cur_mode = m
        self.live = m != "trim"
        (self.trimbar if m == "trim" else self.songbar).pack(padx=12, fill="x")
        self.bar.pack(padx=12, pady=8)
        buttons = {"idle": (self.rec, self.tone, self.gear), "stopped": (self.keep, self.drop),
                   "trim": (self.done, self.drop), "busy": ()}[m]
        for b in buttons:
            b.config(state="normal")
            b.pack(side="left", padx=6)
        for w in self.song_widgets:
            w.config(state="normal" if m == "idle" else "disabled")
        self.msg.pack(padx=12, pady=(0, 12))

    def connect(self):
        missing = [n for n, p in (("REAPER (reaper.fm)", REAPER), ("OBS Studio (obsproject.com)", OBS)) if not p]
        if missing:
            self.say("RiffCam needs " + " and ".join(missing) + ". Install it, then start RiffCam again.")
            return
        if not os.path.exists(AMP):
            shutil.copy(os.path.join(BUNDLE, "template.rpp"), AMP)
        if running("obs64.exe"):  # an OBS left running across sleep loses the webcam: always start a fresh one
            close_obs()
            for _ in range(40):
                if not running("obs64.exe"):
                    break
                time.sleep(0.5)
            else:
                self.say("Couldn't close the running OBS. Close it yourself (also in the tray by the clock) and start RiffCam again.")
                return
        if not running("reaper.exe"):
            subprocess.Popen([REAPER, AMP])
        ws_config()
        subprocess.Popen([OBS, "--collection", APP, "--profile", APP, "--minimize-to-tray", "--disable-shutdown-check"],
                         cwd=os.path.dirname(OBS))
        self.obs = obs_connect()
        if not self.obs:
            self.say("OBS isn't answering. Close OBS completely (also in the tray by the clock) and start RiffCam again.")
            return
        self.configure_obs(ask=False)
        threading.Thread(target=self.preview, daemon=True).start()

    def configure_obs(self, ask):
        """Make sure OBS has our profile/scene; ask for camera + guitar input when unknown (or when `ask`)."""
        try:
            cams, mics = obs_prepare(self.obs)
            s = self.settings
            if ask or s.get("camera") not in {v for _, v in cams} or s.get("audio") not in {v for _, v in mics}:
                done = threading.Event()
                self.ui(lambda: self.setup_dialog(cams, mics, done))
                done.wait()
            obs_apply(self.obs, self.settings)
        except Exception as e:
            self.say(f"OBS setup failed: {e}")
            return
        self.say("Paste a song link (or pick a file), choose where to start, and hit REC.\n"
                 "If REAPER shows its license window, close it.")
        self.ui(lambda: self.mode("idle"))

    def setup_dialog(self, cams, mics, done):
        s = self.settings
        d = tk.Toplevel(self.root, bg=BG, padx=16, pady=12)
        d.title(f"{APP} setup")
        d.transient(self.root)
        d.resizable(False, False)
        lbl = dict(bg=BG, fg="#ddd", font=F(10), anchor="w", justify="left")

        def pick(label, options, current):
            tk.Label(d, text=label, **lbl).pack(fill="x", pady=(8, 2))
            box = ttk.Combobox(d, values=[n for n, _ in options], state="readonly", width=60)
            ids = [v for _, v in options]
            box.current(ids.index(current) if current in ids else 0)
            box.pack(fill="x")
            return lambda: ids[box.current()]
        cam = pick("Camera", cams, s.get("camera"))
        mic = pick("Guitar input: your audio interface's input the guitar is plugged into", mics, s.get("audio"))
        tk.Label(d, text="Video delay in ms (raise it if the picture lags behind the sound)", **lbl).pack(fill="x", pady=(8, 2))
        lag = tk.Spinbox(d, from_=0, to=1000, increment=10, width=8)
        lag.delete(0, "end")
        lag.insert(0, s.get("video_lag_ms", 0))
        lag.pack(anchor="w")
        tk.Label(d, wraplength=480, **lbl, text=(
            "\nIn REAPER (once): Options > Preferences > Audio > Device: pick your interface's ASIO driver. "
            "Then on the armed track ('Guitar'), choose the input your guitar is on and add your amp plugin "
            "with the FX button (or the 🎛 Amp button here). RiffCam records whatever that track sounds like.")).pack(fill="x")

        def save():
            try:
                lag_ms = int(lag.get())
            except ValueError:
                lag_ms = 0
            s.update(camera=cam(), audio=mic(), video_lag_ms=lag_ms)
            json.dump(s, open(SETTINGS, "w", encoding="utf-8"), indent=2)
            d.destroy()
            done.set()
        tk.Button(d, text="Save", command=save, **{**self.small, "font": F(11, "bold"), "bg": "#2e7d32"}).pack(pady=(12, 0))
        d.protocol("WM_DELETE_WINDOW", save)
        d.grab_set()

    def reconfigure(self):
        self.mode("busy")
        threading.Thread(target=self.configure_obs, args=(True,), daemon=True).start()

    def preview(self):
        # OBS screenshots over websocket manage only ~4 fps, so read OBS Virtual Camera with ffmpeg instead (~25 fps)
        try:
            obs = obs_connect()  # own connection: one per thread
            if not obs("GetVirtualCamStatus")["outputActive"]:
                obs("StartVirtualCam")
        except Exception:
            return self.ui(lambda: self.view.config(image=self.blank, text="no preview (OBS Virtual Camera unavailable)"))
        time.sleep(1)
        self.ffplay = subprocess.Popen([FFMPEG, "-loglevel", "quiet", "-f", "dshow", "-i", "video=OBS Virtual Camera",
                                        "-vf", f"scale={W}:{H}", "-r", "25", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                        "pipe:1"], stdout=subprocess.PIPE, creationflags=NOWIN)
        size, head = W * H * 3, f"P6 {W} {H} 255\n".encode()
        while len(buf := self.ffplay.stdout.read(size)) == size:
            if self.live and not self.frame_busy:  # drop frames instead of piling them up if Tk is behind
                self.frame_busy = True
                self.ui(lambda d=head + buf: self.show_frame(d))
        self.ui(lambda: self.view.config(image=self.blank, text="no preview"))

    def show_frame(self, data, still=False):
        if self.live or still:
            self.frame = tk.PhotoImage(data=data, format="ppm")  # keep a reference or Tk drops it
            self.view.config(image=self.frame, text="")
        self.frame_busy = False

    def close(self):
        self.stop_play()
        try:
            if self.started:
                reaper_lua("stop", LUA_STOP.format(out=os.path.join(TMP, "take.txt")))
                self.obs("StopRecord")  # the take stays in raw\
            self.obs("StopVirtualCam")
            for _ in range(25):  # with an output still active OBS asks "exit anyway?" and stays open
                if not (self.obs("GetVirtualCamStatus")["outputActive"] or self.obs("GetRecordStatus")["outputActive"]):
                    break
                time.sleep(0.2)
        except Exception:
            pass
        if getattr(self, "ffplay", None):
            self.ffplay.kill()
        close_obs()  # webcam light off
        self.root.destroy()

    # ---------- song ----------
    def paste_link(self):
        try:
            url = self.root.clipboard_get().strip()
        except tk.TclError:
            url = ""
        if not url.startswith("http"):
            self.msg.config(text="No link in the clipboard. Copy the video address in your browser and click again.")
            return
        self.load_song(lambda: fetch_song(url), "Downloading the song…")

    def pick_file(self):
        path = filedialog.askopenfilename(title="Song", filetypes=[("Audio/video", "*.mp3 *.wav *.m4a *.flac *.ogg *.webm *.mp4"), ("All", "*.*")])
        if path:
            self.load_song(lambda: song_from_file(path), "Preparing the song…")

    def load_song(self, get, text):
        self.msg.config(text=text)
        for w in self.song_widgets:
            w.config(state="disabled")

        def go():
            try:
                title, wav = get()
                length = wav_len(wav)

                def done():
                    self.song, self.song_len = wav, length
                    self.song_lbl.config(text=f"🎵 {title}  ({fmt(length)})")
                    self.pos.config(to=length)
                    self.pos.set(0)
                    self.msg.config(text="Song ready. Choose where to start (▶ 5 s to listen) and hit REC.")
                self.ui(done)
            except Exception as e:
                self.say(f"Couldn't get the song: {e}")
            self.ui(lambda: [w.config(state="normal") for w in self.song_widgets])
        threading.Thread(target=go, daemon=True).start()

    def listen(self):
        if self.song:
            threading.Thread(target=play, args=(self.song, self.pos.get(), 5), daemon=True).start()

    def show_tone(self):
        if running("reaper.exe"):
            reaper_lua("show", LUA_SHOW)
        else:
            subprocess.Popen([REAPER, AMP])

    # ---------- recording ----------
    def toggle(self):
        if not running("reaper.exe"):
            self.msg.config(text="REAPER is closed. Click 🎛 Amp to open it, then try again.")
            return
        self.rec.config(state="disabled")
        if not self.started:
            for w in (self.tone, self.gear, *self.song_widgets):
                w.config(state="disabled")
            threading.Thread(target=self.start_take, args=(self.song, self.pos.get()), daemon=True).start()
        else:
            threading.Thread(target=self.stop_take, daemon=True).start()

    def start_take(self, song, start):
        ok = fresh(os.path.join(TMP, "rec.ok"))
        self.obs("StartRecord")  # OBS first: the video must cover the whole REAPER take
        t_obs = time.time()
        time.sleep(0.3)
        reaper_lua("record", LUA_RECORD.format(song=song or "", start=start, ok=ok))
        if not wait_for(ok, 8):
            raw = self.obs("StopRecord")["outputPath"]
            threading.Thread(target=lambda: (wait_file_done(raw), to_recycle_bin(raw)), daemon=True).start()
            self.say("REAPER didn't start recording. Is a dialog open in REAPER (license etc.)? "
                     "Is a track armed for recording? Fix that and try again.")
            self.ui(lambda: self.mode("idle"))
            return
        self.take = {"song": song, "start": start, "guess": time.time() - t_obs}  # rough offset if matching fails
        self.started = time.time()

        def recording():
            self.rec.config(text="■ STOP", bg="#333", state="normal")
            self.tick()
        self.ui(recording)

    def tick(self):
        if self.started:
            s = int(time.time() - self.started)
            self.msg.config(text=f"● Recording  {s // 60}:{s % 60:02d}")
            self.root.after(500, self.tick)

    def stop_take(self):
        self.started = 0
        info = fresh(os.path.join(TMP, "take.txt"))
        reaper_lua("stop", LUA_STOP.format(out=info))
        got = wait_for(info, 15)
        self.take["raw"] = self.obs("StopRecord")["outputPath"]  # after REAPER, so the video covers the whole take
        self.ui(lambda: self.rec.config(text="● REC", bg="#c62828"))
        if got:
            fn, pos, length, offs, proj = open(info, encoding="utf-8", errors="replace").read().split("|")
            if fn:
                self.take.update(fn=fn, pos=float(pos), len=float(length), offs=float(offs), proj=proj)
                self.say("Keep this take or delete it?")
                self.ui(lambda: self.mode("stopped"))
                return
        self.say("REAPER recorded nothing (is the guitar track armed?). The video stayed in raw\\.")
        self.ui(lambda: self.mode("idle"))

    # ---------- keep / bin ----------
    def delete(self):
        t, self.take = self.take, None
        self.mode("busy")

        def go():
            try:
                wait_file_done(t["raw"])
                to_recycle_bin(t["raw"], t.get("mix"))
                self.say("Take moved to the Recycle Bin. Another one?")
            except Exception as e:
                self.say(f"Couldn't delete: {e}")
            try:
                to_recycle_bin(t["fn"])  # REAPER may still hold the dry wav for a moment; it's only a leftover then
            except Exception:
                pass
            self.ui(lambda: self.mode("idle"))
        threading.Thread(target=go, daemon=True).start()

    def process(self):
        self.mode("busy")
        threading.Thread(target=self._process, daemon=True).start()

    def _process(self):
        t = self.take
        try:
            self.say("REAPER is rendering your take with the song…")
            wait_file_done(t["raw"])
            t["mix"] = os.path.join(TMP, "mix.wav")
            render(t["proj"], t["pos"], t["pos"] + t["len"], t["mix"])  # mix time 0 = take start
            self.say("Lining the video up with the sound…")
            obs_a = mono8k(t["raw"])
            k, strength = t["k"], t["strength"] = find_offset(obs_a, mono8k(t["fn"]))
            note = ""
            if strength < 8:  # nothing to match on (e.g. no playing at all): fall back to the timing guess
                k, note = t["guess"], "\n(Couldn't recognise the guitar in the video's audio, so the sync is approximate.)"
            t["o0"] = k + t["offs"] + self.settings.get("video_lag_ms", 0) / 1000  # video time of mix time 0
            t["L"] = wav_len(t["mix"])
            t["amin"] = max(0.0, -t["o0"])
            t["bmax"] = min(t["L"], len(obs_a) / 8000 - t["o0"])
            t["peaks"] = peaks(t["mix"], W)
            self.a, self.b, self.ph = t["amin"], t["bmax"], t["amin"]

            def enter():
                self.mode("trim")
                self.draw_wave()
                self.show_still(self.ph)
                self.msg.config(text="Click the waveform to move the playhead, ▶ or Space to play. Pause where you "
                                     "want and press Start here / End here (or drag the green and red markers). "
                                     "Then ✓ Export." + note)
            self.ui(enter)
        except Exception as e:
            self.say(f"Error: {e}")
            self.ui(lambda: self.mode("stopped"))

    # ---------- trim ----------
    def x(self, t):
        return t / self.take["L"] * W

    def draw_wave(self):
        c = self.wave
        c.delete("all")
        for i, p in enumerate(self.take["peaks"]):  # absolute: full height = 0 dBFS, red = clipping
            h = min(p, 1.0) * 33
            c.create_line(i, 35 - h, i, 35 + h + 1, fill="#e53935" if p >= 0.99 else "#5c8dbc")
        self.draw_marks()

    def draw_marks(self):
        c, t = self.wave, self.take
        c.delete("m")
        for lo, hi in ((0, self.a), (self.b, t["L"])):
            c.create_rectangle(self.x(lo), 0, self.x(hi), 70, fill="#000", stipple="gray50", width=0, tags="m")
        c.create_line(self.x(self.a), 0, self.x(self.a), 70, fill="#4caf50", width=3, tags="m")
        c.create_line(self.x(self.b), 0, self.x(self.b), 70, fill="#e53935", width=3, tags="m")
        c.create_line(0, 0, 0, 70, fill="white", tags=("m", "ph"))
        self.move_ph()

    def move_ph(self):  # cheap enough to run on every video frame
        self.wave.coords("ph", self.x(self.ph), 0, self.x(self.ph), 70)
        self.trim_lbl.config(text=f"▸ {fmt(self.ph)}    clip {fmt(self.a)} – {fmt(self.b)} ({fmt(self.b - self.a)})")

    def near_mark(self, px):
        return self.take and "L" in self.take and min(abs(px - self.x(self.a)), abs(px - self.x(self.b))) <= 8

    def wave_press(self, e):
        self.drag = "ph"
        if self.near_mark(e.x):
            self.drag = "a" if abs(e.x - self.x(self.a)) <= abs(e.x - self.x(self.b)) else "b"
        self.wave_move(e)

    def wave_move(self, e):
        t = min(max(e.x / W * self.take["L"], self.take["amin"]), self.take["bmax"])
        if self.drag == "ph":
            self.ph = t
            self.move_ph()
        else:
            self.set_mark(self.drag, t, still=False)

    def wave_release(self, e):
        if self.drag != "ph" and not self.playing:
            self.ph = self.a if self.drag == "a" else self.b  # so ▶ starts right at the marker you moved
            self.draw_marks()
        if self.drag == "ph" and self.playing:
            self.play_at(self.ph)  # jump while playing
        elif not self.playing:
            self.show_still(self.ph)

    def set_mark(self, which, t, still=True):
        if which == "a":
            self.a = min(t, self.b - 1)
        else:
            self.b = max(t, self.a + 1)
        self.draw_marks()
        if still and not self.playing:
            self.show_still(self.a if which == "a" else self.b)

    def show_still(self, tau):
        t = self.take
        threading.Thread(target=lambda: (d := frame_ppm(t["raw"], t["o0"] + tau)) and
                         self.ui(lambda: self.show_frame(d, still=True)), daemon=True).start()

    # ---------- player: video + sound from the playhead ----------
    def toggle_play(self):
        if self.playing:
            self.stop_play()
        else:
            self.play_at(self.ph)

    def play_at(self, start):
        self.stop_play()
        t = self.take
        end = t["bmax"]
        if start >= end - 0.3:
            start = self.a
        self.playing = run = object()  # identity token: a newer play_at/stop_play ends this one
        self.b_play.config(text="⏸")
        self.snip_n ^= 1  # two files take turns, so a quick re-click never writes the one still being played

        def go(snip=os.path.join(TMP, f"play{self.snip_n}.wav")):
            ff("-ss", start, "-t", end - start, "-i", t["mix"], snip)
            vid = self.vid = subprocess.Popen(
                [FFMPEG, "-loglevel", "quiet", "-ss", str(t["o0"] + start), "-i", t["raw"], "-t", str(end - start),
                 "-vf", f"scale={W}:{H},fps=25", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                stdout=subprocess.PIPE, creationflags=NOWIN)
            size, head = W * H * 3, f"P6 {W} {H} 255\n".encode()
            buf = vid.stdout.read(size)  # first frame decoded -> start the sound now so both begin together
            if self.playing is not run:
                return vid.kill()
            winsound.PlaySound(snip, winsound.SND_FILENAME | winsound.SND_ASYNC)
            t0, i = time.time(), 0
            while len(buf) == size and self.playing is run:
                late = time.time() - (t0 + i / 25)
                if late < 0:
                    time.sleep(-late)
                if late < 0.04 and not self.frame_busy:  # behind schedule: skip showing, keep decoding
                    self.frame_busy = True
                    self.ui(lambda d=head + buf, p=start + i / 25: self.show_play_frame(d, p, run))
                i += 1
                buf = vid.stdout.read(size)
            vid.kill()
            if self.playing is run:  # reached the end on its own
                self.ui(self.stop_play)
        threading.Thread(target=go, daemon=True).start()

    def show_play_frame(self, data, pos, run):
        self.show_frame(data, still=True)
        if self.playing is run:
            self.ph = pos
            self.move_ph()

    def stop_play(self):
        self.playing = None
        winsound.PlaySound(None, 0)
        if self.vid:
            self.vid.kill()
        self.b_play.config(text="▶")

    def export(self):
        self.mode("busy")
        t, a, b = self.take, self.a, self.b

        def go():
            try:
                self.say("Saving the video…")
                name = os.path.splitext(os.path.basename(t["raw"]))[0]
                final = os.path.join(OUT, name + ".mp4")
                ff("-ss", t["o0"] + a, "-i", t["raw"], "-ss", a, "-i", t["mix"], "-t", b - a,
                   "-map", "0:v:0", "-map", "1:a:0", "-r", 30, "-c:v", *video_encoder(),
                   "-c:a", "aac", "-b:a", "320k", final)
                try:  # dry guitar next to the raw video, can be re-amped later
                    shutil.move(t["fn"], os.path.join(RAW, name + " guitar.wav"))
                except OSError:  # REAPER still holds it: copy, the original stays in its Media folder
                    shutil.copy2(t["fn"], os.path.join(RAW, name + " guitar.wav"))
                os.remove(t["mix"])
                self.take = None
                subprocess.Popen(["explorer", "/select,", final])
                self.say(f"Saved: {os.path.basename(final)}\nReady for the next take.")
                self.ui(lambda: self.mode("idle"))
            except Exception as e:
                self.say(f"Error: {e}")
                self.ui(lambda: self.mode("trim"))
        threading.Thread(target=go, daemon=True).start()


def selftest():
    ch = block("<A\n  x\n  <FXCHAIN\n    <VST\n      AAA=\n    >\n  >\n  y\n>\n", "FXCHAIN")
    assert ch.strip().endswith(">") and "VST" in ch and "y" not in ch, ch

    rng = np.random.default_rng(1)
    take = rng.standard_normal(8000 * 5).astype(np.float32) * np.repeat(rng.random(50), 800).astype(np.float32)
    obs = np.concatenate([rng.standard_normal(9872).astype(np.float32) * 0.01, take * 0.7 + 0.01, np.zeros(8000, np.float32)])
    k, s = find_offset(obs, take)
    assert abs(k - 9872 / 8000) < 1 / 8000 and s > 8, (k, s)

    assert video_encoder()[0] in ("h264_nvenc", "h264_amf", "h264_qsv", "libx264")

    # render the template project over a custom time range
    os.makedirs(TMP, exist_ok=True)
    copy, mix = os.path.join(TMP, "_render.rpp"), os.path.join(TMP, "test_mix.wav")
    shutil.copy(os.path.join(BUNDLE, "template.rpp"), copy)
    render(copy, 2.0, 5.0, mix)
    assert abs(wav_len(mix) - 3.0) < 0.05, wav_len(mix)
    assert len(peaks(mix, 10)) == 10
    os.remove(mix)
    result = f"selftest ok, video encoder: {video_encoder()[0]}"
    if "--online" in sys.argv:  # YouTube download through the bundled JS runtime
        title, wav = fetch_song("https://www.youtube.com/watch?v=jNQXAC9IVRw")
        result += f", download ok: {title!r} {wav_len(wav):.1f} s"
    open(os.path.join(TMP, "selftest.txt"), "w", encoding="utf-8").write(result)  # the .exe has no console
    if sys.stdout:
        print(result)


if __name__ == "__main__":
    if "--test" in sys.argv:
        os.makedirs(SONGS, exist_ok=True)
        selftest()
    else:
        root = tk.Tk()
        App(root)
        root.mainloop()
