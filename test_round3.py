# -*- coding: utf-8 -*-
"""Round 3 tests:
  * reconnect_match() against a mock UDP match server (real sockets)
  * manager stats: exp_gain / total_matches / match_started_at / in_match
  * manager reconnect branch: Start reconnects instead of launching a new match
"""
import asyncio
import os
import socket
import sys
import time

import botcore
import manager

# isolate persistence so tests never touch real data
manager.DATA_DIR = "/tmp/ff_test_data"
manager.DATA_FILE = os.path.join(manager.DATA_DIR, "accounts.json")
try:
    os.remove(manager.DATA_FILE)
except OSError:
    pass


# --------------------------------------------------------------------------- #
# 1) reconnect_match against a real mock UDP server
# --------------------------------------------------------------------------- #
async def mock_match_server(port_holder, hold_seconds=6.0):
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    port_holder.append(sock.getsockname()[1])

    async def run():
        replied = False
        end = time.time() + hold_seconds
        try:
            while time.time() < end:
                try:
                    data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 4096), timeout=0.5)
                    if data and not replied:
                        # any non-empty reply marks the match as alive
                        await loop.sock_sendto(sock, b"\x00\x00\x00\x00\x00\x00\x00\x00", addr)
                        replied = True
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
        finally:
            sock.close()

    return asyncio.create_task(run())


async def test_reconnect_match_positive():
    ports = []
    srv = await mock_match_server(ports, hold_seconds=6.0)
    await asyncio.sleep(0.1)
    port = ports[0]

    info = {
        "server_ip_port": f"127.0.0.1:{port}",
        "udp_key": "00112233445566778899aabbccddeeff",
        "match_code": "0300",
        "account_id": "123456789",
        "match_index": 7,
        "started_at": time.time(),
    }
    logs = []
    res = await botcore.reconnect_match(info, log_cb=logs.append, max_idle=1.2)
    srv.cancel()
    try:
        await srv
    except asyncio.CancelledError:
        pass
    assert res == "reconnected", res
    assert any("RECONNECT" in l for l in logs), logs
    assert any("Reconnected" in l for l in logs), logs
    print("[1] reconnect_match positive ->", res, "| logs:", len(logs))


async def test_reconnect_match_missing_info():
    res = await botcore.reconnect_match({"server_ip_port": "", "udp_key": "", "match_code": ""})
    assert res == "no-match", res
    print("[2] reconnect_match missing info ->", res)


# --------------------------------------------------------------------------- #
# 2) manager stats + reconnect branch
# --------------------------------------------------------------------------- #
FAKE = {
    "account_id": 987654321,
    "nickname": "TestPlayer",
    "region": "IND",
    "level": 42,
    "exp": 12345,
    "open_id": "x",
    "access_token": "tok",
    "platform": "4",
    "token": "jwt",
    "server_time": 123456,
    "aes_ak": b"0123456789abcdef",
    "iv_i": b"abcdef0123456789",
    "functional_addrs": "1.2.3.4:9999",
    "informational_addrs": "1.2.3.4:8888",
    "release_version": "1.0",
    "client_version": "1.132.8",
    "server_url": "http://x/",
    "login_payload_data": b"x",
}

CALLS = {"reconnect": 0, "functional": 0}


async def fake_process(uid, password, region=None):
    await asyncio.sleep(0.02)
    d = dict(FAKE)
    d["region"] = (region or "IND").upper()
    return d


async def fake_build_tcp(*a, **k):
    return "abcd"


async def fake_informational(*a, **k):
    await asyncio.sleep(3600)


async def fake_refresh(ad):
    # simulate exp gained while a match was played
    ad["exp"] = int(ad.get("exp", 0)) + 100


