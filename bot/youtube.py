"""YouTube search/extraction using yt-dlp."""

import asyncio
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FTimeout
from typing import Optional
from urllib.parse import urlparse

import yt_dlp

import config
from bot import logs
from bot import proxies


_YDL_COMMON = {
    "quiet": True,
    "no_warnings": True,
    "nocheckcertificate": True,
    "geo_bypass": True,
    "socket_timeout": 20,
    "retries": 2,
}

_AUDIO_FMT = os.environ.get(
    "AUDIO_FORMAT",
    "bestaudio[ext=m4a]/bestaudio/best",
).strip()

_VIDEO_FMT = os.environ.get(
    "VIDEO_FORMAT",
    "(bestvideo[height<=?360][ext=mp4])+(bestaudio[ext=m4a])/best[height<=?360]/best",
).strip()

_POT_BASE_URL = os.environ.get(
    "POT_BASE_URL",
    "http://127.0.0.1:4416",
).strip()


def _pot_available() -> bool:
    return os.environ.get(
        "DISABLE_POT",
        "",
    ).strip().lower() not in ("1", "true", "yes")


# کلاینت‌هایی که ابتدا امتحان می‌شوند.
# tv و android_vr فعلاً برای GVS به PO Token نیاز ندارند.
_CLIENTS_DIRECT = [
    ["tv"],
    ["android_vr"],
    ["web_safari"],
    ["mweb"],
    None,
    ["ios"],
]

_CLIENTS_PROXY = [
    ["tv"],
    ["android_vr"],
    ["web_safari"],
    ["mweb"],
    None,
]


_BLOCK_SIGNS = (
    "sign in to confirm",
    "not a bot",
    "http error 403",
    "unable to download",
    "failed to extract",
    "login_required",
)


def has_cookies() -> bool:
    return bool(
        config.COOKIES_FILE
        and os.path.isfile(config.COOKIES_FILE)
    )


def _cookie_opts() -> dict:
    if has_cookies():
        return {
            "cookiefile": config.COOKIES_FILE
        }
    return {}


def _format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "زنده / نامشخص"

    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)

    if h:
        return f"{h}:{m:02d}:{s:02d}"

    return f"{m}:{s:02d}"


def _pack(info: dict) -> dict:
    return {
        "id": info.get("id"),
        "title": info.get("title", "نامشخص"),
        "duration": info.get("duration"),
        "duration_text": _format_duration(
            info.get("duration")
        ),
        "stream_url": info.get("url"),
        "webpage_url": info.get(
            "webpage_url",
            "",
        ),
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get(
            "uploader",
            "",
        ),
    }


def _is_block_error(msg: str) -> bool:
    low = msg.lower()
    return any(
        sign in low
        for sign in _BLOCK_SIGNS
    )


def _run(
    search: str,
    fmt: str,
    client: Optional[list],
    proxy: Optional[str],
    download: bool,
    out_dir: str,
) -> dict:

    opts = {
        **_YDL_COMMON,
        **_cookie_opts(),
        "format": fmt,
    }

    js_rt = os.environ.get(
        "JS_RUNTIME",
        "node",
    ).strip()

    if js_rt:
        opts["js_runtimes"] = {
            js_rt: {}
        }

    extractor_args = {}

    if client:
        extractor_args["youtube"] = {
            "player_client": client
        }

    # برای tv و android_vr نیازی به PO Token نیست.
    # برای سایر کلاینت‌ها bgutil فعال می‌ماند.
    if _pot_available() and client not in (
        ["tv"],
        ["android_vr"],
    ):
        extractor_args[
            "youtubepot-bgutilhttp"
        ] = {
            "base_url": [_POT_BASE_URL]
        }

    if extractor_args:
        opts["extractor_args"] = extractor_args

    if proxy:
        opts["proxy"] = proxy
        opts["socket_timeout"] = int(
            os.environ.get(
                "PROXY_TIMEOUT",
                "8",
            )
        )
        opts["retries"] = 0

    if download:
        opts["outtmpl"] = os.path.join(
            out_dir,
            "%(id)s.%(ext)s",
        )

        is_video_dl = fmt == _VIDEO_FMT

        if not is_video_dl:
            mp3q = os.environ.get(
                "MP3_QUALITY",
                "96",
            ).strip()

            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": mp3q,
                }
            ]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(
            search,
            download=download,
        )

        if "entries" in info:
            entries = info.get("entries") or []

            if not entries:
                raise ValueError(
                    "چیزی پیدا نشد"
                )

            info = entries[0]

    return info


