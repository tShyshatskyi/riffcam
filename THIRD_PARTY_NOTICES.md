# Third-party software in the RiffCam release

RiffCam's own code is MIT-licensed (see `LICENSE`). The Windows release zip also bundles the
programs and libraries below, each under its own license.

| Component | License | Source |
|---|---|---|
| FFmpeg (the `ffmpeg-win-x86_64` binary shipped by imageio-ffmpeg) | GPL-3.0 (this build includes GPL parts such as x264) | https://ffmpeg.org/download.html, build scripts: https://github.com/imageio/imageio-binaries |
| Deno (JavaScript runtime used by yt-dlp for YouTube) | MIT | https://github.com/denoland/deno |
| yt-dlp | Unlicense | https://github.com/yt-dlp/yt-dlp |
| yt-dlp-ejs | Unlicense / MIT / ISC | https://github.com/yt-dlp/ejs |
| imageio-ffmpeg | BSD-2-Clause | https://github.com/imageio/imageio-ffmpeg |
| NumPy | BSD-3-Clause and others | https://github.com/numpy/numpy |
| websocket-client | Apache-2.0 | https://github.com/websocket-client/websocket-client |
| Python and Tcl/Tk (via PyInstaller) | PSF-2.0, Tcl/Tk license | https://www.python.org, https://www.tcl.tk |

FFmpeg and Deno are separate programs that RiffCam starts as child processes; they are not linked
into RiffCam's code. The FFmpeg source code for the bundled build is available from the links above.

RiffCam talks to REAPER (https://www.reaper.fm) and OBS Studio (https://obsproject.com), which you
install yourself and which keep their own licenses.
