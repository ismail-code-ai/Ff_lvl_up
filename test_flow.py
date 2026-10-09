# -*- coding: utf-8 -*-
"""Offline test of the manager wiring using mocked botcore network calls."""
import asyncio
import botcore
import manager


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


async def fake_process(uid, password, region=None):
    await asyncio.sleep(0.05)
    d = dict(FAKE)
    d["region"] = (region or "IND").upper()
    return d


async def fake_build_tcp(*a, **k):
    return "abcd"


async def fake_informational(*a, **k):
    await asyncio.sleep(3600)


async def fake_refresh(ad):
    return None


async def fake_functional_br(addrs, starter, region, cv, key, iv, account_id="",
                             account_data=None, max_reconnects=10, status_cb=None,
                             log_cb=None, stop_event=None, single_match=False,
                             match_cb=None):
    async def st(s):
        if status_cb:
            r = status_cb(s)
            if asyncio.iscoroutine(r):
                await r
    if log_cb:
        log_cb("[+] fake gateway connected")
    await st("matchmaking")
    await asyncio.sleep(0.2)
    await st("in_game")
    await asyncio.sleep(0.4)
    await st("matchmaking")
    return "done"


botcore.process_account_uid_pass = fake_process
botcore.build_tcp_startup_packet = fake_build_tcp
botcore.informational = fake_informational
botcore.refresh_account_profile = fake_refresh
botcore.functional_br = fake_functional_br


async def main():
    mgr = manager.AccountManager()
    seen = []

    async def listener(msg):
        if msg.get("type") == "status":
            seen.append((msg["uid"], msg["status"]))

    mgr.add_listener(listener)

    acc = await mgr.add_account("", "555000111", "pw", "IND")
    print("after add  ->", acc["name"], acc["account_id"], "lvl", acc["level"],
          "exp", acc["exp"], "status", acc["status"])
    assert acc["status"] == manager.ST_READY
    assert acc["name"] == "TestPlayer" and acc["level"] == 42 and acc["exp"] == 12345

    await mgr.start_match("", "555000111")
    await asyncio.sleep(0.5)
    s = mgr.get("", "555000111").status
    print("during match ->", s)
    assert s == manager.ST_IN_GAME, s

    await mgr.stop_match("", "555000111")
    print("after stop ->", mgr.get("", "555000111").status)
    assert mgr.get("", "555000111").status == manager.ST_READY

    print("status events:", seen)
    assert ("555000111", "matchmaking") in seen
    assert ("555000111", "in_game") in seen
    print("\nALL FLOW TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
