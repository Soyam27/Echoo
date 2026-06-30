"""
Async Instagram comment scraper using the internal /api/graphql endpoint.
Requires session cookies from the browser (set IG_COOKIES in .env).
"""
import asyncio
import json
import re
import random
import string
import time
from urllib.parse import unquote

import httpx

GRAPHQL_URL = "https://www.instagram.com/api/graphql"
DOC_ID_LOGGEDOUT = "27261273046856309"
DOC_ID_LOGGEDIN = "26864966453197043"
APP_ID = "936619743392459"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 15; Pixel 9) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Mobile Safari/537.36"
)

# Build rev=1042371453 (logged-out)
_DYN = (
    "7xeUjG1mxu1syUbFp41twpUnwgU7SbzEdF8aUco2qwJw5ux609vCwjE1EE2Cw8G11wBz81s8hwGxu786a3a1Yw"
    "Bgao6C1uwoE2swlo8od8-U2zxe2GewGw9a361qw8Xxm16wa-0oa2-azo7u3C2u2J0bS1LyUaUbGwmk0zU8oC1Iw"
    "qo5p389oed6goK10xKi2K7E5y4U7a0EoKmUhw5nyEcE4y1Hwj83KwRzk"
)
_CSR = (
    "g4vkrEAh2Y9iYJmx90F9ljdsyZgLkJBGqylKpatRVbSJllGhrAqnqlAL-CFaHFiaiLYCvmCin8iGQKh2aEkmteeBi"
    "HJZbVdeG8GjVesEyiaFLiBG-DGp7F2qCLml1d7VpoGhajpFbAyFXGAUgCl2zGWWUDDzoOuE-XDz4iqcx9k8DXx28Cxd7"
    "DQjgy48KfnZ4iy8iqzqUCECt154m9x13qzHy4mqmWVVmtCGH8UN4yEnwAKaUyagnwIwpU7l7wXy4qgxFoC4ppo020fw0"
    "JYw0bTe680wO3u1ww2IE0iaxi2J2E8ApN9E0P2092O04zDhE0NOi0Yo3Iw6EN5wcS2O1o80rCtk04rk13li552E3dw9"
    "WkK8w6Uo0YC6U05GG04Ao0OW0ebo"
)

# Build rev=1042381694 (logged-in)
_DYN_LI = (
    "7xeUjG1mxu1syUbFp41twpUnwgU7SbzEdF8aUco2qwJxS0k24o0B-q1ew6ywaq0yE462mcw5Mx62G5UswoEcE7O2l0"
    "Fwqo5W1yw9O1lwlE-U2zxe2GewGw9a361qw8Xxm16wa-0oa2-azo7u3C2u2J0bS1LyUaUbGwmk0zU8oC1Iwqo5p389"
    "oed6goK10xKi2qi7E5y4U7a0EoKmUhw4UxWawOwi84q2i1cweW3mdg"
)
_CSR_LI = (
    "grE2f4ML48G224hikDiOZEZSBJF4hAWkCVJ4p49iiAeBqtkl5kyYGSiiHqRFdXGVsHK44_UGaAi-Su8Ez8JFF7rmit5"
    "jrgCiDF4vXiF2aF2ZkqADAQBAZel5OeHunVVGVWGJea-Fd2FpWpHAVoDjJkVV8ZvLiBnDzy29VZ7nCxCUyqUCFeuVp"
    "WVA79e8JDBG4aFappaXWVUF2kfDxiiqFCCudG58-Al39ogV-HKfFBiV4p2Vu4FSiBUiBG22K5GXBCUkAxC1iy859OwB"
    "wSF0RAwEw080W02TC00Lsu08ywVwnU0Iu04zoaQrwAif4w3fEG3jw7MDl04F86o0MS0YE3Gz83hwm4kw5a5x8Wu3K1VK"
    "2G1hDm0s0E0Iq0qx0ZBQy2169g2UAwb4EFwgU1sm0eOxG01r1w19a08ow4dw3AS"
)

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _shortcode_to_media_id(shortcode: str) -> str:
    n = 0
    for ch in shortcode:
        n = n * 64 + _ALPHABET.index(ch)
    return str(n)


