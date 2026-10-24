# -*- coding: utf-8 -*-
import sys
import os
os.environ['PYTHONUNBUFFERED'] = '1'
os.environ.setdefault('PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION', 'python')
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import asyncio
import httpx
import random
import json
import socket
import struct
import time
import uuid
import itertools
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from google_play_scraper import app as play_scraper
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from protobuf_decoder.protobuf_decoder import Parser
from message_ids import MESSAGE_ID_TO_NAME
import thunderFF_pb2

# ---------------------------------------------------------------------------
# Windows compatibility
# ---------------------------------------------------------------------------
# The default asyncio event loop on Windows is ProactorEventLoop, which does
# NOT implement loop.sock_sendto()/loop.sock_recvfrom(). Those calls are needed
# for the UDP match session, so on Windows we force a SelectorEventLoop.
# (Without this, sending the match HELLO raises NotImplementedError -> the
#  "[SEND-FAIL] HELLO" with an empty error message.)
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Loop-agnostic UDP helpers
# ---------------------------------------------------------------------------
# Even if a ProactorEventLoop is in use (e.g. uvicorn forces it), these helpers
# transparently fall back to blocking socket I/O run in a worker thread, so the
# match session works on every platform / event loop.
def _udp_sendto_blocking(sock, data, addr):
    import select
    try:
        return sock.sendto(data, addr)
    except BlockingIOError:
        _, w, _ = select.select([], [sock], [], 0.5)
        if w:
            try:
                return sock.sendto(data, addr)
            except BlockingIOError:
                return 0
        return 0


def _udp_recvfrom_blocking(sock, bufsize, timeout):
    import select
    r, _, _ = select.select([sock], [], [], timeout)
    if not r:
        raise asyncio.TimeoutError()
    try:
        return sock.recvfrom(bufsize)
    except BlockingIOError:
        raise asyncio.TimeoutError()


