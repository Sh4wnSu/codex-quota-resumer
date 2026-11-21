#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import base64, json, os, socket, struct, threading, time, queue

class WsUnix:
    def __init__(self, path):
        self.path=path; self.sock=None; self.buf=b""
    def connect(self, timeout=10):
        s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(timeout)
        s.connect(self.path)
        key=base64.b64encode(os.urandom(16)).decode()
        req=(f"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode()
        s.sendall(req)
        hdr=b""
        while b"\r\n\r\n" not in hdr:
            chunk=s.recv(1)
            if not chunk: raise RuntimeError("closed during handshake")
            hdr+=chunk
        if b"101" not in hdr.split(b"\r\n")[0]:
            raise RuntimeError("handshake failed: %s" % hdr[:200])
        self.sock=s; self.buf=b""
        return True
    def send_text(self, text):
        payload=text.encode()
        header=bytearray([0x81])
        n=len(payload)
        if n<126: header.append(0x80|n)
        elif n<65536: header.append(0x80|126); header+=struct.pack(">H",n)
        else: header.append(0x80|127); header+=struct.pack(">Q",n)
        mask=os.urandom(4); header+=mask
        masked=bytes(b ^ mask[i%4] for i,b in enumerate(payload))
        self.sock.sendall(bytes(header)+masked)
    def _read(self, n):
        while len(self.buf)<n:
            chunk=self.sock.recv(65536)
            if not chunk: raise EOFError("socket closed")
            self.buf+=chunk
        out,self.buf=self.buf[:n],self.buf[n:]
        return out
    def recv_text(self, timeout=30):
        self.sock.settimeout(timeout)
        fragments=b""; opcode=None
        while True:
            b1,b2=self._read(2)
            fin=b1>>7; op=b1&0x0f; masked=b2>>7; ln=b2&0x7f
            if ln==126: ln=struct.unpack(">H",self._read(2))[0]
            elif ln==127: ln=struct.unpack(">Q",self._read(8))[0]
            mask=self._read(4) if masked else None
            data=self._read(ln)
            if mask: data=bytes(b ^ mask[i%4] for i,b in enumerate(data))
            if op==0x9:  # ping
                self._pong(data); continue
            if op==0xA: continue
            if op==0x8: raise EOFError("server closed")
            opcode=op if opcode is None else opcode
            fragments+=data
            if fin: return fragments.decode("utf-8","replace")

sock=os.path.expanduser("~/.codex/app-server-control/app-server-control.sock")
c=WsUnix(sock); c.connect()
print("WS 已连接")
def call(method, params, rid, timeout=30):
    c.send_text(json.dumps({"jsonrpc":"2.0","id":rid,"method":method,"params":params}))
    t0=time.time()
    while time.time()-t0<timeout:
        try: msg=c.recv_text(timeout=timeout)
        except socket.timeout: return None
        try: o=json.loads(msg)
        except Exception: print("  非JSON帧:", msg[:200]); continue
        if o.get("id")==rid: return o
        print("  [通知]", o.get("method"), json.dumps(o.get("params") or {},ensure_ascii=False)[:160])
    return None
c.send_text(json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"ws-probe","version":"1"}}}))
t0=time.time(); ok=False
while time.time()-t0<20:
    try: msg=c.recv_text(timeout=5)
    except socket.timeout: continue
    o=json.loads(msg)
    if o.get("id")==1: print("initialize ->", json.dumps(o,ensure_ascii=False)[:300]); ok=True; break
    print("  [通知]", o.get("method"))
if ok:
    c.send_text(json.dumps({"jsonrpc":"2.0","method":"initialized","params":{}}))
    r=call("thread/loaded/list", {}, 2, timeout=20)
    print("loaded/list ->", json.dumps(r,ensure_ascii=False)[:500] if r else "超时")
    r=call("account/rateLimits/read", {}, 3, timeout=25)
    if r and "result" in r:
        res=r["result"]; snap=res.get("rateLimits") or {}
        print("rateLimits -> allowed=%s primary=%s secondary=%s" % (res.get("ordinaryUsageAllowed"), snap.get("primary"), snap.get("secondary")))
    else:
        print("rateLimits -> 无响应/错误:", json.dumps(r,ensure_ascii=False)[:300] if r else "超时")