def _extract_shortcode(url: str) -> str | None:
    url = url.split("?")[0].split("#")[0].rstrip("/")
    m = re.search(r"/(p|reels?|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(2) if m else None


def _rand6() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _parse_cookies(cookie_str: str) -> dict:
    cookies: dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = unquote(v.strip())
    return cookies


async def fetch_comments(url: str, cookie_str: str = "", delay: float = 1.5) -> list[dict]:
    """
    Fetch all top-level comments from a public Instagram post/reel.
    Requires a logged-in session (IG_COOKIES in .env must contain sessionid).
    Raises ValueError if no session cookie is configured.
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        raise ValueError(f"Cannot extract shortcode from URL: {url}")
    media_id = _shortcode_to_media_id(shortcode)
    cookies = _parse_cookies(cookie_str) if cookie_str else {}

    if not cookies.get("sessionid"):
        raise ValueError(
            "IG_COOKIES is not configured or missing sessionid. "
            "Set IG_COOKIES in your .env with a logged-in Instagram session."
        )

    return await _fetch_logged_in(url, media_id, cookies, delay)


async def _fetch_logged_out(post_url: str, media_id: str, delay: float) -> list[dict]:
    async with httpx.AsyncClient(http2=True, follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(post_url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        html = resp.text

        lsd_m = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
        if not lsd_m:
            raise RuntimeError("Could not extract LSD token from Instagram page")
        lsd = lsd_m.group(1)
        csrf = resp.cookies.get("csrftoken") or client.cookies.get("csrftoken") or ""
        rev = (re.search(r'"client_revision"\s*:\s*(\d+)', html) or _noop()).group(1)
        hs = (re.search(r'"haste_session"\s*:\s*"([^"]+)"', html) or _noop()).group(1)
        hsi = (re.search(r'"hsi"\s*:\s*"([^"]+)"', html) or _noop()).group(1)
        jazoest = "2" + str(sum(ord(c) for c in csrf))

        post_headers = _base_headers(csrf, lsd, "PolarisLoggedOutDesktopWWWPostCommentsPaginationQuery", post_url)
        base_body = {
            "av": "0", "__d": "www", "__user": "0", "__a": "1",
            "__hs": hs, "dpr": "1", "__ccg": "EXCELLENT", "__rev": rev,
            "__hsi": hsi, "__dyn": _DYN, "__csr": _CSR,
            "__comet_req": "7", "lsd": lsd, "jazoest": jazoest,
            "__spin_r": rev, "__spin_b": "trunk",
            "__crn": "comet.igweb.PolarisLoggedOutDesktopWWWPostRoute",
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": "PolarisLoggedOutDesktopWWWPostCommentsPaginationQuery",
            "server_timestamps": "true",
            "doc_id": DOC_ID_LOGGEDOUT,
            "__s": f"{_rand6()}:{_rand6()}:{_rand6()}",
        }

        return await _paginate(client, base_body, post_headers, media_id, delay,
                               variables_extra={}, is_logged_in=False)


async def _fetch_logged_in(post_url: str, media_id: str, cookies: dict, delay: float) -> list[dict]:
    async with httpx.AsyncClient(http2=True, follow_redirects=True, timeout=30.0) as client:
        for k, v in cookies.items():
            client.cookies.set(k, v, domain="www.instagram.com")

        print(f"[ig_scraper] LOGGED-IN mode  doc_id={DOC_ID_LOGGEDIN}")

        resp = await client.get(post_url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        print(f"[ig_scraper] page GET status={resp.status_code}")
        html = resp.text

        lsd_m = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
        lsd = lsd_m.group(1) if lsd_m else ""

        dtsg_m = re.search(r'"DTSGInitData",\[\],\{"token":"([^"]+)"', html)
        fb_dtsg = dtsg_m.group(1) if dtsg_m else ""
        if not fb_dtsg:
            dtsg_m = re.search(r'"fb_dtsg"\s*:\s*\{\s*"token"\s*:\s*"([^"]+)"', html)
            fb_dtsg = dtsg_m.group(1) if dtsg_m else ""

        # Extract av (actor/viewer ID) from page HTML — it differs from ds_user_id
        # Instagram embeds it in several places; try each pattern in order
        av = ""
        for pat in [
            r'"actorId"\s*:\s*"(\d+)"',
            r'"viewerId"\s*:\s*"(\d+)"',
            r'"av"\s*:\s*"(\d+)"',
            r'"__av"\s*:\s*"(\d+)"',
        ]:
            m = re.search(pat, html)
            if m:
                av = m.group(1)
                break
        if not av:
            # Final fallback: ds_user_id from cookie
            av = cookies.get("ds_user_id", "0")
        print(f"[ig_scraper] av={av}")

        csrf = cookies.get("csrftoken", "")
        rev = (re.search(r'"client_revision"\s*:\s*(\d+)', html) or _noop()).group(1) or "1042381694"
        hs = (re.search(r'"haste_session"\s*:\s*"([^"]+)"', html) or _noop()).group(1)
        hsi = (re.search(r'"hsi"\s*:\s*"([^"]+)"', html) or _noop()).group(1)
        jazoest = "2" + str(sum(ord(c) for c in csrf))

        print(f"[ig_scraper] lsd={'ok' if lsd else 'MISSING'}  fb_dtsg={'ok' if fb_dtsg else 'MISSING'}  rev={rev}")
        if not lsd:
            print("[ig_scraper] WARNING: lsd token missing — GraphQL request will likely be rejected")
        if not fb_dtsg:
            print("[ig_scraper] WARNING: fb_dtsg missing — logged-in auth will fail")

        post_headers = _base_headers(csrf, lsd, "PolarisPostCommentsPaginationQuery", post_url)
        base_body = {
            # av = viewer/actor ID extracted from page HTML (NOT ds_user_id)
            # __user stays "0" — that's what the browser sends too
            "av": av, "__d": "www", "__user": "0", "__a": "1",
            "__hs": hs, "dpr": "1", "__ccg": "EXCELLENT", "__rev": rev,
            "__hsi": hsi, "__dyn": _DYN_LI, "__csr": _CSR_LI,
            "__comet_req": "7", "fb_dtsg": fb_dtsg, "lsd": lsd, "jazoest": jazoest,
            "__spin_r": rev, "__spin_b": "trunk",
            "__crn": "comet.igweb.PolarisDesktopPostRoute",
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": "PolarisPostCommentsPaginationQuery",
            "server_timestamps": "true",
            "doc_id": DOC_ID_LOGGEDIN,
            "__s": f"{_rand6()}:{_rand6()}:{_rand6()}",
        }

        return await _paginate(client, base_body, post_headers, media_id, delay,
                               variables_extra={
                                   "sort_order": "popular",
                                   "__relay_internal__pv__PolarisIsLoggedInrelayprovider": True,
                               },
                               is_logged_in=True)


async def _paginate(
    client: httpx.AsyncClient,
    base_body: dict,
    post_headers: dict,
    media_id: str,
    delay: float,
    variables_extra: dict,
    is_logged_in: bool,
) -> list[dict]:
    comments: list[dict] = []
    cursor = None
    page = 0
    req_num = 1

    while True:
        page += 1
        req_num += 1
        base_body["__req"] = str(req_num)
        base_body["__spin_t"] = str(int(time.time()))

        variables: dict = {"after": cursor, "first": 50, "media_id": media_id}
        if is_logged_in:
            variables.update({"before": None, "last": None})
        variables.update(variables_extra)

        body = {**base_body, "variables": json.dumps(variables)}

        data = None
        for attempt in range(3):
            try:
                resp = await client.post(GRAPHQL_URL, data=body, headers=post_headers)
                print(f"[ig_scraper] page {page} GraphQL status={resp.status_code} is_logged_in={is_logged_in}")
                raw = resp.text.lstrip("for (;;);").strip()
            except httpx.ReadTimeout:
                raw = ""
            if raw and not raw.startswith("<!"):
                try:
                    data = json.loads(raw)
                    break
                except json.JSONDecodeError:
                    print(f"[ig_scraper] page {page} JSON decode error — first 200 chars: {raw[:200]}")
            else:
                if raw.startswith("<!"):
                    print(f"[ig_scraper] page {page} got HTML response (likely checkpoint/login redirect)")
            wait = 5 * (attempt + 1) + random.uniform(0, 2)
            print(f"[ig_scraper] page {page} rejected (attempt {attempt+1}/3) — waiting {wait:.1f}s")
            await asyncio.sleep(wait)

        if data is None:
            print(f"[ig_scraper] page {page} all retries failed — stopping")
            break

        if data.get("error"):
            print(f"[ig_scraper] page {page} API error {data['error']}: {data.get('errorSummary')}")
            break

        d = data.get("data", {})
        comment_block = (
            d.get("xdt_api__v1__media__media_id__comments__connection") or
            d.get("xig_polaris_media", {}).get("comments_connection") or
            d.get("xdt_shortcode_media", {}).get("edge_media_to_parent_comment") or
            d.get("xdt_shortcode_media", {}).get("edge_media_to_comment")
        )

        if not comment_block:
            print(f"[ig_scraper] page {page} unexpected shape — keys: {list(d.keys())}")
            break

        edges = comment_block.get("edges", [])
        for edge in edges:
            node = edge.get("node", edge)
            text = node.get("text", "")
            if not text:
                continue
            comments.append({
                "id": node.get("id") or node.get("pk"),
                "username": (node.get("user") or node.get("owner") or {}).get("username", "unknown"),
                "text": text,
                "timestamp": node.get("created_at"),
                "likes": node.get("comment_like_count") or node.get("like_count") or 0,
            })

        page_info = comment_block.get("page_info", {})
        has_next = page_info.get("has_next_page", False)
        cursor = page_info.get("end_cursor")

        print(f"[ig_scraper] page {page} +{len(edges)} comments (total {len(comments)}) has_next={has_next}")

        if not has_next or not cursor:
            break

        await asyncio.sleep(delay + random.uniform(0.5, 1.5))

    return comments


def _base_headers(csrf: str, lsd: str, friendly_name: str, referer: str) -> dict:
    return {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        # Do NOT set Accept-Encoding here — let httpx advertise only what it can decompress.
        # Advertising zstd causes Instagram to send zstd-compressed responses that httpx cannot decode.
        # sec-ch-ua headers must match the User-Agent (Pixel 9 / Chrome 149 mobile)
        "sec-ch-ua": '"Chromium";v="149", "Not)A;Brand";v="24"',
        "sec-ch-ua-full-version-list": '"Chromium";v="149.0.0.0", "Not)A;Brand";v="24.0.0.0"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-model": '"Pixel 9"',
        "sec-ch-ua-platform": '"Android"',
        "sec-ch-ua-platform-version": '"15"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "x-asbd-id": "359341",
        "x-csrftoken": csrf,
        "x-fb-friendly-name": friendly_name,
        "x-fb-lsd": lsd,
        "x-ig-app-id": APP_ID,
        "x-ig-max-touch-points": "5",
        "Origin": "https://www.instagram.com",
        "Referer": referer,
        "Priority": "u=1, i",
    }


class _noop:
    """Fallback for failed regex matches so .group(1) returns empty string."""
    def group(self, _: int) -> str:
        return ""