async def udp_sendto(sock, data, addr):
    """Send a UDP datagram, working on both Selector and Proactor loops."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.sock_sendto(sock, data, addr)
    except NotImplementedError:
        return await loop.run_in_executor(None, _udp_sendto_blocking, sock, data, addr)


async def udp_recvfrom(sock, bufsize, timeout):
    """Receive a UDP datagram with timeout, working on any event loop."""
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(loop.sock_recvfrom(sock, bufsize), timeout)
    except NotImplementedError:
        return await loop.run_in_executor(
            None, _udp_recvfrom_blocking, sock, bufsize, timeout
        )


START_MATCH_INTERVAL = 1
MAX_MATCH_DURATION = 1500
MATCH_END_IDLE_TIMEOUT = 60
MATCH_IDLE_TIMEOUT = 2
NON_MATCH_RECONNECT_DELAY = 0.5

MODE_ID = 1
MAP_ID = 1

ACCOUNT_REGION = "ME"

xK, xV = b'Yg&tc%DEuh6%Zc^8', b'6oyZDr22E3ychjM%'
AES_KEY = xK
AES_IV = xV

REGION_SERVERS = {
    "IND": "https://clientbp.ppmainecoonghj.com/",
    "ID": "https://clientbp.ppmainecoonghj.com/",
    "BR": "https://clientbp.ppmainecoonghj.com/",
    "ME": "https://clientbp.ppmainecoonghj.com/",
    "VN": "https://clientbp.ppmainecoonghj.com/",
    "TH": "https://clientbp.ppmainecoonghj.com/",
    "CIS": "https://clientbp.ppmainecoonghj.com/",
    "BD": "https://clientbp.ppmainecoonghj.com/",
    "PK": "https://clientbp.ppmainecoonghj.com/",
    "SG": "https://clientbp.ppmainecoonghj.com/",
    "NA": "https://clientbp.ppmainecoonghj.com/",
    "SAC": "https://clientbp.ppmainecoonghj.com/",
    "EUROPE": "https://clientbp.ppmainecoonghj.com/",
    "TW": "https://clientbp.ppmainecoonghj.com/",
}

REGION_LANG = {
    "BD": "bn", "IND": "hi", "PK": "ur", "SG": "en", "ID": "id",
    "ME": "ar", "VN": "vi", "TW": "zh", "CIS": "ru", "TH": "th",
    "EU": "en", "US": "en", "SAC": "es", "LK": "en", "BR": "pt",
}

REGION_PREFIX = {
    "ME": "031900",
    "IND": "031400",
    "BD": "031400",
    "SG": "031500",
    "TH": "031500",
    "PH": "031500",
    "VN": "031500",
    "MY": "031500",
    "ID": "031500",
    "HK": "031500",
    "TW": "031500",
    "PK": "031500",
    "CIS": "031500",
    "BR": "031500",
    "NA": "031500",
    "SAC": "031500",
    "EUROPE": "031500",
}

CRC7_TABLE = bytes([
    0, 9, 18, 27, 36, 45, 54, 63, 72, 65, 90, 83, 108, 101, 126, 119,
    25, 16, 11, 2, 61, 52, 47, 38, 81, 88, 67, 74, 117, 124, 103, 110,
    50, 59, 32, 41, 22, 31, 4, 13, 122, 115, 104, 97, 94, 87, 76, 69,
    43, 34, 57, 48, 15, 6, 29, 20, 99, 106, 113, 120, 71, 78, 85, 92,
    100, 109, 118, 127, 64, 73, 82, 91, 44, 37, 62, 55, 8, 1, 26, 19,
    125, 116, 111, 102, 89, 80, 75, 66, 53, 60, 39, 46, 17, 24, 3, 10,
    86, 95, 68, 77, 114, 123, 96, 105, 30, 23, 12, 5, 58, 51, 40, 33,
    79, 70, 93, 84, 107, 98, 121, 112, 7, 14, 21, 28, 35, 42, 49, 56,
    65, 72, 83, 90, 101, 108, 119, 126, 9, 0, 27, 18, 45, 36, 63, 54,
    88, 81, 74, 67, 124, 117, 110, 103, 16, 25, 2, 11, 52, 61, 38, 47,
    115, 122, 97, 104, 87, 94, 69, 76, 59, 50, 41, 32, 31, 22, 13, 4,
    106, 99, 120, 113, 78, 71, 92, 85, 34, 43, 48, 57, 6, 15, 20, 29,
    37, 44, 55, 62, 1, 8, 19, 26, 109, 100, 127, 118, 73, 64, 91, 82,
    60, 53, 46, 39, 24, 17, 10, 3, 116, 125, 102, 111, 80, 89, 66, 75,
    23, 30, 5, 12, 51, 58, 33, 40, 95, 86, 77, 68, 123, 114, 105, 96,
    14, 7, 28, 21, 42, 35, 56, 49, 70, 79, 84, 93, 98, 107, 112, 121,
])

_DELTA = 0x9E3779B9
_ROUNDS = 16
_FIELD_SIZES = {0: 1, 1: 2, 2: 2, 3: 1, 4: 2}
_FIELD_NAMES = {0: "sendOption", 1: "cmd", 2: "orderId", 3: "flags", 4: "length"}

CLOUDFLARE_PRIMARY_DNS = "1.1.1.1"
CLOUDFLARE_SECONDARY_DNS = "1.0.0.1"
_DNS_CACHE: Dict[str, Tuple[str, float]] = {}
_DNS_CACHE_TTL = 300.0

client = httpx.AsyncClient(
    timeout=15.0,
    limits=httpx.Limits(max_connections=300, max_keepalive_connections=150)
)
_LOGIN_SEMAPHORE = asyncio.Semaphore(4)

headers = {
    'User-Agent': 'UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)',
    'Connection': 'Keep-Alive',
    'Accept-Encoding': 'gzip',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Expect': '100-continue',
    'X-Unity-Version': '2018.4.12f1',
    'X-GA-SV': '1789535859',
    'X-GA': 'v1 1',
    'ReleaseVersion': 'OB55'
}


def log(msg): print(msg, flush=True)
def print_error(msg): log(f"[-] {msg}")
def print_success(msg): log(f"[+] {msg}")
def print_info(msg): log(f"[i] {msg}")
def print_warning(msg): log(f"[!] {msg}")


def get_proto_field(d, key, default=None):
    if not d or not isinstance(d, dict):
        return default
    if key in d:
        val = d[key].get('data')
        return val if val is not None else default
    if str(key) in d:
        val = d[str(key)].get('data')
        return val if val is not None else default
    return default


def _pb_varint(n):
    if n < 0:
        n = (1 << 64) + n
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            break
    return bytes(out)


def _pb_tag(f, w):
    return _pb_varint((f << 3) | w)


def _pb_field(f, v):
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, int):
        return _pb_tag(f, 0) + _pb_varint(v)
    if isinstance(v, str):
        d = v.encode('utf-8')
        return _pb_tag(f, 2) + _pb_varint(len(d)) + d
    if isinstance(v, (bytes, bytearray)):
        d = bytes(v)
        return _pb_tag(f, 2) + _pb_varint(len(d)) + d
    return b""


def crc7(data):
    c = 0
    for b in data:
        c = CRC7_TABLE[((2 * (c & 0xFF)) ^ (b & 0xFF)) & 0xFF] & 0x7F
    return c & 0x7F


def zigzag_encode(n):
    z = n << 1
    out = bytearray()
    while z >= 0x80:
        out.append((z & 0x7F) | 0x80)
        z >>= 7
    out.append(z)
    return bytes(out)


def uleb_encode(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            break
    return bytes(out)


def _generate_new_device() -> dict:
    device_list = [
        ("Samsung", "SM-G998B", "Adreno (TM) 660", "Android OS 12 / API-31"),
        ("Xiaomi", "2201122G", "Adreno (TM) 730", "Android OS 13 / API-33"),
        ("Realme", "RMX3700", "Mali-G710", "Android OS 14 / API-34"),
        ("OnePlus", "CPH2451", "Adreno (TM) 740", "Android OS 13 / API-33"),
        ("OPPO", "CPH2611", "Adreno (TM) 720", "Android OS 14 / API-34"),
        ("Vivo", "V2203", "Mali-G710", "Android OS 12 / API-31"),
        ("Poco", "M2102J20SG", "Adreno (TM) 660", "Android OS 13 / API-33"),
    ]
    brand, model, gpu, os_ver = random.choice(device_list)
    return {
        "unique_device_id": f"Google|{str(uuid.uuid4())}",
        "brand": brand, "model": model, "gpu_renderer": gpu, "system_software": os_ver,
        "screen_width": random.choice([1080, 1440, 720, 1280]),
        "screen_height": random.choice([2400, 3200, 1600, 2400]),
        "screen_dpi": str(random.randint(300, 420)),
        "memory": random.randint(2800, 6500),
        "processor_details": f"ARM64 FP ASIMD AES VMH | {random.randint(2200, 3200)} | {random.randint(6, 12)}",
        "client_ip": f"{random.randint(103, 223)}.{random.randint(10, 250)}.{random.randint(10, 250)}.{random.randint(10, 250)}"
    }


def get_device_for_account(account_identifier: str) -> dict:
    return _generate_new_device()


async def resolve_host_cloudflare(hostname: str) -> str:
    if not hostname:
        return hostname
    parts = hostname.split('.')
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return hostname
    now = time.time()
    if hostname in _DNS_CACHE:
        ip, exp = _DNS_CACHE[hostname]
        if now < exp:
            return ip

    def _query_cloudflare(server_ip: str) -> Optional[str]:
        s = None
        try:
            tx_id = random.randint(1000, 65535)
            header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
            qname = b"".join(bytes([len(part)]) + part.encode('ascii') for part in hostname.split('.')) + b"\x00"
            query_pkt = header + qname + struct.pack(">HH", 1, 1)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.2)
            s.sendto(query_pkt, (server_ip, 53))
            resp, _ = s.recvfrom(1024)
            if len(resp) >= 12:
                ancount = struct.unpack(">H", resp[6:8])[0]
                if ancount > 0:
                    offset = 12 + len(qname) + 4
                    for _ in range(ancount):
                        if offset >= len(resp):
                            break
                        if (resp[offset] & 0xC0) == 0xC0:
                            offset += 2
                        else:
                            while offset < len(resp) and resp[offset] != 0:
                                offset += 1 + resp[offset]
                            offset += 1
                        if offset + 10 > len(resp):
                            break
                        rtype, rclass, ttl, rdlen = struct.unpack(">HHIH", resp[offset:offset+10])
                        offset += 10
                        if rtype == 1 and rdlen == 4 and offset + 4 <= len(resp):
                            return socket.inet_ntoa(resp[offset:offset+4])
                        offset += rdlen
        except Exception:
            pass
        finally:
            if s:
                try: s.close()
                except Exception: pass
        return None

    loop = asyncio.get_running_loop()
    ip = await loop.run_in_executor(None, _query_cloudflare, CLOUDFLARE_PRIMARY_DNS)
    if not ip:
        ip = await loop.run_in_executor(None, _query_cloudflare, CLOUDFLARE_SECONDARY_DNS)
    if not ip:
        try:
            ip_info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
            if ip_info:
                ip = ip_info[0][4][0]
        except Exception:
            ip = hostname
    if ip:
        _DNS_CACHE[hostname] = (ip, now + _DNS_CACHE_TTL)
    return ip or hostname


def optimize_tcp_socket(sock: socket.socket):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "SIO_KEEPALIVE_VALS") and os.name == 'nt':
            try: sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 2000))
            except Exception: pass
        elif hasattr(socket, "TCP_KEEPIDLE"):
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                if hasattr(socket, "TCP_KEEPINTVL"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 2)
                if hasattr(socket, "TCP_KEEPCNT"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
            except Exception: pass
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
    except Exception:
        pass


def optimize_udp_socket(sock: socket.socket):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
        if hasattr(socket, 'SIO_UDP_CONNRESET') and os.name == 'nt':
            try: sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except Exception: pass
    except Exception:
        pass


async def safe_close_writer(writer):
    if not writer:
        return
    try:
        if not writer.is_closing():
            writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=1.5)
    except Exception:
        pass


async def aes_encrypt(payload, key, iv):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad(payload, AES.block_size))


async def get_playstore_version():
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: play_scraper('com.dts.freefireth', lang='hi', country='id')
        )
        return result.get("version")
    except Exception:
        return "1.132.8"


async def version_config(region=None):
    if region is None:
        region = ACCOUNT_REGION
    region = str(region).upper() if region else ACCOUNT_REGION
    app_version = await get_playstore_version()
    lang = REGION_LANG.get(region, "en")
    api_url = (
        "https://version.ggwhitehawk.com/live/ver.php"
        f"?version={app_version}"
        f"&lang={lang}&device=android&channel=android"
        f"&appstore=googleplay&region={region}"
        "&whitelist_version=1.3.0&whitelist_sp_version=1.0.0"
    )
    try:
        response = await client.get(api_url)
        response.raise_for_status()
        data = response.json()
        server_url = data.get("server_url")
        remote_version = data.get("remote_version")
        latest_release_version = data.get("latest_release_version")
        if not server_url or not remote_version or not latest_release_version:
            return None
        return latest_release_version, remote_version, server_url
    except Exception:
        return None


async def get_access_token(uid, password):
    url = "https://100067.connect.garena.com/oauth/guest/token/grant"
    hdrs = {
        "Host": "100067.connect.garena.com",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; SM-G998B Build/SP1A.210812.016)",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "close"
    }
    data = {
        "uid": uid, "password": password,
        "response_type": "token", "client_type": "2",
        "client_secret": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
        "client_id": "100067"
    }
    for attempt in range(5):
        try:
            response = await client.post(url, headers=hdrs, data=data)
            if response.status_code == 200:
                rd = response.json()
                open_id = rd.get("open_id")
                access_token = rd.get("access_token")
                platform = rd.get("platform", 4)
                if open_id and access_token:
                    return open_id, access_token, platform
            if response.status_code == 429:
                await asyncio.sleep(1)
                continue
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return None


async def parse_results(parsed_results):
    result_dict = {}
    for result in parsed_results:
        field_data = {"wire_type": result.wire_type}
        if result.wire_type in ("varint", "string", "bytes"):
            field_data["data"] = result.data
        elif result.wire_type == "length_delimited":
            if hasattr(result.data, "results"):
                field_data["data"] = await parse_results(result.data.results)
            elif isinstance(result.data, list):
                field_data["data"] = await parse_results(result.data)
            else:
                field_data["data"] = str(result.data)
        result_dict[str(result.field)] = field_data
    return result_dict


async def decode_protobuf(data):
    parsed_results = Parser().parse(data)
    parsed_results_dict = await parse_results(parsed_results)
    return json.dumps(parsed_results_dict)


async def build_majorlogin_payload(open_id, access_token, platform, client_version, device_info, verr=None):
    try:
        if verr is None:
            verr = client_version
        proto = thunderFF_pb2.MajorLoginReq()
        proto.event_time = str(datetime.now())[:-7]
        proto.game_name = "free fire"
        proto.platform_id = 1
        proto.client_version = str(verr)
        proto.client_version_code = "2019121229"
        proto.system_software = "Android OS 15 / API-35 (AP3A.240905.015.A2/185014)"
        proto.system_hardware = "Handheld"
        proto.device_type = "Handheld"
        proto.screen_width = 1600
        proto.screen_height = 719
        proto.screen_dpi = "234"
        proto.processor_details = "ARM64 FP ASIMD AES | 1820 | 8"
        proto.memory = 2798
        proto.gpu_renderer = "Mali-G57"
        proto.gpu_version = "OpenGL ES 3.2 v1.r49p1-04eac0.2848c17a2fd4e9340e06555168eaa3c9"
        proto.unique_device_id = "Google|f744e396-5694-4e65-995d-97a958f2bd1f"
        proto.client_ip = "197.0.137.129"
        proto.language = "pt-br"
        proto.open_id = str(open_id)
        proto.open_id_type = "4"
        proto.login_open_id_type = 4
        proto.access_token = str(access_token)
        proto.login_by = 2
        proto.platform_sdk_id = 1
        proto.origin_platform_type = "4"
        proto.primary_platform_type = "4"
        proto.reg_avatar = 1
        proto.channel_type = 3
        proto.telecom_operator = "TUNTEL"
        proto.network_operator_a = "TUNTEL"
        proto.network_type = "WIFI"
        proto.network_type_a = "WIFI"
        proto.cpu_type = 2
        proto.cpu_architecture = "64"
        proto.graphics_api = "OpenGLES2"
        proto.supported_astc_bitset = 8191
        proto.client_using_version = "7428b253defc164018c604a1ebbfebdf"
        proto.loading_time = 15078
        proto.release_channel = "android"
        proto.extra_info = "KqsHTx3+QOmBRR1WKvaWewlcpqJBfjki+PPHQoQG8+0yV+Uos7gUFFjHMQ/e7u6han6Fl77r7c3vMN3p8UbKgN+nfycQCgwBmWgBzomx2gj84c+p"
        proto.android_engine_init_flag = 111207
        proto.if_push = 1
        proto.is_vpn = 0

        memory_available = proto.memory_available
        memory_available.version = 55
        memory_available.hidden_value = 81

        proto.external_storage_total = 49973
        proto.external_storage_available = 11338
        proto.internal_storage_total = 854
        proto.internal_storage_available = 11466
        proto.game_disk_storage_total = 49973
        proto.game_disk_storage_available = 11466
        proto.external_sdcard_total_storage = 49973
        proto.external_sdcard_avail_storage = 11466

        proto.library_path = "/data/app/~~lHFxTCCbupG2QVJmsURtZw==/com.dts.freefireth-N3aCHpHNXpdxjD80uIIbww==/lib/arm64"
        proto.library_token = "b8e0cd5e295eee42f5860d3c86e483dd|/data/app/~~lHFxTCCbupG2QVJmsURtZw==/com.dts.freefireth-N3aCHpHNXpdxjD80uIIbww==/base.apk"

        base_payload = proto.SerializeToString()
        extra = b""
        extra += _pb_field(96, '{"cur_rate":[90,60,120],"support_etc2":false}')
        extra += _pb_field(97, 1)
        extra += _pb_field(99, "4")
        extra += _pb_field(100, "4")
        extra += _pb_field(102, b"\x17]ENWU\x0eR5")
        extra += _pb_field(104, 52882)
        extra += _pb_field(105, 1)
        extra += _pb_field(106, "https://dl.ak.freefiremobile.com/live/ABHotUpdates/|https://core-ak.freefiremobile.com/live/ABHotUpdates/|6b2078db9d22dd98f8e9386a39af8462")
        extra += _pb_field(107, "c8e41b7a93f02d56e1a94c7b8203f5d1")

        full_payload = base_payload + extra
        return await aes_encrypt(full_payload, AES_KEY, AES_IV)
    except Exception:
        return None


async def send_majorlogin(data, release_version, server_url):
    try:
        url = f"{server_url}MajorLogin" if server_url.endswith('/') else f"{server_url}/MajorLogin"
        req_headers = headers.copy()
        req_headers["ReleaseVersion"] = str(release_version)
        response = await client.post(url, headers=req_headers, data=data)
        if response.status_code != 200:
            return None
        response_content = response.content
        if len(response_content) < 40:
            return None

        res_proto = thunderFF_pb2.MajorLoginRes()
        try:
            res_proto.ParseFromString(response_content)
        except Exception:
            pass

        dict_res = {}
        try:
            parsed = Parser().parse(response_content.hex())
            dict_res = await parse_results(parsed)
        except Exception:
            pass

        key_val = get_proto_field(dict_res, 22)
        iv_val = get_proto_field(dict_res, 23)
        if not key_val:
            key_val = res_proto.aes_ak
        if not iv_val:
            iv_val = res_proto.iv_i

        if isinstance(key_val, str):
            try: key_val = bytes.fromhex(key_val)
            except Exception: pass
        if isinstance(iv_val, str):
            try: iv_val = bytes.fromhex(iv_val)
            except Exception: pass

        if not key_val or not iv_val:
            for offset in range(min(128, len(response_content))):
                try:
                    candidate = thunderFF_pb2.MajorLoginRes()
                    candidate.ParseFromString(response_content[offset:])
                    if candidate.region and candidate.token:
                        res_proto = candidate
                        break
                except Exception:
                    pass
            try:
                parsed = Parser().parse(response_content.hex())
                dict_res = await parse_results(parsed)
                kv = get_proto_field(dict_res, 22)
                ivv = get_proto_field(dict_res, 23)
                if isinstance(kv, str):
                    try: kv = bytes.fromhex(kv)
                    except Exception: pass
                if isinstance(ivv, str):
                    try: ivv = bytes.fromhex(ivv)
                    except Exception: pass
                if kv: key_val = kv
                if ivv: iv_val = ivv
            except Exception:
                pass

        if key_val:
            try: res_proto.aes_ak = key_val
            except Exception: pass
        if iv_val:
            try: res_proto.iv_i = iv_val
            except Exception: pass

        return res_proto
    except Exception as e:
        log(f"[-] send_majorlogin error: {e}")
        return None


async def send_getlogin(data, base_url, token, release_version):
    try:
        url = f"{base_url.rstrip('/')}/GetLoginData"
        req_headers = headers.copy()
        req_headers["ReleaseVersion"] = release_version
        req_headers['Authorization'] = f"Bearer {token}"
        req_headers['Host'] = "clientbp.ppmainecoonghj.com"
        response = await client.post(url, headers=req_headers, data=data)
        if response.status_code != 200:
            return None
        response_content = response.content

        res_proto = thunderFF_pb2.GetLoginDataRes()
        try:
            res_proto.ParseFromString(response_content)
        except Exception:
            pass

        def _extract_addr(raw_bytes, field_no):
            try:
                pos = 0
                n = len(raw_bytes)
                def read_varint(buf, p):
                    res = 0
                    sh = 0
                    while p < len(buf):
                        b = buf[p]
                        p += 1
                        res |= (b & 0x7F) << sh
                        if not (b & 0x80):
                            break
                        sh += 7
                    return res, p
                fields = {}
                while pos < n:
                    try:
                        key, pos = read_varint(raw_bytes, pos)
                    except Exception:
                        break
                    fn = key >> 3
                    wt = key & 0x07
                    try:
                        if wt == 0:
                            val, pos = read_varint(raw_bytes, pos)
                        elif wt == 2:
                            ln, pos = read_varint(raw_bytes, pos)
                            val = raw_bytes[pos:pos + ln]
                            pos += ln
                        elif wt == 5:
                            val = raw_bytes[pos:pos + 4]
                            pos += 4
                        elif wt == 1:
                            val = raw_bytes[pos:pos + 8]
                            pos += 8
                        else:
                            break
                    except Exception:
                        break
                    fields.setdefault(fn, []).append(val)
                if field_no in fields:
                    v = fields[field_no][0]
                    if isinstance(v, bytes):
                        try:
                            return v.decode('utf-8', 'ignore')
                        except Exception:
                            return None
                    return str(v)
            except Exception:
                return None

        try:
            for offset in range(0, min(80, len(response_content))):
                fa = _extract_addr(response_content[offset:], 14)
                ia = _extract_addr(response_content[offset:], 32)
                if fa and ":" in fa and ia and ":" in ia:
                    res_proto.functional_addrs = fa
                    res_proto.informational_addrs = ia
                    break
        except Exception:
            pass

        dict_res = {}
        try:
            parsed = Parser().parse(response_content.hex())
            dict_res = await parse_results(parsed)
        except Exception:
            pass

        return res_proto, dict_res
    except Exception as e:
        log(f"[-] send_getlogin error: {e}")
        return None


async def build_tcp_startup_packet(account_id, token, server_time, key, iv, region=None, typ='OnLine'):
    if region is None:
        region = ACCOUNT_REGION
    uid_hex = f"{int(account_id):016x}"
    timestamp_hex = f"{int(server_time):08x}"
    encode_token = token.encode()
    encrypted_packet = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(encode_token, 16)).hex()
    encrypted_packet_length = f"{len(encrypted_packet) // 2:08x}"
    if typ == 'OnLine':
        return f"9015{uid_hex}{timestamp_hex}00000000{encrypted_packet_length}{encrypted_packet}"
    else:
        return f"9015{uid_hex}{timestamp_hex}{encrypted_packet_length}{encrypted_packet}"


async def send_keep_alive(region=None):
    try:
        if region is None:
            region = ACCOUNT_REGION
        reg = str(region).upper() if region else ACCOUNT_REGION
        if reg == "ME":
            ka_hex = "0219"
        elif reg == "IND":
            ka_hex = "0214"
        else:
            ka_hex = "0215"
        return bytes.fromhex(ka_hex)
    except Exception:
        return bytes.fromhex("0219")


async def start_game_battle_royale(region, client_version, writer, key, iv):
    packet = bytes.fromhex("080112800a0a010110013a110a044944433110aa011a064555524f50453a100a044944433210311a064555524f504540014a0801090a0b1219202758016291090a8001303838463832424630324139363736373032303130313030303030303030303030303136303030313030313530303032323246393745454530463030303030303436373632353134303030303030303030303030303030303030303030303030303030303030303030303030303066663030303030303030636163666131366410241afb02735d5e571400024a775d45414d1a041b1c001f11010449715f4243481a001e1d071c1703004b1a4066785c524570735c51486775421b5c5a4c07504042685a63610816054e19025e75196001477c015165406370195f5547404e4550640103020f1304064863754268676c755f65576e40467e5f0a417a4701026d675d6e73670b1108495a4c6a0b78470b740065645e525a057258425f584a447d4e6759440c11044e7c596d7f4b625f7d04055a47505c4e1d6b5b4107447d7201057d7f0f14084e430457674f7e517d72015172415d027473577c4d615f79535256780911030f4d5e027a797f614165067806505d53777750475e75064257076500460817014e741e7e5078487e7a7c465e7669767153497064605a7376677773550d160148037e18675966787f4c42607a645f577e7b441b460776026b18685d0b110205490060020f70676175654674706671797f41067346677c4e06585e780f15074c57047b40517075415f6364027259674b5b0166407f7340600407770a22047a5d5c52300b3a0a167305067162727516134208312e3133302e3232480350015ae90403626253513635686e556f4e36416456324b796f566c636f477776484f624e56526c4d727073504b4f43654177616848494176795556497273743752737149734a7a786b3247525268377a2f637664626d504f6a73552f79626d38547a4c69586d2f474351696d494b53486833447955726f39515152756c34545350626d6d624b7949565937545671577059455372323646572f59624578507338514f706d317372785455736c30796a434144444d4f34616a654b615753366361496c554b4963797a494e396d52516f715277687939797257476d337a644345337a6a61436f492f5a585233656f65365a42647a64677654636b6b665733356e4d4c6a6a565072564b6433523172756174394e50514150724a5546627859696c4c5a3859707336654d5447666b6649793574666a526c314d4648706b51774c6373374439656378566c41636f374e664f6d2b30654756466c4434744478706771385533595973587645384842502f70666c767a737138316a32524f4d7857437556445442492f684735625462773166456e4249725162762b636144775147696f74554e316d4c4b77734379456f4766706746614251457645672b736a764c4c78704743334c304a5344532f74526169504354553344374e6249306547516651622f5a466f4c36455630775a324d6f583932414c572f5049752f56634663584e70596b356f7966326151416a536971486a2f363276354843644f525551303578754e6171795251625653704654303137655237675255636b4966366c6f447476342b514e4a4670766d74757077707774396a5a5974437a4b56743657726d6e36785837706658456251555434684f3758a201050803108703a201050804108103a20105080510c001a20105081d10cc01a2010408161078a20105080e10af01a201020815")
    proto = thunderFF_pb2.StartMatch()
    proto.ParseFromString(packet)
    if hasattr(proto.main, 'region_list') and len(proto.main.region_list) > 0:
        proto.main.region_list[0].region = region
        if len(proto.main.region_list) > 1:
            proto.main.region_list[1].region = region
    if hasattr(proto.main, 'client_version'):
        proto.main.client_version.remote_version = client_version
    packet = proto.SerializeToString()
    encrypted_packet = (await aes_encrypt(packet, key, iv)).hex()
    packet_length = len(encrypted_packet) // 2
    hex_length = hex(packet_length)[2:]
    hex_length = hex_length if len(hex_length) > 1 else "0" + hex_length
    reg = str(region).upper() if region else ACCOUNT_REGION
    reg_prefix = REGION_PREFIX.get(reg, "031500")
    final_packet = reg_prefix + "0" * (6 - len(hex_length)) + hex_length + encrypted_packet
    writer.write(bytes.fromhex(final_packet))
    await writer.drain()
    log(f"[BR] Match search sent ({packet_length} bytes) | Region: {reg}")


def has_ssan_zig(n):
    z = (n << 1) & 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while z >= 0x80:
        out.append((z & 0x7F) | 0x80)
        z >>= 7
    out.append(z)
    return bytes(out)


async def tea_enc(v0, v1, k0, k1, k2, k3):
    s = 0
    for _ in range(_ROUNDS):
        s = (s + _DELTA) & 0xFFFFFFFF
        v0 = (v0 + (((((v1 << 4) & 0xFFFFFFFF) + k0) & 0xFFFFFFFF ^
                      ((v1 + s) & 0xFFFFFFFF) ^
                      (((v1 >> 5) + k1) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        v1 = (v1 + (((((v0 << 4) & 0xFFFFFFFF) + k2) & 0xFFFFFFFF ^
                      ((v0 + s) & 0xFFFFFFFF) ^
                      (((v0 >> 5) + k3) & 0xFFFFFFFF)))) & 0xFFFFFFFF
    return v0, v1


async def tea_dec(v0, v1, k0, k1, k2, k3):
    s = (_DELTA * _ROUNDS) & 0xFFFFFFFF
    for _ in range(_ROUNDS):
        v1 = (v1 - (((((v0 << 4) & 0xFFFFFFFF) + k2) & 0xFFFFFFFF ^
                      ((v0 + s) & 0xFFFFFFFF) ^
                      (((v0 >> 5) + k3) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        v0 = (v0 - (((((v1 << 4) & 0xFFFFFFFF) + k0) & 0xFFFFFFFF ^
                      ((v1 + s) & 0xFFFFFFFF) ^
                      (((v1 >> 5) + k1) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        s = (s - _DELTA) & 0xFFFFFFFF
    return v0, v1


async def tea_cbc_encrypt(padded, key_bytes):
    k0, k1, k2, k3 = (struct.unpack_from("<I", key_bytes, o)[0] for o in (0, 4, 8, 12))
    out = bytearray(len(padded))
    prev_cipher = bytearray(8)
    prev_intermediate = bytearray(8)
    for i in range(0, len(padded), 8):
        xored = bytearray(8)
        for j in range(8):
            xored[j] = padded[i + j] ^ prev_cipher[j]
        e0, e1 = await tea_enc(
            struct.unpack_from("<I", xored, 0)[0],
            struct.unpack_from("<I", xored, 4)[0],
            k0, k1, k2, k3,
        )
        enc = bytearray(8)
        struct.pack_into("<I", enc, 0, e0)
        struct.pack_into("<I", enc, 4, e1)
        for j in range(8):
            out[i + j] = enc[j] ^ prev_intermediate[j]
        prev_cipher[:] = out[i:i + 8]
        prev_intermediate[:] = xored
    return bytes(out)


async def build_padded(content):
    pad_len = (8 - (len(content) + 10) % 8) % 8
    return bytes([pad_len, 0, 0]) + b"\x00" * pad_len + content + b"\x00" * 7


async def encode_header(layout, send_option, cmd, order_id, flags, length, k, v80):
    out = bytearray()
    for code in layout:
        value = {0: send_option, 1: cmd, 2: order_id, 3: flags, 4: length}[code]
        if _FIELD_SIZES[code] == 1:
            out.append((value & 0xFF) ^ k)
        else:
            v = ((value & 0xFFFF) ^ v80) & 0xFFFF
            out.append(v & 0xFF)
            out.append((v >> 8) & 0xFF)
    return bytes(out)


async def crc7_buff(crc, buf):
    c = crc & 0x7F
    for b in buf:
        c = CRC7_TABLE[((2 * (c & 0xFF)) ^ (b & 0xFF)) & 0xFF] & 0x7F
    return c & 0x7F


async def sv_frame(msg_key, layout, send_option, cmd, order_id, flags, content, key, encrypted=True):
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    body = await tea_cbc_encrypt(await build_padded(content), key) if encrypted else content
    hdr = bytearray([msg_key, 0]) + await encode_header(layout, send_option, cmd, order_id, flags, len(body), k, v80)
    packet = bytearray(hdr + body)
    packet[1] = await crc7_buff(0, bytes(packet[2:])) & 0x7F
    return bytes(packet)


async def build_match_startup_packets(token, udp_key, match_code, account_id, block_val,
                                      server_ip="", region=None, client_version="1.132.8",
                                      client_version_code="2019121229", access_token="",
                                      mode_id=MODE_ID, map_id=MAP_ID):
    if region is None:
        region = ACCOUNT_REGION
    token = token.strip()
    udp_key = bytes.fromhex(udp_key)
    match_code = [int(ch) for ch in str(match_code).strip()]

    thunder_jwt = token[:660] if len(token) > 660 else token
    sharma_jwt = token[660:] if len(token) > 660 else ""
    encoded_thunder_jwt = thunder_jwt.encode() if isinstance(thunder_jwt, str) else thunder_jwt
    encoded_sharma_jwt = sharma_jwt.encode() if isinstance(sharma_jwt, str) else sharma_jwt

    garena420 = has_ssan_zig(len(encoded_thunder_jwt)) + encoded_thunder_jwt
    reg = str(region).upper() if region else ACCOUNT_REGION

    csoversea_block = bytes.fromhex(
        "ca0163736f7665727365612e7374726f6e67686f6c642e66726565666972656d6f62696c652e636f6d"
        "3b302e302e302e303b33342e3132362e37362e34353b33342e38372e3137372e31343b33342e38372e"
        "3137302e3233303b33352e3138352e3138332e35370000000000000100000000000000000000000001"
        "00000800000100000000000100a8a2d7bebd8d8bdf110200"
    )

    mid = bytes.fromhex('0000000001000102030101') + has_ssan_zig(len(reg)) + reg.encode()
    mid += bytes.fromhex('0001030003000004')
    mid += has_ssan_zig(len(client_version)) + client_version.encode()
    mid += has_ssan_zig(len(client_version_code)) + client_version_code.encode()
    mid += csoversea_block

    clean_ip = server_ip.split(':')[0] if server_ip else "0.0.0.0"
    mid += has_ssan_zig(len(clean_ip)) + clean_ip.encode()

    clean_acc_tok = access_token.strip() if access_token else ""
    if clean_acc_tok:
        mid += has_ssan_zig(len(clean_acc_tok)) + clean_acc_tok.encode()

    mid += has_ssan_zig(len(encoded_sharma_jwt)) + encoded_sharma_jwt

    tg_garena420 = (
        uleb_encode(int(account_id)) +
        uleb_encode(int(block_val)) +
        uleb_encode(1) +
        uleb_encode(int(mode_id)) +
        uleb_encode(int(block_val)) +
        uleb_encode(int(map_id)) +
        mid
    )

    process = await sv_frame(0x5E, match_code, 2, 447, 0, 1, garena420, udp_key)
    loading = await sv_frame(0x5A, match_code, 2, 448, 1, 1, tg_garena420, udp_key)
    return process.hex(), loading.hex()


async def produce_xor_key(secret_key):
    k = secret_key[0] if secret_key and len(secret_key) > 0 else 10
    return k, ((k << 8) | k) & 0xFFFF


async def parse_layout(layout):
    if isinstance(layout, str):
        return [int(ch) for ch in layout.strip()]
    return list(layout)


async def tea_cbc_decrypt(body, key_bytes):
    k0, k1, k2, k3 = (struct.unpack_from("<I", key_bytes, o)[0] for o in (0, 4, 8, 12))
    out = bytearray(len(body))
    prev_intermediate = bytearray(8)
    prev_cipher = bytearray(8)
    xored = bytearray(8)
    dec = bytearray(8)
    for i in range(0, len(body), 8):
        for j in range(8):
            xored[j] = body[i + j] ^ prev_intermediate[j]
        d0, d1 = await tea_dec(
            struct.unpack_from("<I", xored, 0)[0],
            struct.unpack_from("<I", xored, 4)[0],
            k0, k1, k2, k3
        )
        struct.pack_into("<I", dec, 0, d0)
        struct.pack_into("<I", dec, 4, d1)
        for j in range(8):
            out[i + j] = dec[j] ^ prev_cipher[j]
        prev_cipher[:] = body[i:i + 8]
        prev_intermediate[:] = dec
    return bytes(out)


async def build_hello_packet(text, key, layout):
    data = text.encode("utf-8")
    if len(data) > 25:
        raise ValueError(f"Text is too long ({len(data)} bytes)")
    content = b"\x10\x00\x00\x00" + data + b"\x00" * (29 - 4 - len(data))
    k, v80 = await produce_xor_key(key)
    layout = await parse_layout(layout)
    padded = await build_padded(content)
    enc_body = await tea_cbc_encrypt(padded, key)
    header_bytes = await encode_header(layout, 1, 1, 0, 1, len(enc_body), k, v80)
    packet = bytearray([0x63, 0x00]) + header_bytes + enc_body
    packet[1] = await crc7_buff(0, packet[2:]) & 0x7F
    return bytes(packet).hex()


async def classify(frame):
    cmd = frame["cmd"]
    msg_name = MESSAGE_ID_TO_NAME.get(cmd, f"UNKNOWN_{cmd}")
    if msg_name == "UDP_HELLO":
        return "HELLO"
    if msg_name == "UDP_ACK":
        return "ACK"
    if msg_name == "UDP_PING":
        return "PING"
    if msg_name == "RUDP_JOIN_MATCH":
        return "JOIN_MATCH"
    if msg_name.startswith("RUDP_"):
        return msg_name
    if msg_name.startswith("UDP_"):
        return msg_name
    return "DATA"


async def build_packet(msg_key, layout, send_option, cmd, order_id, flags, content, key, encrypted=True):
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    body = await tea_cbc_encrypt(await build_padded(content), key) if encrypted else content
    hdr = bytearray([msg_key, 0])
    for code in layout:
        value = {0: send_option, 1: cmd, 2: order_id, 3: flags, 4: len(body)}[code]
        if _FIELD_SIZES[code] == 1:
            hdr.append((value & 0xFF) ^ k)
        else:
            v = ((value & 0xFFFF) ^ v80) & 0xFFFF
            hdr.append(v & 0xFF)
            hdr.append((v >> 8) & 0xFF)
    packet = bytearray(hdr + body)
    packet[1] = await crc7_buff(0, bytes(packet[2:])) & 0x7F
    return bytes(packet)


async def layouts_from_mask(mask):
    ru = [int(c) for c in str(mask).strip()]
    nr = [c for c in ru if c != 2]
    return ru, nr


async def reply_for(frame, key, mask, ack_key=0x68, ping_key=0x6D, hello_key=0x5B, ack_style="short"):
    ru, nr = await layouts_from_mask(mask)
    typ = await classify(frame)
    if typ == "HELLO":
        if ack_style == "echo":
            content = frame["content"] if frame["content"] else b"\x10\x00\x00\x00"
            return typ, await build_packet(hello_key, nr, 1, 1, None, 1, content, key)
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, b"\x01\x00", key)
    if typ == "ACK":
        content = frame["content"] if frame["content"] else b"\x01\x00"
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, content, key)
    if typ == "PING":
        c = frame["content"]
        counter = c[:4] if len(c) >= 4 else c
        return typ, await build_packet(ping_key, nr, 0, 3, None, 0, counter + b"\x00\x00\x00", key, encrypted=False)
    if typ == "JOIN_MATCH":
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, b"\x02\x00", key)
    return typ, None


async def keepalive_ping(sock, ip, port, key_bytes, mask, stop_event,
                         log_cb=None, match_index=None):
    nr = (await layouts_from_mask(mask))[1]
    ping_keys = [0x66, 0x6D, 0x69, 0x6C, 0x6B, 0x6E, 0x6F, 0x70]
    loop = asyncio.get_event_loop()
    i = 0
    sent = 0
    while not stop_event.is_set():
        pk = ping_keys[i % len(ping_keys)]
        counter = int(time.time() * 1000) & 0xFFFFFFFF
        pkt = await build_packet(pk, nr, 0, 3, None, 0, struct.pack("<I", counter) + b"\x00\x00\x00", key_bytes, encrypted=False)
        try:
            await udp_sendto(sock, pkt, (ip, port))
            sent += 1
            if log_cb and (sent == 1 or sent % 5 == 0):
                try:
                    log_cb(f"[MATCH #{match_index}] [PING] keep-alive #{sent} sent -> {(ip, port)}")
                except Exception:
                    pass
        except Exception:
            pass
        i += 1
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass


async def try_header(buf, layout, k, v80):
    off = 2
    out = {}
    for code in layout:
        size = _FIELD_SIZES[code]
        if off + size > len(buf):
            return None
        out[_FIELD_NAMES[code]] = (buf[off] ^ k) if size == 1 else ((buf[off] | (buf[off + 1] << 8)) ^ v80) & 0xFFFF
        off += size
    out["headerLen"] = off
    return out


async def oicq_unpad(padded):
    if not padded or len(padded) < 8:
        return None
    if not all(padded[-1 - i] == 0 for i in range(7)):
        return None
    pad_len = padded[0] & 0x07
    s = 3 + pad_len
    e = len(padded) - 7
    return padded[s:e] if s < e else b""


async def decode_packet(packet, key, mask=None):
    data = bytes(packet) if isinstance(packet, bytes) else bytes.fromhex(packet)
    if len(data) < 8:
        return None
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    crc_ok = (data[1] & 0x7F) == await crc7_buff(0, data[2:])
    candidates = []
    if mask:
        ru, nr = await layouts_from_mask(mask)
        layouts = [("RUDP", ru), ("nonRUDP", nr)]
    else:
        layouts = [("RUDP", list(p)) for p in itertools.permutations([0, 1, 2, 3, 4])]
        layouts += [("nonRUDP", list(p)) for p in itertools.permutations([0, 1, 3, 4])]
    for kind, layout in layouts:
        f = await try_header(data, layout, k, v80)
        if not f:
            continue
        if f.get("flags", 0) > 7 or f.get("sendOption", 0) > 7:
            continue
        if f.get("length", -1) != len(data) - f["headerLen"]:
            continue
        body = data[f["headerLen"]:f["headerLen"] + f["length"]]
        content = None
        padded = None
        if f.get("flags", 0) & 1:
            if len(body) < 8 or len(body) % 8 != 0:
                continue
            padded = await tea_cbc_decrypt(body, key)
            content = await oicq_unpad(padded)
            if content is None:
                continue
        else:
            content = body
        score = (1 if crc_ok else 0) + (1 if content is not None else 0)
        candidates.append({
            "kind": kind, "layout": layout, "headerLen": f["headerLen"],
            "msgKey": data[0], "cmd": f.get("cmd", 0), "flags": f.get("flags", 0),
            "sendOption": f.get("sendOption", 0), "orderId": f.get("orderId"),
            "length": f.get("length", 0), "content": content, "crcOk": crc_ok,
            "padded": padded, "score": score, "total": len(data),
        })
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c["kind"] == "RUDP" or c["kind"] == "nonRUDP", c["score"]), reverse=True)
    return candidates[0]


async def play_game(server_ip_port, thunder, sharma, udp_key, match_code,
                    account_id, player_region, client_version, key, iv,
                    match_index: int, status_cb=None, log_cb=None):
    match_start_time = time.time()
    ping_task = None
    sock = None
    ping_stop = asyncio.Event()
    uid_str = str(account_id)
    completed_cleanly = False

    def emit(msg):
        try:
            if log_cb:
                log_cb(msg)
            else:
                log(msg)
        except Exception:
            pass

    async def set_status(st):
        try:
            if status_cb:
                res = status_cb(st)
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            pass

    try:
        ip, port = server_ip_port.split(":")
        port = int(port)
        resolved_ip = await resolve_host_cloudflare(ip)

        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('0.0.0.0', 0))
        except Exception:
            pass
        optimize_udp_socket(sock)
        sock.setblocking(False)

        udp_key_bytes = bytes.fromhex(udp_key)
        hello_packet = await build_hello_packet(f"{account_id}_2585", udp_key_bytes, match_code)
        hello_bytes = bytes.fromhex(hello_packet)

        # Send HELLO with retries so a transient send error never kills the match.
        hello_ok = False
        last_err = None
        for attempt in range(1, 6):
            try:
                await udp_sendto(sock, hello_bytes, (resolved_ip, port))
                emit(f"[MATCH #{match_index}] [SEND-OK] HELLO size={len(hello_bytes)}B -> {(resolved_ip, port)}")
                hello_ok = True
                break
            except Exception as e:
                last_err = e
                emit(f"[MATCH #{match_index}] [SEND-FAIL] HELLO (attempt {attempt}/5): {type(e).__name__}: {e}")
                await asyncio.sleep(0.4)
        if not hello_ok:
            emit(f"[MATCH #{match_index}] [!] Could not send HELLO after retries: {type(last_err).__name__}: {last_err}")
            emit(f"[MATCH #{match_index}] [i] Tip: on Windows this is usually the asyncio Proactor loop; "
                 f"the bot now forces a Selector loop, but if it persists run with "
                 f"'set PYTHONASYNCIODEBUG=' or update Python.")

        ack_state = "waiting_for_hello_reply"
        thunder_sent = False
        sharma_sent = False
        join_match_received = False
        local_closed = False
        send_lock = asyncio.Lock()

        # We are now talking to the match server -> report "In Game".
        if hello_ok:
            await set_status("in_game")

        ping_task = asyncio.create_task(
            keepalive_ping(sock, resolved_ip, port, udp_key_bytes, match_code, ping_stop,
                           log_cb=log_cb, match_index=match_index)
        )

        last_activity = time.time()
        MAX_IDLE_BEFORE_HELLO_RESEND = 7.0

        async def send_thunder_sharma_inline():
            nonlocal ack_state, thunder_sent, sharma_sent
            if thunder_sent:
                return
            async with send_lock:
                if thunder_sent:
                    return
                try:
                    thunder_b = bytes.fromhex(thunder)
                    await udp_sendto(sock, thunder_b, (resolved_ip, port))
                    emit(f"[MATCH #{match_index}] [SEND-OK] THUNDER size={len(thunder_b)}B -> {(resolved_ip, port)}")
                    thunder_sent = True

                    await asyncio.sleep(0.3)

                    prepare_ack = await build_packet(
                        0x68, (await layouts_from_mask(match_code))[1],
                        0, 2, None, 1, b"\x01\x00", udp_key_bytes
                    )
                    await udp_sendto(sock, prepare_ack, (resolved_ip, port))
                    emit(f"[MATCH #{match_index}] [SEND-OK] PREPARE_ACK size={len(prepare_ack)}B -> {(resolved_ip, port)}")

                    await asyncio.sleep(0.4)

                    sharma_b = bytes.fromhex(sharma)
                    await udp_sendto(sock, sharma_b, (resolved_ip, port))
                    emit(f"[MATCH #{match_index}] [SEND-OK] SHARMA size={len(sharma_b)}B -> {(resolved_ip, port)}")

                    sharma_sent = True
                    ack_state = "thunder_sharma_sent"
                    emit(f"[MATCH #{match_index}] Startup delivered. Playing.")
                    await set_status("in_game")
                except Exception as e:
                    emit(f"[MATCH #{match_index}] [SEND-FAIL] Startup: {e}")

        while not local_closed:
            elapsed = time.time() - match_start_time
            if elapsed > MAX_MATCH_DURATION:
                completed_cleanly = True
                break

            try:
                response, server_addr = await udp_recvfrom(sock, 65535, 2.0)
                if response:
                    last_activity = time.time()
                    frame = await decode_packet(response, udp_key_bytes, match_code)
                    if frame:
                        ptype = await classify(frame)
                        cmd = frame['cmd']

                        if cmd in [103, 107]:
                            emit(f"[\u2605] Match #{match_index} finished (cmd {cmd})")
                            completed_cleanly = True
                            local_closed = True
                            continue

                        if cmd == 101:
                            try:
                                ack_pkt = await build_packet(
                                    0x68, (await layouts_from_mask(match_code))[1],
                                    0, 2, None, 1, b"\x01\x00", udp_key_bytes
                                )
                                await udp_sendto(sock, ack_pkt, server_addr)
                                emit(f"[MATCH #{match_index}] [SEND-OK] ACK-101 size={len(ack_pkt)}B -> {server_addr}")
                            except Exception as e:
                                emit(f"[MATCH #{match_index}] [SEND-FAIL] ACK-101: {e}")

                        if ptype in ["ACK", "PING", "HELLO", "JOIN_MATCH"]:
                            if ptype == "HELLO" and ack_state == "waiting_for_hello_reply":
                                typ, reply = await reply_for(
                                    frame, udp_key_bytes, match_code, ack_style="short"
                                )
                                if reply:
                                    try:
                                        await udp_sendto(sock, reply, server_addr)
                                        emit(f"[MATCH #{match_index}] [SEND-OK] HELLO-REPLY size={len(reply)}B -> {server_addr}")
                                    except Exception as e:
                                        emit(f"[MATCH #{match_index}] [SEND-FAIL] HELLO-REPLY: {e}")
                                ack_state = "ack_sent_waiting"
                            elif ptype == "ACK":
                                if ack_state == "waiting_for_hello_reply":
                                    typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                    if reply:
                                        try:
                                            await udp_sendto(sock, reply, server_addr)
                                            emit(f"[MATCH #{match_index}] [SEND-OK] ACK-REPLY size={len(reply)}B -> {server_addr}")
                                        except Exception as e:
                                            emit(f"[MATCH #{match_index}] [SEND-FAIL] ACK-REPLY: {e}")
                                    ack_state = "ready_to_send_thunder"
                                elif ack_state == "ack_sent_waiting":
                                    ack_state = "ready_to_send_thunder"
                                else:
                                    typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                    if reply:
                                        try:
                                            await udp_sendto(sock, reply, server_addr)
                                            emit(f"[MATCH #{match_index}] [SEND-OK] ACK-REPLY size={len(reply)}B -> {server_addr}")
                                        except Exception as e:
                                            emit(f"[MATCH #{match_index}] [SEND-FAIL] ACK-REPLY: {e}")
                            elif ptype == "PING":
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                if reply:
                                    try:
                                        await udp_sendto(sock, reply, server_addr)
                                        emit(f"[MATCH #{match_index}] [SEND-OK] PING-REPLY size={len(reply)}B -> {server_addr}")
                                    except Exception as e:
                                        emit(f"[MATCH #{match_index}] [SEND-FAIL] PING-REPLY: {e}")
                            elif ptype == "JOIN_MATCH" and not join_match_received:
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                if reply:
                                    try:
                                        await udp_sendto(sock, reply, server_addr)
                                        emit(f"[MATCH #{match_index}] [SEND-OK] JOIN-MATCH-REPLY size={len(reply)}B -> {server_addr}")
                                    except Exception as e:
                                        emit(f"[MATCH #{match_index}] [SEND-FAIL] JOIN-MATCH-REPLY: {e}")
                                    join_match_received = True

            except asyncio.TimeoutError:
                if ack_state == "ready_to_send_thunder" and not thunder_sent:
                    await send_thunder_sharma_inline()
                elif ack_state == "waiting_for_hello_reply":
                    if (time.time() - last_activity) > MAX_IDLE_BEFORE_HELLO_RESEND:
                        try:
                            pkt = await build_hello_packet(
                                f"{account_id}_2585", udp_key_bytes, match_code
                            )
                            await udp_sendto(sock, bytes.fromhex(pkt), (resolved_ip, port))
                            emit(f"[MATCH #{match_index}] [SEND-OK] HELLO-RESEND size={len(bytes.fromhex(pkt))}B -> {(resolved_ip, port)}")
                        except Exception as e:
                            emit(f"[MATCH #{match_index}] [SEND-FAIL] HELLO-RESEND: {e}")
                        last_activity = time.time()
                    if (time.time() - match_start_time) > 25.0:
                        emit(f"[MATCH #{match_index}] No HELLO reply within 25s - restarting match search.")
                        completed_cleanly = True
                        break
                else:
                    # We are inside the match; if the server goes silent for too
                    # long the match is over -> finish so a new one can start.
                    if (time.time() - last_activity) > MATCH_END_IDLE_TIMEOUT:
                        emit(f"[MATCH #{match_index}] No server activity for {MATCH_END_IDLE_TIMEOUT}s - match considered finished.")
                        completed_cleanly = True
                        break

            except BlockingIOError:
                await asyncio.sleep(0.05)
            except OSError:
                await asyncio.sleep(0.5)
                continue
            except Exception:
                await asyncio.sleep(0.5)
                continue

            if ack_state == "ready_to_send_thunder" and not thunder_sent:
                await send_thunder_sharma_inline()

        return f"match #{match_index} finished"

    except Exception as e:
        emit(f"[MATCH #{match_index}] Session error: {e}")
        return f"match #{match_index} error"

    finally:
        ping_stop.set()
        if ping_task:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
        if sock:
            try:
                sock.close()
            except Exception:
                pass


async def reconnect_match(match_info, status_cb=None, log_cb=None, stop_event=None,
                          max_idle=MATCH_END_IDLE_TIMEOUT):
    """Reconnect to an *already running* match and keep sending keep-alive pings.

    Used when the account is (or may still be) inside a match, so that we do NOT
    start a brand new match. We re-open the UDP socket to the match server, send
    HELLO, answer the server, then keep the session alive with pings until the
    match ends or the user stops it.

    Returns one of:
      * "reconnected" - server answered, we stayed in the match until it ended
      * "no-match"    - server never answered -> the match is over / not live
      * "stopped"     - stop_event was set by the user
    """
    server_ip_port = str(match_info.get("server_ip_port") or "")
    udp_key = match_info.get("udp_key") or ""
    match_code = match_info.get("match_code") or ""
    account_id = str(match_info.get("account_id") or "")
    match_index = match_info.get("match_index", 0)

    def emit(msg):
        try:
            if log_cb:
                log_cb(msg)
            else:
                log(msg)
        except Exception:
            pass

    async def set_status(st):
        try:
            if status_cb:
                res = status_cb(st)
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            pass

    if not (server_ip_port and ":" in server_ip_port and udp_key and match_code):
        emit(f"[MATCH #{match_index}] [RECONNECT] Missing match info - cannot reconnect.")
        return "no-match"

    sock = None
    ping_task = None
    ping_stop = asyncio.Event()

    try:
        ip, port = server_ip_port.split(":")
        port = int(port)
        resolved_ip = await resolve_host_cloudflare(ip)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", 0))
        except Exception:
            pass
        optimize_udp_socket(sock)
        sock.setblocking(False)

        udp_key_bytes = bytes.fromhex(udp_key)
        hello_packet = await build_hello_packet(f"{account_id}_2585", udp_key_bytes, match_code)
        hello_bytes = bytes.fromhex(hello_packet)

        emit(f"[MATCH #{match_index}] [RECONNECT] Reconnecting to existing match at {server_ip_port} ...")

        got_reply = False
        for attempt in range(1, 6):
            if stop_event is not None and stop_event.is_set():
                return "stopped"
            try:
                await udp_sendto(sock, hello_bytes, (resolved_ip, port))
                emit(f"[MATCH #{match_index}] [RECONNECT] HELLO #{attempt} -> {(resolved_ip, port)}")
            except Exception as e:
                emit(f"[MATCH #{match_index}] [RECONNECT] HELLO send error: {e}")
            try:
                response, server_addr = await udp_recvfrom(sock, 65535, 2.5)
                if response:
                    got_reply = True
                    frame = await decode_packet(response, udp_key_bytes, match_code)
                    if frame:
                        ptype = await classify(frame)
                        typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                        if reply:
                            try:
                                await udp_sendto(sock, reply, server_addr)
                                emit(f"[MATCH #{match_index}] [RECONNECT] {ptype or 'REPLY'} reply sent -> {server_addr}")
                            except Exception as e:
                                emit(f"[MATCH #{match_index}] [RECONNECT] reply error: {e}")
                    break
            except asyncio.TimeoutError:
                continue

        if not got_reply:
            emit(f"[MATCH #{match_index}] [RECONNECT] No reply from match server - match is over.")
            return "no-match"

        await set_status("in_game")
        emit(f"[MATCH #{match_index}] [RECONNECT] Reconnected. Sending keep-alive pings.")

        ping_task = asyncio.create_task(
            keepalive_ping(sock, resolved_ip, port, udp_key_bytes, match_code, ping_stop,
                           log_cb=log_cb, match_index=match_index)
        )

        last_activity = time.time()
        while True:
            if stop_event is not None and stop_event.is_set():
                return "stopped"
            try:
                response, server_addr = await udp_recvfrom(sock, 65535, 2.0)
                if response:
                    last_activity = time.time()
                    frame = await decode_packet(response, udp_key_bytes, match_code)
                    if frame:
                        cmd = frame.get("cmd", 0)
                        if cmd in (103, 107):
                            emit(f"[\u2605] Match #{match_index} finished (cmd {cmd})")
                            return "reconnected"
                        ptype = await classify(frame)
                        typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                        if reply:
                            try:
                                await udp_sendto(sock, reply, server_addr)
                                emit(f"[MATCH #{match_index}] [RECONNECT] {ptype or 'REPLY'} reply sent -> {server_addr}")
                            except Exception:
                                pass
            except asyncio.TimeoutError:
                if (time.time() - last_activity) > max_idle:
                    emit(f"[MATCH #{match_index}] [RECONNECT] No activity for {max_idle}s - match finished.")
                    return "reconnected"
            except BlockingIOError:
                await asyncio.sleep(0.05)
            except OSError:
                await asyncio.sleep(0.5)
            except Exception:
                await asyncio.sleep(0.5)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        emit(f"[MATCH #{match_index}] [RECONNECT] error: {e}")
        return "no-match"
    finally:
        ping_stop.set()
        if ping_task:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
        if sock:
            try:
                sock.close()
            except Exception:
                pass


async def functional_br(addrs, starter_packet, account_region, client_version,
                        key, iv, account_id="", account_data=None,
                        max_reconnects=10, status_cb=None, log_cb=None,
                        stop_event=None, single_match=False, match_cb=None):
    reconnects = 0
    ip, port = addrs.split(":")
    play_matches: List[asyncio.Task] = []
    no_response_count = 0
    search_attempts = 0
    last_start_time = 0.0
    uid_str = str(account_id)

    current_token = starter_packet
    current_key = key
    current_iv = iv
    current_account_data = account_data

    def emit(msg):
        try:
            if log_cb:
                log_cb(msg)
            else:
                log(msg)
        except Exception:
            pass

    async def set_status(st):
        try:
            if status_cb:
                res = status_cb(st)
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            pass

    async def notify_match(event, info):
        try:
            if match_cb:
                res = match_cb(event, info)
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            pass

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            writer = None
            gateway_ping_task = None
            try:
                resolved_ip = await resolve_host_cloudflare(ip)
                reader, writer = await asyncio.open_connection(resolved_ip, int(port))

                raw_sock = writer.get_extra_info('socket')
                if raw_sock:
                    optimize_tcp_socket(raw_sock)

                writer.write(bytes.fromhex(current_token))
                await writer.drain()

                try:
                    init_ka = await send_keep_alive(account_region)
                    if init_ka and writer and not writer.is_closing():
                        writer.write(init_ka)
                        await asyncio.wait_for(writer.drain(), timeout=3)
                except Exception:
                    pass

                async def func_gateway_keepalive():
                    ka_bytes = await send_keep_alive(account_region)
                    while True:
                        await asyncio.sleep(5)
                        try:
                            if writer and not writer.is_closing():
                                writer.write(ka_bytes)
                                await writer.drain()
                        except Exception:
                            break

                gateway_ping_task = asyncio.create_task(func_gateway_keepalive())

                emit(f"[+] TCP gateway connected | UID: {uid_str}")
                await set_status("matchmaking")
                reconnects = 0
                no_response_count = 0
                last_start_time = 0.0

                async def send_start_match():
                    nonlocal search_attempts, last_start_time
                    search_attempts += 1
                    current_region = account_region
                    try:
                        await asyncio.sleep(random.uniform(0.2, 0.4))
                        emit(f"[BR] UID {uid_str} -> BR match #{search_attempts}")
                        await start_game_battle_royale(
                            current_region, client_version, writer,
                            current_key, current_iv
                        )
                        await set_status("matchmaking")
                    except Exception as e:
                        emit(f"[!] StartMatch notice: {e}")
                    last_start_time = asyncio.get_running_loop().time()

                await send_start_match()

                while True:
                    if stop_event is not None and stop_event.is_set():
                        raise asyncio.CancelledError()

                    play_matches[:] = [m for m in play_matches if not m.done()]

                    now = asyncio.get_running_loop().time()
                    if len(play_matches) == 0 and (now - last_start_time >= START_MATCH_INTERVAL):
                        await send_start_match()

                    try:
                        data = await asyncio.wait_for(reader.read(65535), timeout=0.5)
                    except asyncio.TimeoutError:
                        no_response_count += 1
                        if no_response_count > 60:
                            no_response_count = 0
                        continue

                    if not data:
                        raise ConnectionError("Connection closed by server")

                    hex_data = data.hex()
                    packet_length = len(data)
                    no_response_count = 0

                    emit(f"[DEBUG] UID {uid_str} recv len={packet_length} head={hex_data[:40]}")

                    if packet_length < 10:
                        continue

                    is_match_packet = False
                    payload_hex = None

                    if hex_data.startswith("0300"):
                        if 10 < packet_length < 30:
                            emit(f"[*] Queue confirmed (BR) | UID: {uid_str}")
                            continue
                        elif packet_length >= 300:
                            is_match_packet = True
                            payload_hex = hex_data[10:]
                        else:
                            continue
                    elif packet_length >= 200:
                        is_match_packet = True
                        payload_hex = hex_data

                    if not is_match_packet:
                        continue

                    emit(f"[+] Match found [BR] | UID: {uid_str} | size={packet_length}")

                    try:
                        res = json.loads(await decode_protobuf(payload_hex))
                        token = None
                        udp_key = None
                        match_code = None
                        server_ip_port = None
                        match_account_id = None
                        block_val = None

                        if '42' in res and 'data' in res['42']:
                            match_code = res['42']['data']
                        if '5' in res and 'data' in res['5']:
                            res_field5 = res['5']['data']
                            server_ip_port = res_field5.get('2', {}).get('data')
                            udp_key = res_field5.get('3', {}).get('data')
                            token = res_field5.get('4', {}).get('data')
                            if '42' in res_field5:
                                match_code = res_field5['42']['data']
                        if '1' in res and 'data' in res['1']:
                            match_account_id = res['1']['data']
                        if '5' in res and 'data' in res['5']:
                            block_val = res['5']['data'].get('1', {}).get('data')

                        effective_acc_id = match_account_id or account_id or "ME_BOT"

                        emit(f"[DEBUG] UID {uid_str} parsed: token={'YES' if token else 'NO'} udp_key={'YES' if udp_key else 'NO'} match_code={'YES' if match_code else 'NO'} server={server_ip_port}")

                        if token and udp_key and match_code and server_ip_port:
                            acc_tok = current_account_data.get('access_token', '') if current_account_data else ""

                            thunder, sharma = await build_match_startup_packets(
                                token, udp_key, match_code, effective_acc_id, block_val or 0,
                                server_ip=server_ip_port,
                                region=account_region,
                                client_version=client_version,
                                access_token=acc_tok,
                                mode_id=MODE_ID,
                                map_id=MAP_ID
                            )

                            match_index = len(play_matches) + 1
                            emit(f"[+] Match #{match_index} [BR] injected -> {server_ip_port}")

                            match_info = {
                                "server_ip_port": server_ip_port,
                                "udp_key": udp_key,
                                "match_code": match_code,
                                "account_id": effective_acc_id,
                                "region": account_region,
                                "client_version": client_version,
                                "match_index": match_index,
                                "started_at": time.time(),
                            }
                            await notify_match("start", match_info)

                            new_match = asyncio.create_task(
                                play_game(
                                    server_ip_port,
                                    thunder,
                                    sharma,
                                    udp_key,
                                    match_code,
                                    effective_acc_id,
                                    account_region,
                                    client_version,
                                    current_key,
                                    current_iv,
                                    match_index=match_index,
                                    status_cb=status_cb,
                                    log_cb=log_cb
                                )
                            )
                            play_matches.append(new_match)

                            async def drain_gateway_reader():
                                while not new_match.done():
                                    try:
                                        data_gw = await asyncio.wait_for(reader.read(4096), timeout=1.0)
                                        if not data_gw:
                                            break
                                    except asyncio.TimeoutError:
                                        continue
                                    except Exception:
                                        break

                            drain_task = asyncio.create_task(drain_gateway_reader())

                            try:
                                await new_match
                            except Exception as e:
                                emit(f"[MATCH #{match_index}] error: {e}")
                            finally:
                                drain_task.cancel()
                                try:
                                    await drain_task
                                except asyncio.CancelledError:
                                    pass

                            await notify_match("end", match_info)

                            play_matches[:] = [m for m in play_matches if not m.done()]

                            try:
                                await refresh_account_profile(current_account_data)
                            except Exception:
                                pass

                            new_level = 1
                            if current_account_data:
                                new_level = max(1, int(current_account_data.get("level", 1) or 1))
                            emit(f"[*] UID {uid_str} | Lvl {new_level} | Next: BR")

                            if gateway_ping_task:
                                gateway_ping_task.cancel()
                            await safe_close_writer(writer)
                            writer = None

                            if single_match:
                                return f"match #{match_index} finished"

                            await set_status("matchmaking")
                            break

                        else:
                            emit(f"[DEBUG] UID {uid_str} not a valid match packet \u2014 ignoring")
                            continue

                    except Exception as e:
                        emit(f"[!] Match packet notice: {e}")
                        continue

            except asyncio.CancelledError:
                if gateway_ping_task:
                    gateway_ping_task.cancel()
                raise
            except Exception as e:
                if gateway_ping_task:
                    gateway_ping_task.cancel()
                play_matches[:] = [m for m in play_matches if not m.done()]
                await safe_close_writer(writer)

                if "Cache expired" in str(e):
                    break

                reconnects += 1
                if reconnects > max_reconnects:
                    reconnects = 0
                    break

                await asyncio.sleep(min(reconnects * 0.5, 2.0))
            finally:
                if gateway_ping_task:
                    gateway_ping_task.cancel()
                if writer:
                    await safe_close_writer(writer)

    except asyncio.CancelledError:
        raise
    finally:
        for m in play_matches:
            if not m.done():
                m.cancel()


async def informational(addrs, starter_packet, key, iv, region=None, account_id="", max_reconnects=3):
    if region is None:
        region = ACCOUNT_REGION
    uid_str = str(account_id)
    reconnects = 0
    ip, port = addrs.split(":")
    while True:
        writer = None
        ping_task = None
        try:
            resolved_ip = await resolve_host_cloudflare(ip)
            reader, writer = await asyncio.open_connection(resolved_ip, int(port))

            raw_sock = writer.get_extra_info('socket')
            if raw_sock:
                optimize_tcp_socket(raw_sock)

            writer.write(bytes.fromhex(starter_packet))
            await writer.drain()
            reconnects = 0

            try:
                init_ka = await send_keep_alive(region)
                if init_ka and writer and not writer.is_closing():
                    writer.write(init_ka)
                    await asyncio.wait_for(writer.drain(), timeout=3)
            except Exception:
                pass

            async def info_keepalive():
                ka_bytes = await send_keep_alive(region)
                while True:
                    await asyncio.sleep(5)
                    try:
                        if writer and not writer.is_closing():
                            writer.write(ka_bytes)
                            await writer.drain()
                    except Exception:
                        break

            ping_task = asyncio.create_task(info_keepalive())

            while True:
                try:
                    data = await asyncio.wait_for(reader.read(8192), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if not data:
                    raise ConnectionError("Connection closed")
        except asyncio.CancelledError:
            if ping_task:
                ping_task.cancel()
            await safe_close_writer(writer)
            raise
        except Exception:
            if ping_task:
                ping_task.cancel()
            await safe_close_writer(writer)
            reconnects += 1
            if reconnects > max_reconnects:
                await asyncio.sleep(3)
                reconnects = 0
            else:
                await asyncio.sleep(1)


async def refresh_account_profile(account_data: Dict):
    try:
        if not account_data:
            return
        url = account_data.get('server_url')
        token = account_data.get('token')
        release_version = account_data.get('release_version')
        payload = account_data.get('login_payload_data')
        if not (url and token and release_version and payload):
            return
        res = await send_getlogin(payload, url, token, release_version)
        if not res:
            return
        res_proto, dict_res = res
        level = int(get_proto_field(dict_res, 6, 1))
        exp = int(get_proto_field(dict_res, 7, 0))
        nickname = res_proto.nickname or get_proto_field(dict_res, 4, "")
        if level <= 0:
            level = 1
        if exp < 0:
            exp = 0
        account_data['level'] = level
        account_data['exp'] = exp
        if nickname:
            account_data['nickname'] = nickname
    except Exception:
        pass


async def process_account_uid_pass(uid: str, password: str, region: Optional[str] = None) -> Optional[Dict]:
    log(f"[*] Logging in UID: {uid}")
    try:
        async with _LOGIN_SEMAPHORE:
            verconfig_res = await version_config(region)
            if verconfig_res is None:
                return None
            release_version, client_version, server_url = verconfig_res

            tokengrant_response = await get_access_token(uid, password)
            if tokengrant_response is None:
                log(f"[-] OAuth failed for UID {uid}")
                return None
            open_id, access_token, platform = tokengrant_response

            device_info = get_device_for_account(uid)
            login_payload_data = await build_majorlogin_payload(open_id, access_token, platform, client_version, device_info)
            if login_payload_data is None:
                return None

            majorlogin_response = await send_majorlogin(login_payload_data, release_version, server_url)
            if majorlogin_response is None:
                log(f"[-] MajorLogin failed for {uid}")
                return None

            getlogin_result = await send_getlogin(
                login_payload_data,
                majorlogin_response.url,
                majorlogin_response.token,
                release_version
            )
            if getlogin_result is None:
                log(f"[-] GetLoginData failed for {uid}")
                return None

            res_proto, dict_res = getlogin_result

        acc_id = str(majorlogin_response.account_id)
        if acc_id == "0" or not acc_id or acc_id == "None":
            log(f"[-] Invalid account_id (0) for UID {uid} — skipping this account")
            return None

        level = int(get_proto_field(dict_res, 6, 1))
        exp = int(get_proto_field(dict_res, 7, 0))
        nickname = res_proto.nickname or get_proto_field(dict_res, 4, f"Player_{acc_id}")
        fallback_region = (str(region).upper() if region else ACCOUNT_REGION)
        region = majorlogin_response.region or get_proto_field(dict_res, 3, fallback_region)
        region = str(region).upper() if region else fallback_region

        if level <= 0:
            level = 1
        if exp < 0:
            exp = 0

        func_addr = res_proto.functional_addrs
        info_addr = res_proto.informational_addrs
        if not isinstance(func_addr, str) or not func_addr or ":" not in func_addr:
            func_addr = None
        if not isinstance(info_addr, str) or not info_addr or ":" not in info_addr:
            info_addr = None

        key_val = majorlogin_response.aes_ak
        iv_val = majorlogin_response.iv_i
        if not isinstance(key_val, (bytes, bytearray)) or len(key_val) < 16:
            key_val = AES_KEY
        if not isinstance(iv_val, (bytes, bytearray)) or len(iv_val) < 16:
            iv_val = AES_IV

        log(f"[+] Login OK | UID {acc_id} | {nickname} | Lvl {level} | Mode: BR")

        account_data = {
            'account_id': majorlogin_response.account_id,
            'nickname': nickname,
            'region': region,
            'level': level,
            'exp': exp,
            'open_id': open_id,
            'access_token': access_token,
            'platform': str(platform),
            'token': majorlogin_response.token,
            'server_time': majorlogin_response.server_time,
            'aes_ak': key_val,
            'iv_i': iv_val,
            'functional_addrs': func_addr,
            'informational_addrs': info_addr,
            'release_version': release_version,
            'client_version': client_version,
            'server_url': majorlogin_response.url,
            'login_payload_data': login_payload_data,
            'auth_type': 'guest',
            'auth_uid': uid,
            'auth_password': password
        }
        return account_data
    except Exception as e:
        log(f"[-] process_account_uid_pass error: {e}")
        return None