def _proxy_alive(
    proxy: str,
    timeout: float = 3.0,
) -> bool:

    try:
        u = urlparse(proxy)
        host = u.hostname
        port = u.port or 8080

        if not host:
            return False

        with socket.create_connection(
            (host, port),
            timeout=timeout,
        ):
            return True

    except Exception:
        return False


def _try_clients(
    search: str,
    fmt: str,
    clients: list,
    proxy: Optional[str],
    download: bool,
    out_dir: str,
):

    if download:
        hard = float(
            os.environ.get(
                "DL_HARD_TIMEOUT",
                "90",
            )
        )
    else:
        hard = float(
            os.environ.get(
                "ATTEMPT_HARD_TIMEOUT",
                "20" if proxy else "45",
            )
        )

    last_err = None

    for client in clients:

        label = (
            ",".join(client)
            if client
            else "auto"
        )

        pxy = (
            proxy.split("@")[-1]
            if proxy
            else "direct"
        )

        t0 = time.monotonic()

        logs.stage_start(
            "YT_TRY",
            client=label,
            proxy=pxy,
        )

        try:

            with ThreadPoolExecutor(
                max_workers=1
            ) as ex:

                fut = ex.submit(
                    _run,
                    search,
                    fmt,
                    client,
                    proxy,
                    download,
                    out_dir,
                )

                try:
                    info = fut.result(
                        timeout=hard
                    )

                except FTimeout:

                    logs.stage_fail(
                        "YT_TRY",
                        err=(
                            f"مهلت {hard:.0f}s تمام شد"
                        ),
                        took=(
                            time.monotonic()
                            - t0
                        ),
                        client=label,
                        proxy=pxy,
                    )

                    last_err = TimeoutError(
                        f"hard timeout {hard}s"
                    )

                    continue

            logs.stage_ok(
                "YT_TRY",
                took=(
                    time.monotonic()
                    - t0
                ),
                client=label,
                proxy=pxy,
                title=info.get(
                    "title",
                    "?",
                ),
            )

            return info, None

        except Exception as e:

            last_err = e
            msg = str(e)

            logs.stage_fail(
                "YT_TRY",
                err=(
                    f"{type(e).__name__}: "
                    f"{msg[:120]}"
                ),
                took=(
                    time.monotonic()
                    - t0
                ),
                client=label,
                proxy=pxy,
            )

            low = msg.lower()

            if any(
                k in low
                for k in (
                    "video unavailable",
                    "private video",
                    "removed",
                )
            ):
                return None, e

    return None, last_err


def _get_proxy_list() -> list:

    if not proxies.enabled():
        return []

    try:
        result = proxies.candidates(
            limit=int(
                os.environ.get(
                    "PROXY_MAX_TRY",
                    "40",
                )
            )
        )

        return result or []

    except Exception as e:

        logs.info(
            "YT: دریافت پروکسی ناموفق: %s",
            e,
        )

        return []


def _extract_with_fallback(
    query: str,
    video: bool,
    download: bool = False,
    out_dir: str = "",
) -> dict:

    is_url = query.startswith(
        ("http://", "https://")
    )

    search = (
        query
        if is_url
        else f"ytsearch1:{query}"
    )

    fmt = (
        _VIDEO_FMT
        if video
        else _AUDIO_FMT
    )

    proxy_list = _get_proxy_list()
    use_proxy = bool(proxy_list)

    logs.info(
        "YT: کوکی=%s | PO=%s | پروکسی=%s | تعداد پروکسی=%d",
        has_cookies(),
        _pot_available(),
        use_proxy,
        len(proxy_list),
    )

    proxy_first = (
        os.environ.get(
            "PROXY_FIRST",
            "0",
        )
        .strip()
        .lower()
        in ("1", "true", "yes")
    )

    if use_proxy and proxy_first:

        info = _via_proxies(
            search,
            fmt,
            download,
            out_dir,
            prior_err=None,
            proxy_list=proxy_list,
        )

        if info is not None:
            return info

    # اول کلاینت‌های بدون نیاز به PO
    # سپس کلاینت‌های دارای PO.
    info, err = _try_clients(
        search,
        fmt,
        _CLIENTS_DIRECT,
        None,
        download,
        out_dir,
    )

    if info is not None:
        return info

    if err and not _is_block_error(
        str(err)
    ):
        logs.stage_fail(
            "YT_EXTRACT",
            err=(
                "خطای غیربلاکی: "
                f"{str(err)[:120]}"
            ),
        )
        raise err

    if use_proxy:

        info = _via_proxies(
            search,
            fmt,
            download,
            out_dir,
            prior_err=err,
            proxy_list=proxy_list,
        )

        if info is not None:
            return info

    if err:
        raise err

    raise RuntimeError(
        "استخراج یوتیوب ناموفق بود"
    )