async def fake_functional_br(addrs, starter, region, cv, key, iv, account_id="",
                             account_data=None, max_reconnects=10, status_cb=None,
                             log_cb=None, stop_event=None, single_match=False,
                             match_cb=None):
    CALLS["functional"] += 1

    async def st(s):
        if status_cb:
            r = status_cb(s)
            if asyncio.iscoroutine(r):
                await r

    async def mc(ev, info):
        if match_cb:
            r = match_cb(ev, info)
            if asyncio.iscoroutine(r):
                await r

    await st("matchmaking")
    info = {
        "server_ip_port": "1.2.3.4:9999",
        "udp_key": "00112233445566778899aabbccddeeff",
        "match_code": "0300",
        "account_id": str(account_id),
        "match_index": 1,
        "started_at": time.time(),
    }
    await mc("start", info)
    await st("in_game")
    await asyncio.sleep(0.15)
    await mc("end", info)
    await st("matchmaking")
    # one match is enough for the test -> ask the session loop to stop
    if stop_event is not None:
        stop_event.set()
    return "done"


async def fake_reconnect_match(info, status_cb=None, log_cb=None, stop_event=None,
                               max_idle=None):
    CALLS["reconnect"] += 1
    if log_cb:
        log_cb("[~] fake reconnect: reconnected to existing match")
    if status_cb:
        r = status_cb("in_game")
        if asyncio.iscoroutine(r):
            await r
    return "reconnected"


async def test_manager_stats_and_reconnect():
    botcore.process_account_uid_pass = fake_process
    botcore.build_tcp_startup_packet = fake_build_tcp
    botcore.informational = fake_informational
    botcore.refresh_account_profile = fake_refresh
    botcore.functional_br = fake_functional_br
    botcore.reconnect_match = fake_reconnect_match

    mgr = manager.AccountManager()

    # ---- stats after a played match ----
    acc = await mgr.add_account("", "555000111", "pw", "IND")
    assert acc["status"] == manager.ST_READY
    assert acc["exp_gain"] == 0 and acc["total_matches"] == 0

    await mgr.start_match("", "555000111")
    await asyncio.sleep(0.6)
    s = mgr.get("", "555000111")
    pub = s.public()
    print("[3] after match -> total_matches=%s exp_gain=%s in_match=%s match_started_at=%s"
          % (pub["total_matches"], pub["exp_gain"], pub["in_match"], pub["match_started_at"]))
    assert pub["total_matches"] == 1, pub["total_matches"]
    assert pub["exp_gain"] == 100, pub["exp_gain"]
    assert pub["in_match"] is False
    assert pub["match_started_at"] is None

    # ---- reconnect branch: a recent match_info with no running task ----
    s2 = mgr.get("", "555000111")
    s2.task = None
    s2.match_info = {
        "server_ip_port": "1.2.3.4:9999",
        "udp_key": "00112233445566778899aabbccddeeff",
        "match_code": "0300",
        "account_id": "987654321",
        "match_index": 9,
        "started_at": time.time(),
    }
    before = CALLS["reconnect"]
    await mgr.start_match("", "555000111")
    await asyncio.sleep(0.5)
    print("[4] reconnect calls: %s (was %s) | total_matches=%s"
          % (CALLS["reconnect"], before, mgr.get("", "555000111").total_matches))
    assert CALLS["reconnect"] == before + 1, "reconnect_match was not used"
    # reconnected match counted, then a normal session ran one more match
    assert mgr.get("", "555000111").total_matches >= 2

    # ---- start while already in a match -> must NOT launch a new match ----
    s3 = mgr.get("", "555000111")
    s3.in_match = True
    s3.match_info = {"match_index": 5, "started_at": time.time()}
    s3.stop_event = asyncio.Event()
    s3.task = asyncio.create_task(asyncio.sleep(5))
    fcalls = CALLS["functional"]
    rbefore = CALLS["reconnect"]
    await mgr.start_match("", "555000111")
    await asyncio.sleep(0.1)
    assert CALLS["functional"] == fcalls, "started a new match while already in one!"
    assert CALLS["reconnect"] == rbefore, "reconnected while already running!"
    s3.task.cancel()
    try:
        await s3.task
    except asyncio.CancelledError:
        pass
    print("[5] start while in-match -> no new match, no reconnect (correct)")

    print("\nALL ROUND-3 TESTS PASSED")


async def main():
    await test_reconnect_match_positive()
    await test_reconnect_match_missing_info()
    await test_manager_stats_and_reconnect()


if __name__ == "__main__":
    asyncio.run(main())
