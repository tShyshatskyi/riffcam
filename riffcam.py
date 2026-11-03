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
from PIL import ImageTk
import ctypes.wintypes
import skin


def dpi_scale():
    """Draw at the screen's real pixel density instead of letting Windows stretch (and blur) the window."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return ctypes.windll.user32.GetDpiForSystem() / 96
    except Exception:
        return 1.0


K = skin.Z = dpi_scale()


def k(v):
    return round(v * K)


W, H = k(496), k(279)  # camera picture

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


def load_fonts():
    """Make the bundled fonts usable by Tk, for this process only."""
    for f in os.listdir(skin.FONTS):
        if f.endswith(".ttf"):
            ctypes.windll.gdi32.AddFontResourceExW(os.path.join(skin.FONTS, f), 0x10, 0)  # FR_PRIVATE


def UI(size):
    return ("Russo One", size)


def LCD(size):
    return ("VT323", size)


# window layout, in design pixels (k() turns them into real ones)
WIN_W, WIN_H = 536, 610
HEADER = (0, 0, 536, 52)
PV = (20, 64)  # camera picture, W x H
BEZEL = (8, 54, 528, 372)
PANEL = (8, 380, 528, 482)
ROW = 522  # big buttons
LCD_BOX = (8, 562, 528, 604)
WAVE_H = k(50)
ACHIEVEMENTS = {1: "FIRST BLOOD", 5: "WARMED UP", 10: "ON FIRE", 25: "AXE MASTER", 50: "FRETBOARD LEGEND", 100: "ROCK GOD"}


def kbox(box):
    return tuple(k(v) for v in box)


class Sprite:
    """A skinned button on a canvas: one image per state, rendered on first use."""

    def __init__(self, c, x, y, render, command):
        self.c, self.command, self.on, self.state = c, command, True, "normal"
        self.id = c.create_image(x, y, state="hidden")
        self.skin(render)
        c.tag_bind(self.id, "<Enter>", lambda e: self.paint("hover"))
        c.tag_bind(self.id, "<Leave>", lambda e: self.paint("normal"))
        c.tag_bind(self.id, "<ButtonPress-1>", lambda e: self.paint("pressed"))
        c.tag_bind(self.id, "<ButtonRelease-1>", self.release)

    def skin(self, render):
        self.render, self.imgs = render, {}
        self.paint(self.state)

    def paint(self, state):
        self.state = state
        shown = state if self.on else "disabled"
        if shown not in self.imgs:
            self.imgs[shown] = ImageTk.PhotoImage(self.render(shown))
        self.c.itemconfig(self.id, image=self.imgs[shown])
        self.c.config(cursor="hand2" if self.on and state != "normal" else "")

    def release(self, e):
        if self.on and self.state == "pressed":
            self.paint("hover")
            self.command()

    def show(self, on):
        self.c.itemconfig(self.id, state="normal" if on else "hidden")

    def enable(self, on):
        self.on = on
        self.paint("normal")


class Slider:
    """Neon slider with a chrome knob."""

    def __init__(self, c, x0, y, length, command):
        self.c, self.x0, self.y, self.len, self.command = c, x0, y, length, command
        self.to, self.value, self.on = 0.0, 0.0, True
        self.track = c.create_rectangle(x0, y - k(4), x0 + length, y + k(4), fill="#030603", outline="#000", width=k(2))
        self.fill = c.create_rectangle(x0, y - k(3), x0, y + k(3), fill=skin.ACID_HEX, outline="")
        self.knob_img = ImageTk.PhotoImage(skin.knob(k(16)))
        self.knob = c.create_image(x0, y, image=self.knob_img)
        for item in (self.track, self.fill, self.knob):
            c.tag_bind(item, "<ButtonPress-1>", self.drag)
            c.tag_bind(item, "<B1-Motion>", self.drag)

    def drag(self, e):
        if self.on and self.to:
            self.set((e.x - self.x0) / self.len * self.to)

    def set(self, v):
        self.value = round(min(max(v, 0.0), self.to) * 2) / 2  # half-second steps
        x = self.x0 + (self.value / self.to * self.len if self.to else 0)
        self.c.coords(self.fill, self.x0, self.y - k(3), x, self.y + k(3))
        self.c.coords(self.knob, x, self.y)
        self.command(self.value)

    def get(self):
        return self.value

    def show(self, on):
        for item in (self.track, self.fill, self.knob):
            self.c.itemconfig(item, state="normal" if on else "hidden")

    def enable(self, on):
        self.on = on
        self.c.itemconfig(self.fill, fill=skin.ACID_HEX if on else "#3a4a2a")


class App:
    def __init__(self, root):
        self.root, self.obs, self.started, self.live = root, None, 0, True
        self.song, self.song_len, self.take = None, 0.0, None
        self.playing, self.vid, self.frame_busy, self.snip_n = None, None, False, 0
        self.settings = load_settings()
        root.title(APP)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.iconbitmap(os.path.join(BUNDLE, "riffcam.ico"))
        for d in (DATA, SONGS, TMP, RAW):
            os.makedirs(d, exist_ok=True)

        c = self.c = tk.Canvas(root, width=k(WIN_W), height=k(WIN_H), highlightthickness=0, bg="#000")
        c.pack()
        wa = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)  # SPI_GETWORKAREA
        root.geometry(f"+{max(0, (wa.right - k(WIN_W)) // 2)}+{max(0, (wa.bottom - k(WIN_H) - k(32)) // 2)}")
        px, py0 = k(PV[0]), k(PV[1])
        self.bg = ImageTk.PhotoImage(skin.background(k(WIN_W), k(WIN_H), kbox(HEADER), kbox(BEZEL), (px, py0, W, H),
                                                     kbox(PANEL), kbox(LCD_BOX)))
        c.create_image(0, 0, image=self.bg, anchor="nw")
        self.logo = ImageTk.PhotoImage(skin.chrome_text(APP, k(40)))
        c.create_image(k(2), k(26), image=self.logo, anchor="w")
        c.create_text(k(WIN_W - 16), k(13), text="GAMERSCORE", font=UI(7), fill="#8a9098", anchor="e")
        self.score = c.create_text(k(WIN_W - 14), k(33), text="", font=LCD(24), fill=skin.ACID_HEX, anchor="e")
        self.show_score()

        # camera screen: picture, scanlines, REC light, achievement toast
        self.blank = ImageTk.PhotoImage(skin.static(W, H))
        self.view = c.create_image(px, py0, image=self.blank, anchor="nw")
        self.crt = ImageTk.PhotoImage(skin.crt_overlay(W, H))
        c.create_image(px, py0, image=self.crt, anchor="nw")
        self.rec_light = c.create_text(px + k(16), py0 + k(20), text="", font=LCD(26), fill=skin.HOT_HEX, anchor="w")
        self.toast_xy = (k(WIN_W) // 2, py0 + H - k(46))
        self.toast = c.create_image(*self.toast_xy, state="hidden")

        def btn(x, y, w, h, label, kind="metal", command=None, icon=None, size=None):
            return Sprite(c, k(x), y, lambda st: skin.button(k(w), k(h), label, kind, st, k(size), icon), command)

        # song panel
        py = k(PANEL[1])
        self.b_link = btn(84, py + k(24), 124, 28, "PASTE LINK", command=self.paste_link, size=11)
        self.b_file = btn(206, py + k(24), 104, 28, "OPEN FILE", command=self.pick_file, size=11)
        c.create_text(k(270), py + k(24), text="NO SONG: GUITAR ONLY", font=UI(9), fill="#e8ecef", anchor="w",
                      width=k(250), tags=("song", "song_title"))
        c.create_text(k(24), py + k(68), text="START", font=UI(8), fill="#9aa3ab", anchor="w", tags="song")
        self.pos = Slider(c, k(76), py + k(68), k(296), lambda v: c.itemconfig("pos_lbl", text=fmt(v)))
        c.create_text(k(384), py + k(68), text="0:00.0", font=LCD(22), fill=skin.ACID_HEX, anchor="w", tags=("song", "pos_lbl"))
        self.b_listen = btn(488, py + k(68), 68, 28, "5S", command=self.listen, icon="play", size=11)
        self.song_widgets = (self.b_link, self.b_file, self.pos, self.b_listen)

        # trim panel: waveform with a playhead and in/out markers, plays the video with the sound
        self.wave = tk.Canvas(root, width=W, height=WAVE_H, bg="#040904", highlightthickness=1, highlightbackground="#000")
        c.create_window(px, py + k(10), window=self.wave, anchor="nw", tags="trim")
        self.wave.bind("<Button-1>", self.wave_press)
        self.wave.bind("<B1-Motion>", self.wave_move)
        self.wave.bind("<ButtonRelease-1>", self.wave_release)
        self.wave.bind("<Motion>", lambda e: self.wave.config(cursor="sb_h_double_arrow" if self.near_mark(e.x) else "hand2"))
        ty = py + k(80)
        self.b_play = Sprite(c, k(42), ty, lambda st: skin.button(k(42), k(28), "", "acid", st,
                                                                  icon="pause" if self.playing else "play"), self.toggle_play)
        self.b_in = btn(122, ty, 98, 28, "MARK IN", command=lambda: self.set_mark("a", self.ph), icon="in", size=10)
        self.b_out = btn(228, ty, 98, 28, "MARK OUT", command=lambda: self.set_mark("b", self.ph), icon="out", size=10)
        c.create_text(k(286), ty, text="", font=LCD(18), fill=skin.ACID_HEX, anchor="w", tags=("trim", "trim_lbl"))
        self.trim_widgets = (self.b_play, self.b_in, self.b_out)
        root.bind("<space>", lambda e: self.cur_mode == "trim" and self.toggle_play())

        # big buttons
        self.rec_render = lambda st: skin.orb(k(70), "REC", "red", st)
        self.stop_render = lambda st: skin.orb(k(70), "", "red", st, stop=True)
        row = k(ROW)
        self.rec = Sprite(c, k(WIN_W) // 2, row, self.rec_render, self.toggle)
        self.tone = btn(134, row, 112, 40, "AMP", command=self.show_tone, size=15)
        self.gear = btn(402, row, 112, 40, "SETUP", command=self.reconfigure, icon="gear", size=13)
        self.keep = btn(180, row, 160, 46, "KEEP", "acid", self.process, icon="check", size=17)
        self.done = btn(180, row, 160, 46, "EXPORT", "acid", self.export, icon="check", size=17)
        self.drop = btn(356, row, 160, 46, "TRASH", "red", self.delete, icon="cross", size=17)
        self.big = (self.rec, self.tone, self.gear, self.keep, self.done, self.drop)

        self.msg = c.create_text(k(LCD_BOX[0] + 14), k(LCD_BOX[1] + LCD_BOX[3]) // 2, text="", font=LCD(16),
                                 fill=skin.ACID_HEX, anchor="w", width=k(LCD_BOX[2] - LCD_BOX[0] - 26))
        self.say("Booting REAPER + OBS...")
        self.mode("busy")
        threading.Thread(target=self.connect, daemon=True).start()

    # ---------- plumbing ----------
    def ui(self, fn):
        self.root.after(0, fn)

    def say(self, text):
        self.ui(lambda: self.c.itemconfig(self.msg, text="> " + text.upper()))

    def mode(self, m):
        """idle (song + record) / stopped (keep or bin) / trim (cut and export) / busy."""
        self.stop_play()
        self.cur_mode = m
        self.live = m != "trim"
        shown = {"idle": (self.rec, self.tone, self.gear), "stopped": (self.keep, self.drop),
                 "trim": (self.done, self.drop), "busy": ()}[m]
        for b in self.big:
            b.show(b in shown)
            if b in shown:
                b.enable(True)
        for w in self.song_widgets:
            w.show(m != "trim")
            w.enable(m == "idle")
        for w in self.trim_widgets:
            w.show(m == "trim")
        self.c.itemconfig("song", state="hidden" if m == "trim" else "normal")
        self.c.itemconfig("trim", state="normal" if m == "trim" else "hidden")

    def show_score(self):
        self.c.itemconfig(self.score, text=f"{self.settings.get('gamerscore', 0)}G")

    def achievement(self):
        """Every export earns gamerscore; milestones pop an Xbox-style toast."""
        s = self.settings
        s["exports"] = s.get("exports", 0) + 1
        name = ACHIEVEMENTS.get(s["exports"])
        points = 50 if name else 10
        s["gamerscore"] = s.get("gamerscore", 0) + points
        json.dump(s, open(SETTINGS, "w", encoding="utf-8"), indent=2)
        self.show_score()
        self.toast_img = ImageTk.PhotoImage(skin.toast("ACHIEVEMENT UNLOCKED" if name else "TAKE EXPORTED",
                                                       f"{points}G – {name or 'Nice one'}"))
        x, y = self.toast_xy
        self.c.itemconfig(self.toast, image=self.toast_img, state="normal")
        for i in range(10):  # slide up
            self.root.after(i * 25, lambda i=i: self.c.coords(self.toast, x, y + k(30) - i * k(3)))
        self.root.after(4500, lambda: self.c.itemconfig(self.toast, state="hidden"))

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
                self.say("Couldn't close the running OBS. Close it yourself (also in the tray) and start RiffCam again.")
                return
        if not running("reaper.exe"):
            subprocess.Popen([REAPER, AMP])
        ws_config()
        subprocess.Popen([OBS, "--collection", APP, "--profile", APP, "--minimize-to-tray", "--disable-shutdown-check"],
                         cwd=os.path.dirname(OBS))
        self.obs = obs_connect()
        if not self.obs:
            self.say("OBS isn't answering. Close OBS completely (also in the tray) and start RiffCam again.")
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
        self.say("Ready to shred. Paste a song link or open a file, set the start, hit REC. "
                 "(Close REAPER's license popup if it shows.)")
        self.ui(lambda: self.mode("idle"))

    def setup_dialog(self, cams, mics, done):
        s = self.settings
        d = tk.Toplevel(self.root, bg="#0b0d0e", padx=18, pady=14)
        d.title(f"{APP} setup")
        d.transient(self.root)
        d.resizable(False, False)
        d.iconbitmap(os.path.join(BUNDLE, "riffcam.ico"))
        st = ttk.Style(d)
        st.theme_use("clam")
        st.configure("Riff.TCombobox", fieldbackground="#030603", background="#2a2d31", foreground=skin.ACID_HEX,
                     arrowcolor=skin.ACID_HEX, bordercolor="#000", lightcolor="#2a2d31", darkcolor="#000")
        st.map("Riff.TCombobox", fieldbackground=[("readonly", "#030603")], foreground=[("readonly", skin.ACID_HEX)],
               selectbackground=[("readonly", "#030603")], selectforeground=[("readonly", skin.ACID_HEX)])
        d.option_add("*TCombobox*Listbox.background", "#030603")
        d.option_add("*TCombobox*Listbox.foreground", skin.ACID_HEX)
        d.option_add("*TCombobox*Listbox.selectBackground", skin.PINK_HEX)
        d.option_add("*TCombobox*Listbox.font", LCD(16))
        lbl = dict(bg="#0b0d0e", fg="#e8ecef", font=UI(10), anchor="w", justify="left")
        self.setup_logo = ImageTk.PhotoImage(skin.chrome_text("SETUP", k(30), skin.UI_FONT, skin.PINK, flare_at=0.9))
        tk.Label(d, image=self.setup_logo, bg="#0b0d0e").pack(anchor="w")

        def pick(label, options, current):
            tk.Label(d, text=label, **lbl).pack(fill="x", pady=(10, 3))
            box = ttk.Combobox(d, values=[n for n, _ in options], state="readonly", width=52, style="Riff.TCombobox", font=LCD(16))
            ids = [v for _, v in options]
            box.current(ids.index(current) if current in ids else 0)
            box.pack(fill="x")
            return lambda: ids[box.current()]
        cam = pick("CAMERA", cams, s.get("camera"))
        mic = pick("GUITAR INPUT  (the audio interface input your guitar is plugged into)", mics, s.get("audio"))
        tk.Label(d, text="VIDEO DELAY, MS  (raise it if the picture lags the sound)", **lbl).pack(fill="x", pady=(10, 3))
        lag = tk.Spinbox(d, from_=0, to=1000, increment=10, width=8, font=LCD(18), bg="#030603", fg=skin.ACID_HEX,
                         buttonbackground="#2a2d31", insertbackground=skin.ACID_HEX, relief="flat")
        lag.delete(0, "end")
        lag.insert(0, s.get("video_lag_ms", 0))
        lag.pack(anchor="w")
        tk.Label(d, wraplength=k(470), **{**lbl, "fg": "#9aa3ab", "font": UI(9)}, text=(
            "\nIN REAPER (ONCE): Options > Preferences > Audio > Device: pick your interface's ASIO driver. "
            "Then on the armed track ('Guitar') choose your guitar's input and add your amp plugin with the FX "
            "button (or AMP here). RiffCam records whatever that track sounds like.")).pack(fill="x")

        def save():
            try:
                lag_ms = int(lag.get())
            except ValueError:
                lag_ms = 0
            s.update(camera=cam(), audio=mic(), video_lag_ms=lag_ms)
            json.dump(s, open(SETTINGS, "w", encoding="utf-8"), indent=2)
            d.destroy()
            done.set()
        bc = tk.Canvas(d, width=k(180), height=k(66), bg="#0b0d0e", highlightthickness=0)
        bc.pack(pady=(12, 0))
        self.save_btn = Sprite(bc, k(90), k(33), lambda st: skin.button(k(150), k(42), "SAVE", "acid", st, k(16), "check"), save)
        self.save_btn.show(True)
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
            return
        time.sleep(1)
        self.ffplay = subprocess.Popen([FFMPEG, "-loglevel", "quiet", "-f", "dshow", "-i", "video=OBS Virtual Camera",
                                        "-vf", f"scale={W}:{H}", "-r", "25", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                        "pipe:1"], stdout=subprocess.PIPE, creationflags=NOWIN)
        size, head = W * H * 3, f"P6 {W} {H} 255\n".encode()
        while len(buf := self.ffplay.stdout.read(size)) == size:
            if self.live and not self.frame_busy:  # drop frames instead of piling them up if Tk is behind
                self.frame_busy = True
                self.ui(lambda d=head + buf: self.show_frame(d))
        self.ui(lambda: self.c.itemconfig(self.view, image=self.blank))

    def show_frame(self, data, still=False):
        if self.live or still:
            self.frame = tk.PhotoImage(data=data, format="ppm")  # keep a reference or Tk drops it
            self.c.itemconfig(self.view, image=self.frame)
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
            self.say("No link in the clipboard. Copy the video address in your browser and click again.")
            return
        self.load_song(lambda: fetch_song(url), "Downloading the song...")

    def pick_file(self):
        path = filedialog.askopenfilename(title="Song", filetypes=[("Audio/video", "*.mp3 *.wav *.m4a *.flac *.ogg *.webm *.mp4"), ("All", "*.*")])
        if path:
            self.load_song(lambda: song_from_file(path), "Loading the song...")

    def load_song(self, get, text):
        self.say(text)
        for w in self.song_widgets:
            w.enable(False)

        def go():
            try:
                title, wav = get()
                length = wav_len(wav)

                def done():
                    self.song, self.song_len = wav, length
                    self.c.itemconfig("song_title", text=f"{title[:42]}  ({fmt(length)})")
                    self.pos.to = length
                    self.pos.set(0)
                    self.say("Song loaded. Set the start (5S to preview it) and hit REC.")
                self.ui(done)
            except Exception as e:
                self.say(f"Couldn't get the song: {e}")
            self.ui(lambda: [w.enable(True) for w in self.song_widgets])
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
            self.say("REAPER is closed. Hit AMP to open it, then try again.")
            return
        self.rec.enable(False)
        if not self.started:
            for w in (self.tone, self.gear, *self.song_widgets):
                w.enable(False)
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
            self.say("REAPER didn't start recording. Close any popup in REAPER (license etc.), "
                     "check a track is armed, and try again.")
            self.ui(lambda: self.mode("idle"))
            return
        self.take = {"song": song, "start": start, "guess": time.time() - t_obs}  # rough offset if matching fails
        self.started = time.time()
        self.say("Recording. Give it everything.")

        def recording():
            self.rec.skin(self.stop_render)
            self.rec.enable(True)
            self.tick()
        self.ui(recording)

    def tick(self):
        if self.started:
            s = int(time.time() - self.started)
            blink = "●" if int((time.time() - self.started) * 2) % 2 == 0 else " "
            self.c.itemconfig(self.rec_light, text=f"{blink} REC {s // 60:02d}:{s % 60:02d}")
            self.root.after(250, self.tick)
        else:
            self.c.itemconfig(self.rec_light, text="")

    def stop_take(self):
        self.started = 0
        info = fresh(os.path.join(TMP, "take.txt"))
        reaper_lua("stop", LUA_STOP.format(out=info))
        got = wait_for(info, 15)
        self.take["raw"] = self.obs("StopRecord")["outputPath"]  # after REAPER, so the video covers the whole take
        self.ui(lambda: self.rec.skin(self.rec_render))
        if got:
            fn, pos, length, offs, proj = open(info, encoding="utf-8", errors="replace").read().split("|")
            if fn:
                self.take.update(fn=fn, pos=float(pos), len=float(length), offs=float(offs), proj=proj)
                self.say("Keep this take or trash it?")
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
                self.say("Take sent to the Recycle Bin. Run it back?")
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
            self.say("REAPER is rendering your take with the song...")
            wait_file_done(t["raw"])
            t["mix"] = os.path.join(TMP, "mix.wav")
            render(t["proj"], t["pos"], t["pos"] + t["len"], t["mix"])  # mix time 0 = take start
            self.say("Syncing video to audio...")
            obs_a = mono8k(t["raw"])
            k, strength = t["k"], t["strength"] = find_offset(obs_a, mono8k(t["fn"]))
            note = ""
            if strength < 8:  # nothing to match on (e.g. no playing at all): fall back to the timing guess
                k, note = t["guess"], " (Couldn't hear the guitar in the video, sync is approximate.)"
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
                self.say("Click the wave to move, SPACE to play. MARK IN / MARK OUT (or drag the markers), then EXPORT." + note)
            self.ui(enter)
        except Exception as e:
            self.say(f"Error: {e}")
            self.ui(lambda: self.mode("stopped"))

    # ---------- trim ----------
    def x(self, t):
        return t / self.take["L"] * W

    def draw_wave(self):
        c, mid, amp = self.wave, WAVE_H / 2, WAVE_H / 2 - 3
        c.delete("all")
        for s in range(0, int(self.take["L"]) + 1):  # a grid line per second
            c.create_line(self.x(s), 0, self.x(s), WAVE_H, fill="#0f2a0f" if s % 5 else "#1d4d1d")
        c.create_line(0, mid, W, mid, fill="#123012")
        for i, p in enumerate(self.take["peaks"]):  # absolute: full height = 0 dBFS, red = clipping
            h = min(p, 1.0) * amp
            hot = p >= 0.99
            c.create_line(i, mid - h - 1, i, mid + h + 2, fill="#5a0010" if hot else "#2c5c00")
            c.create_line(i, mid - h, i, mid + h + 1, fill=skin.HOT_HEX if hot else skin.ACID_HEX)
        self.draw_marks()

    def draw_marks(self):
        c, t = self.wave, self.take
        c.delete("m")
        for lo, hi in ((0, self.a), (self.b, t["L"])):
            c.create_rectangle(self.x(lo), 0, self.x(hi), WAVE_H, fill="#000", stipple="gray50", width=0, tags="m")
        for pos, color, label, anchor in ((self.a, skin.ACID_HEX, "IN", "nw"), (self.b, skin.PINK_HEX, "OUT", "ne")):
            c.create_line(self.x(pos), 0, self.x(pos), WAVE_H, fill=color, width=k(3), tags="m")
            c.create_text(self.x(pos) + (k(4) if anchor == "nw" else -k(4)), 1, text=label, fill=color, font=LCD(14), anchor=anchor, tags="m")
        c.create_line(0, 0, 0, WAVE_H, fill="white", width=k(2), tags=("m", "ph"))
        self.move_ph()

    def move_ph(self):  # cheap enough to run on every video frame
        self.wave.coords("ph", self.x(self.ph), 0, self.x(self.ph), WAVE_H)
        self.c.itemconfig("trim_lbl", text=f"{fmt(self.ph)}  [{fmt(self.a)}-{fmt(self.b)}]")

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
            self.ph = self.a if self.drag == "a" else self.b  # so play starts right at the marker you moved
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
        self.b_play.skin(self.b_play.render)
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
        was = self.playing
        self.playing = None
        winsound.PlaySound(None, 0)
        if self.vid:
            self.vid.kill()
        if was:
            self.b_play.skin(self.b_play.render)

    def export(self):
        self.mode("busy")
        t, a, b = self.take, self.a, self.b

        def go():
            try:
                self.say("Exporting...")
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
                self.say(f"Saved {os.path.basename(final)}. Ready for the next one.")
                self.ui(lambda: (self.mode("idle"), self.achievement()))
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
    assert os.path.exists(os.path.join(BUNDLE, "riffcam.ico"))
    assert skin.chrome_text(APP, 20).getbbox() and skin.button(80, 30, "OK").getbbox()  # fonts found, skin draws

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
        load_fonts()
        root = tk.Tk()
        App(root)
        root.mainloop()