def _via_proxies(
    search: str,
    fmt: str,
    download: bool,
    out_dir: str,
    prior_err,
    proxy_list: Optional[list] = None,
):

    if proxy_list is None:
        proxy_list = _get_proxy_list()

    logs.info(
        "YT: تلاش با پروکسی — %d کاندید",
        len(proxy_list),
    )

    if not proxy_list:
        return None

    tried = 0

    for i, proxy in enumerate(
        proxy_list,
        1,
    ):

        if not _proxy_alive(proxy):

            try:
                proxies.mark_bad(proxy)
            except Exception:
                pass

            continue

        tried += 1

        info, e = _try_clients(
            search,
            fmt,
            _CLIENTS_PROXY,
            proxy,
            download,
            out_dir,
        )

        if info is not None:

            try:
                proxies.mark_good(proxy)
            except Exception:
                pass

            logs.stage_ok(
                "YT_EXTRACT",
                note=(
                    f"موفق با پروکسی #{i}"
                ),
            )

            return info

        try:
            proxies.mark_bad(proxy)
        except Exception:
            pass

    logs.stage_fail(
        "YT_EXTRACT",
        err=(
            f"همه پروکسی‌ها ناموفق "
            f"({tried} پروکسی زنده)"
        ),
    )

    return None


def _extract(
    query: str,
    video: bool,
) -> dict:

    return _pack(
        _extract_with_fallback(
            query,
            video,
            download=False,
        )
    )


def _download(
    query: str,
    out_dir: str,
    video: bool = False,
) -> dict:

    info = _extract_with_fallback(
        query,
        video=video,
        download=True,
        out_dir=out_dir,
    )

    vid = info["id"]

    ext = (
        "mp4"
        if video
        else "mp3"
    )

    path = os.path.join(
        out_dir,
        f"{vid}.{ext}",
    )

    if not os.path.isfile(path):

        import glob

        matches = glob.glob(
            os.path.join(
                out_dir,
                f"{vid}.*",
            )
        )

        matches = [
            m
            for m in matches
            if not m.endswith(
                (".part", ".ytdl")
            )
        ]

        if matches:
            path = matches[0]

    return {
        "id": vid,
        "path": path,
        "title": info.get(
            "title",
            "audio",
        ),
        "duration": info.get(
            "duration"
        ),
        "duration_text": _format_duration(
            info.get("duration")
        ),
        "thumbnail": info.get(
            "thumbnail"
        ),
        "uploader": info.get(
            "uploader",
            "",
        ),
        "webpage_url": info.get(
            "webpage_url",
            "",
        ),
    }


async def get_media(
    query: str,
    video: bool = False,
) -> dict:

    loop = asyncio.get_event_loop()

    return await loop.run_in_executor(
        None,
        _extract,
        query,
        video,
    )


def _search_title_sync(
    query: str,
) -> dict:

    info = _extract_with_fallback(
        query,
        video=False,
        download=False,
    )

    return {
        "id": info.get(
            "id",
            "",
        ),
        "title": info.get(
            "title",
            "",
        ),
        "uploader": info.get(
            "uploader",
            "",
        ),
        "duration": info.get(
            "duration"
        ) or 0,
    }


async def search_title(
    query: str,
) -> dict:

    loop = asyncio.get_event_loop()

    try:
        return await loop.run_in_executor(
            None,
            _search_title_sync,
            query,
        )

    except Exception as e:

        logs.debug(
            "yt search_title: %s",
            e,
        )

        return {}


async def download_audio(
    query: str,
    out_dir: str = "downloads",
) -> dict:

    os.makedirs(
        out_dir,
        exist_ok=True,
    )

    loop = asyncio.get_event_loop()

    return await loop.run_in_executor(
        None,
        _download,
        query,
        out_dir,
        False,
    )


async def download_media(
    query: str,
    video: bool = False,
    out_dir: str = "downloads",
) -> dict:

    os.makedirs(
        out_dir,
        exist_ok=True,
    )

    loop = asyncio.get_event_loop()

    return await loop.run_in_executor(
        None,
        _download,
        query,
        out_dir,
        video,
    )
