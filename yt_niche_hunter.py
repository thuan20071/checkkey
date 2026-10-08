#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================
 YT NICHE HUNTER v2.0 PRO - Web phan tich YouTube & tim ngach
 Ban full chuc nang: tim kenh theo tu khoa (bo loc nhu tool pro),
 phan tich outlier/VPH, kenh tuong tu, thu vien, lich su, xuat bao cao.
 1 file duy nhat - chay bang chay_web.bat
================================================================
"""
import sys, subprocess, os

def _bootstrap():
    need = []
    for mod, pkg in [("flask", "flask"), ("requests", "requests"), ("openpyxl", "openpyxl")]:
        try:
            __import__(mod)
        except ImportError:
            need.append(pkg)
    if need:
        print("[bootstrap] Dang cai dat thu vien:", ", ".join(need))
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet"] + need)
        except Exception:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages"] + need)
        os.execv(sys.executable, [sys.executable] + sys.argv)

_bootstrap()

import re, json, math, time, sqlite3, threading, io, csv
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify, Response, send_file

# ---------------- Cau hinh ----------------
APP_NAME = "YT NICHE HUNTER"
VERSION = "2.1"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "app.db")
YT_API = "https://www.googleapis.com/youtube/v3"

REGIONS = [
    ("US", "US - United States"), ("VN", "VN - Viet Nam"), ("JP", "JP - Japan"),
    ("KR", "KR - Korea"), ("GB", "UK - United Kingdom"), ("DE", "DE - Germany"),
    ("FR", "FR - France"), ("BR", "BR - Brazil"), ("MX", "MX - Mexico"),
    ("IN", "IN - India"), ("ID", "ID - Indonesia"), ("TH", "TH - Thailand"),
    ("TW", "TW - Taiwan"), ("CA", "CA - Canada"), ("AU", "AU - Australia"),
]

PUBLISHED_WINDOWS = {
    "today": ("Hôm nay", 1), "week": ("Tuần này", 7), "month": ("Tháng này", 30),
    "3months": ("3 tháng", 90), "year": ("Năm nay", 365), "all": ("Tất cả", None),
}

_db_lock = threading.Lock()

def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with _db_lock, db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS settings(
            k TEXT PRIMARY KEY, v TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS library(
            channel_id TEXT PRIMARY KEY, title TEXT, subs INTEGER, videos INTEGER,
            views INTEGER, avg_views REAL, shorts_pct REAL, freq REAL,
            country TEXT, thumb TEXT, added_at TEXT, note TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, keyword TEXT,
            region TEXT, pubwin TEXT, result_count INTEGER, created_at TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS quota_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, endpoint TEXT,
            cost INTEGER, created_at TEXT)""")
        con.commit()

# Khoi tao DB ngay khi import module (can thiet khi chay bang gunicorn
# tren Render/PythonAnywhere, luc do __main__ khong chay)
try:
    init_db()
except Exception as _e:
    print("[init_db] loi khoi tao DB:", _e)

def get_setting(k, default=""):
    with _db_lock, db() as con:
        r = con.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

def set_setting(k, v):
    with _db_lock, db() as con:
        con.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (k, v))
        con.commit()

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def log_quota(endpoint, cost):
    try:
        with _db_lock, db() as con:
            con.execute("INSERT INTO quota_log(day,endpoint,cost,created_at) VALUES(?,?,?,?)",
                        (today_str(), endpoint, cost, datetime.now().isoformat(timespec="seconds")))
            con.commit()
    except Exception:
        pass

def quota_today():
    with _db_lock, db() as con:
        r = con.execute("SELECT COALESCE(SUM(cost),0) s FROM quota_log WHERE day=?",
                        (today_str(),)).fetchone()
        return r["s"] or 0

# ---------------- YouTube API helpers ----------------
class YTError(Exception):
    pass

def yt_key():
    # Uu tien bien moi truong YT_API_KEY (tien cho deploy cloud),
    # sau do moi den key luu trong DB (trang Cai dat)
    env_key = os.environ.get("YT_API_KEY", "").strip()
    if env_key:
        return env_key
    return get_setting("yt_api_key", "").strip()

def yt_get(path, params, cost=1):
    key = yt_key()
    if not key:
        raise YTError("Chua cau hinh YouTube API key. Vao trang Cai dat de nhap key.")
    params = dict(params or {})
    params["key"] = key
    log_quota(path, cost)
    try:
        r = requests.get(YT_API + path, params=params, timeout=25)
    except requests.RequestException as e:
        raise YTError("Loi mang khi goi YouTube API: %s" % e)
    try:
        data = r.json()
    except Exception:
        raise YTError("YouTube API tra ve du lieu la (HTTP %s)" % r.status_code)
    if "error" in data:
        msg = data["error"].get("message", "Unknown error")
        raise YTError("YouTube API loi: %s" % msg)
    return data

DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")

def parse_duration(s):
    m = DUR_RE.match(s or "")
    if not m:
        return 0
    h, mi, se = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + int(se or 0)

def parse_time(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def fmt_int(n):
    try:
        n = int(n)
    except Exception:
        return "0"
    if n >= 1_000_000_000:
        return "%.1fB" % (n / 1_000_000_000)
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fK" % (n / 1_000)
    return str(n)

def demo_channels(keyword, n=8):
    import random
    random.seed(abs(hash(keyword)) % 99999)
    topics = ["Review", "Gaming", "Vlog", "Khoa hoc", "Tai chinh", "Am nhac"]
    out = []
    for i in range(n):
        cid = "UCdemo%03dxxxxxxxxxxxxxxxx" % i
        subs = random.randint(5000, 2000000)
        vids = random.randint(20, 900)
        views = subs * random.randint(8, 60)
        out.append({
            "channel_id": cid, "title": "%s %s #%d (demo)" % (keyword, random.choice(topics), i + 1),
            "subs": subs, "videos": vids, "views": views, "country": random.choice(["VN", "US", "JP", "KR"]),
            "thumb": "", "url": "#", "avg_views": views // max(vids, 1),
            "avg10": int(views // max(vids, 1) * random.uniform(0.7, 2.5)),
            "shorts_pct": round(random.uniform(0, 100), 1),
            "last_upload": (datetime.now(timezone.utc) - timedelta(days=random.randint(0, 40))).isoformat(),
            "freq": round(random.uniform(0.2, 9.5), 1),
            "view_sub": round(views / max(subs, 1), 1),
            "demo": True,
        })
    return out

def search_channel_ids(q, region, max_results):
    """Buoc 1: search.list type=channel -> danh sach channelId (ton 100 quota)."""
    params = {"part": "snippet", "q": q, "type": "channel",
              "maxResults": min(max_results, 50), "order": "relevance"}
    if region:
        params["regionCode"] = region
    data = yt_get("/search", params, cost=100)
    ids = []
    for it in data.get("items", []):
        cid = (it.get("id") or {}).get("channelId")
        if cid and cid not in ids:
            ids.append(cid)
    return ids

def channels_detail(ids):
    """Buoc 2: channels.list -> thong tin kenh (1 quota / 50 kenh)."""
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        data = yt_get("/channels", {"part": "snippet,statistics,contentDetails,topicDetails",
                                    "id": ",".join(chunk)}, cost=1)
        out.extend(data.get("items", []))
    return out

def uploads_playlist_items(playlist_id, n):
    items, page, got = [], None, 0
    while got < n:
        params = {"part": "contentDetails", "playlistId": playlist_id,
                  "maxResults": min(50, n - got)}
        if page:
            params["pageToken"] = page
        data = yt_get("/playlistItems", params, cost=1)
        for it in data.get("items", []):
            vid = ((it.get("contentDetails") or {}).get("videoId"))
            if vid:
                items.append(vid)
                got += 1
                if got >= n:
                    break
        page = data.get("nextPageToken")
        if not page:
            break
    return items

def videos_detail(ids):
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        data = yt_get("/videos", {"part": "snippet,statistics,contentDetails",
                                  "id": ",".join(chunk)}, cost=1)
        out.extend(data.get("items", []))
    return out

def enrich_channel(ch, n_videos=20):
    """Lam giau 1 kenh: video moi nhat, TB views, % shorts, tan suat..."""
    stats = ch.get("statistics") or {}
    snippet = ch.get("snippet") or {}
    content = ch.get("contentDetails") or {}
    cid = ch.get("id")
    subs = int(stats.get("subscriberCount") or 0)
    total_views = int(stats.get("viewCount") or 0)
    video_count = int(stats.get("videoCount") or 0)
    uploads_pl = (content.get("relatedPlaylists") or {}).get("uploads")

    vids, avg_views, avg10, shorts_pct = [], 0, 0, 0.0
    last_upload, freq = "", 0.0
    if uploads_pl:
        try:
            vids = videos_detail(uploads_playlist_items(uploads_pl, n_videos))
        except YTError:
            vids = []
    if vids:
        views = [int((v.get("statistics") or {}).get("viewCount") or 0) for v in vids]
        durs = [parse_duration((v.get("contentDetails") or {}).get("duration")) for v in vids]
        pubs = [parse_time((v.get("snippet") or {}).get("publishedAt")) for v in vids]
        pubs = [p for p in pubs if p]
        if views:
            avg_views = sum(views) / len(views)
            avg10 = sum(sorted(views, reverse=True)[:10]) / min(len(views), 10)
        shorts = sum(1 for d in durs if 0 < d <= 61)
        shorts_pct = round(100.0 * shorts / len(durs), 1) if durs else 0.0
        if pubs:
            last = max(pubs)
            last_upload = last.isoformat()
            days = (datetime.now(timezone.utc) - min(pubs)).days or 1
            freq = round(len(pubs) / max(days / 30.0, 0.2), 1)
    return {
        "channel_id": cid,
        "title": snippet.get("title") or cid,
        "subs": subs, "videos": video_count, "views": total_views,
        "country": (snippet.get("country") or "").upper(),
        "thumb": ((snippet.get("thumbnails") or {}).get("default") or {}).get("url") or "",
        "url": "https://www.youtube.com/channel/" + cid,
        "topics": (ch.get("topicDetails") or {}).get("topicCategories") or [],
        "avg_views": int(avg_views), "avg10": int(avg10),
        "shorts_pct": shorts_pct, "last_upload": last_upload, "freq": freq,
        "view_sub": round(total_views / max(subs, 1), 1),
    }

# ---------------- Phan tich kenh (Outlier / VPH) ----------------
def analyze_channel(cid, n_videos=25):
    chs = channels_detail([cid])
    if not chs:
        raise YTError("Khong tim thay kenh.")
    ch = chs[0]
    stats = ch.get("statistics") or {}
    snippet = ch.get("snippet") or {}
    uploads_pl = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    vids = videos_detail(uploads_playlist_items(uploads_pl, n_videos)) if uploads_pl else []
    videos = []
    for v in vids:
        st = v.get("statistics") or {}
        sn = v.get("snippet") or {}
        cd = v.get("contentDetails") or {}
        views = int(st.get("viewCount") or 0)
        pub = parse_time(sn.get("publishedAt"))
        hours = max((datetime.now(timezone.utc) - pub).total_seconds() / 3600, 0.5) if pub else 0.5
        videos.append({
            "video_id": v.get("id"),
            "title": sn.get("title") or "",
            "views": views,
            "likes": int(st.get("likeCount") or 0),
            "comments": int(st.get("commentCount") or 0),
            "duration": parse_duration(cd.get("duration")),
            "published": sn.get("publishedAt") or "",
            "vph": round(views / hours, 1),
            "url": "https://youtu.be/" + (v.get("id") or ""),
            "thumb": ((sn.get("thumbnails") or {}).get("medium") or {}).get("url") or "",
        })
    med = sorted([x["views"] for x in videos])[len(videos) // 2] if videos else 1
    for x in videos:
        x["outlier"] = round(x["views"] / max(med, 1), 2)
    videos.sort(key=lambda x: x["views"], reverse=True)
    shorts = sum(1 for x in videos if 0 < x["duration"] <= 61)
    return {
        "channel_id": cid,
        "title": snippet.get("title") or cid,
        "subs": int(stats.get("subscriberCount") or 0),
        "views": int(stats.get("viewCount") or 0),
        "video_count": int(stats.get("videoCount") or 0),
        "thumb": ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url") or "",
        "median_views": med,
        "shorts_pct": round(100.0 * shorts / len(videos), 1) if videos else 0,
        "videos": videos,
    }

def find_similar(cid, n=15):
    chs = channels_detail([cid])
    if not chs:
        raise YTError("Khong tim thay kenh mau.")
    ch = chs[0]
    snippet = ch.get("snippet") or {}
    title = snippet.get("title") or ""
    topics = (ch.get("topicDetails") or {}).get("topicCategories") or []
    kw = re.sub(r"[^\w\s]", " ", title).strip().split()
    kw = " ".join([w for w in kw if len(w) > 2][:3]) or title
    topic_kw = ""
    if topics:
        topic_kw = topics[0].split("/")[-1].replace("_", " ")
    query = (kw + " " + topic_kw).strip()
    ids = [i for i in search_channel_ids(query, get_setting("region", "US"), min(n + 5, 50)) if i != cid][:n]
    detail = channels_detail(ids)
    out = []
    for c in detail:
        e = enrich_channel(c, n_videos=10)
        match = 0
        ct = set((c.get("topicDetails") or {}).get("topicCategories") or [])
        if topics and ct:
            match = len(set(topics) & ct)
        e["match"] = match
        out.append(e)
    out.sort(key=lambda x: (x["match"], x["subs"]), reverse=True)
    return {"source": {"channel_id": cid, "title": title}, "channels": out}

def resolve_channel(q):
    q = q.strip()
    m = re.search(r"(?:youtube\.com/(?:channel/|c/|@)|youtu\.be/)([\w\-.@]+)", q)
    if m:
        token = m.group(1)
        if token.startswith("UC") and len(token) >= 20:
            return token
        handle = token if token.startswith("@") else "@" + token
        data = yt_get("/channels", {"part": "id", "forHandle": handle}, cost=1)
        items = data.get("items", [])
        if items:
            return items[0]["id"]
        data = yt_get("/search", {"part": "snippet", "q": token, "type": "channel", "maxResults": 1}, cost=100)
        items = data.get("items", [])
        if items:
            return items[0]["id"]["channelId"]
        raise YTError("Khong giai ma duoc link kenh.")
    if q.startswith("@"):
        data = yt_get("/channels", {"part": "id", "forHandle": q}, cost=1)
        items = data.get("items", [])
        if items:
            return items[0]["id"]
        raise YTError("Khong tim thay handle " + q)
    if q.startswith("UC") and len(q) >= 20:
        return q
    data = yt_get("/search", {"part": "snippet", "q": q, "type": "channel", "maxResults": 1}, cost=100)
    items = data.get("items", [])
    if items:
        return items[0]["id"]["channelId"]
    raise YTError("Khong tim thay kenh cho: " + q)

# ---------------- Flask app ----------------
app = Flask(__name__)
LAST_RESULTS = {"items": [], "label": ""}

def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code

@app.route("/")
def index():
    return HTML_PAGE

@app.route("/api/status")
def api_status():
    key = yt_key()
    return jsonify({"ok": True, "app": APP_NAME, "version": VERSION,
                    "has_key": bool(key), "demo": not key,
                    "quota_today": quota_today(),
                    "region": get_setting("region", "US")})

@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify({"ok": True, "yt_api_key": "******" if yt_key() else "",
                        "region": get_setting("region", "US"),
                        "enrich_n": get_setting("enrich_n", "20"),
                        "max_results": get_setting("max_results", "50"),
                        "ai_base": get_setting("ai_base", ""),
                        "ai_key": "******" if get_setting("ai_key", "") else ""})
    d = request.get_json(force=True) or {}
    for k in ("region", "enrich_n", "max_results", "ai_base"):
        if k in d:
            set_setting(k, str(d[k]))
    if d.get("yt_api_key") and d["yt_api_key"] != "******":
        set_setting("yt_api_key", d["yt_api_key"].strip())
    if d.get("ai_key") and d["ai_key"] != "******":
        set_setting("ai_key", d["ai_key"].strip())
    return jsonify({"ok": True})

@app.route("/api/test_key", methods=["POST"])
def api_test_key():
    try:
        yt_get("/channels", {"part": "id", "id": "UC_x5XG1OV2P6uZZ5FSM9Ttw"}, cost=1)
        return jsonify({"ok": True, "msg": "Ket noi OK! Quota hom nay: %d" % quota_today()})
    except YTError as e:
        return _err(str(e))

@app.route("/api/search_channels", methods=["POST"])
def api_search_channels():
    d = request.get_json(force=True) or {}
    q = (d.get("q") or "").strip()
    if not q:
        return _err("Nhap tu khoa.")
    region = d.get("region") or get_setting("region", "US") or None
    pubwin = d.get("pubwin") or "all"
    max_results = int(d.get("max_results") or get_setting("max_results", "50") or 50)
    enrich_n = int(d.get("enrich_n") or get_setting("enrich_n", "20") or 20)
    min_subs = int(d.get("min_subs") or 0)
    max_subs = int(d.get("max_subs") or 0)
    min_avg = int(d.get("min_avg") or 0)
    sort_by = d.get("sort") or "subs"
    logs = []

    def log(m):
        logs.append("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), m))

    try:
        if not yt_key():
            items = demo_channels(q, min(max_results, 10))
            log("Che do DEMO (chua co API key) - tra ve %d kenh mau." % len(items))
            LAST_RESULTS.update(items=items, label=q)
            with _db_lock, db() as con:
                con.execute("INSERT INTO history(kind,keyword,region,pubwin,result_count,created_at) VALUES(?,?,?,?,?,?)",
                            ("search", q, region or "", pubwin, len(items), datetime.now().isoformat(timespec="seconds")))
                con.commit()
            return jsonify({"ok": True, "demo": True, "items": items, "logs": logs})
        log("YouTube API v3: dang tim kenh cho tu khoa \"%s\" (khu vuc %s)..." % (q, region or "all"))
        ids = search_channel_ids(q, region, max_results)
        log("Tim thay %d kenh so bo. Dang dong bo thong tin chi tiet..." % len(ids))
        detail = channels_detail(ids)
        items = []
        for c in detail:
            try:
                items.append(enrich_channel(c, n_videos=enrich_n))
            except YTError as e:
                log("Bo qua 1 kenh (loi: %s)" % e)
        win_days = PUBLISHED_WINDOWS.get(pubwin, ("", None))[1]
        if win_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=win_days)
            before = len(items)

            def _pub_ok(x):
                p = parse_time(x["last_upload"]) if x["last_upload"] else None
                return p is not None and p >= cutoff
            items = [x for x in items if _pub_ok(x)]
            log("Loc ngay dang: moc '%s' (<= %d ngay) -> con %d/%d kenh." % (pubwin, win_days, len(items), before))
        if min_subs:
            items = [x for x in items if x["subs"] >= min_subs]
        if max_subs:
            items = [x for x in items if x["subs"] <= max_subs]
        if min_avg:
            items = [x for x in items if x["avg_views"] >= min_avg]
        keyfn = {"subs": lambda x: x["subs"], "views": lambda x: x["views"],
                 "avg": lambda x: x["avg_views"], "freq": lambda x: x["freq"],
                 "viewsub": lambda x: x["view_sub"]}.get(sort_by, lambda x: x["subs"])
        items.sort(key=keyfn, reverse=True)
        log("Hoan thanh! Tra ve %d kenh (da sap xep)." % len(items))
        LAST_RESULTS.update(items=items, label=q)
        with _db_lock, db() as con:
            con.execute("INSERT INTO history(kind,keyword,region,pubwin,result_count,created_at) VALUES(?,?,?,?,?,?)",
                        ("search", q, region or "", pubwin, len(items), datetime.now().isoformat(timespec="seconds")))
            con.commit()
        return jsonify({"ok": True, "items": items, "logs": logs, "quota_today": quota_today()})
    except YTError as e:
        log("LOI: %s" % e)
        return jsonify({"ok": False, "error": str(e), "logs": logs}), 400

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    d = request.get_json(force=True) or {}
    q = (d.get("q") or "").strip()
    if not q:
        return _err("Nhap link/ID/@handle kenh.")
    try:
        if not yt_key():
            return _err("Can API key de phan tich that. Vao Cai dat de nhap key.")
        cid = resolve_channel(q)
        data = analyze_channel(cid)
        return jsonify({"ok": True, "data": data, "quota_today": quota_today()})
    except YTError as e:
        return _err(str(e))

@app.route("/api/similar", methods=["POST"])
def api_similar():
    d = request.get_json(force=True) or {}
    q = (d.get("q") or "").strip()
    n = int(d.get("n") or 15)
    if not q:
        return _err("Nhap kenh mau.")
    try:
        if not yt_key():
            items = demo_channels(q, n)
            return jsonify({"ok": True, "demo": True,
                            "source": {"title": q}, "channels": items})
        cid = resolve_channel(q)
        data = find_similar(cid, n)
        with _db_lock, db() as con:
            con.execute("INSERT INTO history(kind,keyword,region,pubwin,result_count,created_at) VALUES(?,?,?,?,?,?)",
                        ("similar", q, "", "", len(data["channels"]), datetime.now().isoformat(timespec="seconds")))
            con.commit()
        return jsonify({"ok": True, "source": data["source"], "channels": data["channels"],
                        "quota_today": quota_today()})
    except YTError as e:
        return _err(str(e))

@app.route("/api/ai_expand", methods=["POST"])
def api_ai_expand():
    d = request.get_json(force=True) or {}
    q = (d.get("q") or "").strip()
    base = get_setting("ai_base", "").strip().rstrip("/")
    key = get_setting("ai_key", "").strip()
    if not q:
        return _err("Nhap tu khoa.")
    if not base or not key:
        return _err("Chua cau hinh AI (vao Cai dat -> muc AI).")
    try:
        r = requests.post(base + "/chat/completions", timeout=40,
                          headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                          json={"model": get_setting("ai_model", "gpt-4o-mini"),
                                "messages": [
                                    {"role": "system", "content": "Ban la chuyen gia nghien cuu YouTube. Tra ve DUY NHAT JSON array 6 tu khoa lien quan, khong giai thich. Vi du: [\"k1\",\"k2\"]"},
                                    {"role": "user", "content": "Tu khoa goc: %s. Cho 6 tu khoa lien quan de tim kenh YouTube (tieng Anh + tieng Viet)." % q}],
                                "temperature": 0.7, "max_tokens": 200})
        data = r.json()
        txt = (data["choices"][0]["message"]["content"] or "").strip()
        m = re.search(r"\[.*\]", txt, re.S)
        kws = json.loads(m.group(0)) if m else []
        kws = [str(x).strip() for x in kws if str(x).strip()][:8]
        return jsonify({"ok": True, "keywords": kws})
    except Exception as e:
        return _err("Loi AI: %s" % e)

@app.route("/api/library", methods=["GET"])
def api_lib_list():
    with _db_lock, db() as con:
        rows = con.execute("SELECT * FROM library ORDER BY subs DESC").fetchall()
    return jsonify({"ok": True, "items": [dict(r) for r in rows]})

@app.route("/api/library", methods=["POST"])
def api_lib_add():
    c = request.get_json(force=True) or {}
    if not c.get("channel_id"):
        return _err("Thieu channel_id")
    with _db_lock, db() as con:
        con.execute("""INSERT OR REPLACE INTO library
            (channel_id,title,subs,videos,views,avg_views,shorts_pct,freq,country,thumb,added_at,note)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (c.get("channel_id"), c.get("title"), int(c.get("subs") or 0), int(c.get("videos") or 0),
             int(c.get("views") or 0), int(c.get("avg_views") or 0), float(c.get("shorts_pct") or 0),
             float(c.get("freq") or 0), c.get("country") or "", c.get("thumb") or "",
             datetime.now().isoformat(timespec="seconds"), c.get("note") or ""))
        con.commit()
    return jsonify({"ok": True})

@app.route("/api/library/<cid>", methods=["DELETE"])
def api_lib_del(cid):
    with _db_lock, db() as con:
        con.execute("DELETE FROM library WHERE channel_id=?", (cid,))
        con.commit()
    return jsonify({"ok": True})

@app.route("/api/history")
def api_history():
    with _db_lock, db() as con:
        rows = con.execute("SELECT * FROM history ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify({"ok": True, "items": [dict(r) for r in rows]})

@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():
    with _db_lock, db() as con:
        con.execute("DELETE FROM history")
        con.commit()
    return jsonify({"ok": True})

@app.route("/api/export")
def api_export():
    fmt = request.args.get("fmt", "xlsx")
    items = LAST_RESULTS.get("items") or []
    if not items:
        return _err("Chua co ket qua de xuat - hay tim kiem truoc.")
    cols = ["title", "channel_id", "subs", "videos", "views", "avg_views", "avg10",
            "shorts_pct", "freq", "view_sub", "country", "last_upload", "url"]
    header = ["Ten kenh", "Channel ID", "Subs", "So video", "Tong views", "TB view/video",
              "TB 10 video", "% Shorts", "Video/thang", "View/Sub", "Quoc gia", "Dang cuoi", "URL"]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        for c in items:
            w.writerow([c.get(k, "") for k in cols])
        return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=kenh_youtube.csv"})
    if fmt == "json":
        return Response(json.dumps(items, ensure_ascii=False, indent=2), mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=kenh_youtube.json"})
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active
    ws.title = "Kenh YouTube"
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="7C3AED")
        cell.alignment = Alignment(horizontal="center")
    for c in items:
        ws.append([c.get(k, "") for k in cols])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18
    ws.column_dimensions["A"].width = 30
    ws.freeze_panes = "A2"
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(bio, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name="kenh_youtube.xlsx")

@app.route("/api/dashboard")
def api_dashboard():
    with _db_lock, db() as con:
        lib_n = con.execute("SELECT COUNT(*) c FROM library").fetchone()["c"]
        h24 = con.execute("SELECT COUNT(*) c FROM history WHERE created_at >= ?",
                          ((datetime.now() - timedelta(hours=24)).isoformat(),)).fetchone()["c"]
        top = con.execute("SELECT keyword, SUM(result_count) s, COUNT(*) n FROM history GROUP BY keyword ORDER BY s DESC LIMIT 8").fetchall()
    return jsonify({"ok": True, "library_count": lib_n, "searches_24h": h24,
                    "quota_today": quota_today(), "top_keywords": [dict(r) for r in top]})

# ---------------- Giao dien ----------------
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YT NICHE HUNTER v2 PRO - Phan tich YouTube & Tim ngach</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0a0e1a;--bg2:#0f1526;--card:#141b32;--line:rgba(124,58,237,.22);
--txt:#e8edff;--mut:#8b98b8;--vio:#7c3aed;--cy:#22d3ee;--grn:#22c55e;--red:#ef4444;--yel:#f59e0b}
body{background:radial-gradient(1200px 600px at 80% -10%,rgba(124,58,237,.18),transparent),
radial-gradient(900px 500px at 10% 110%,rgba(34,211,238,.10),transparent),var(--bg);
color:var(--txt);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
.wrap{display:flex;min-height:100vh}
.side{width:236px;background:rgba(10,14,26,.85);border-right:1px solid var(--line);
padding:22px 14px;position:sticky;top:0;height:100vh;backdrop-filter:blur(8px);flex-shrink:0}
.logo{font-size:22px;font-weight:800;background:linear-gradient(90deg,var(--vio),var(--cy));
-webkit-background-clip:text;background-clip:text;color:transparent;letter-spacing:1px}
.logo small{display:block;font-size:10px;color:var(--mut);letter-spacing:3px;-webkit-text-fill-color:var(--mut)}
.nav{margin-top:22px;display:flex;flex-direction:column;gap:6px}
.nav button{background:none;border:1px solid transparent;color:var(--mut);text-align:left;
padding:11px 13px;border-radius:11px;cursor:pointer;font-size:14px;transition:.18s;width:100%}
.nav button:hover{background:rgba(124,58,237,.12);color:var(--txt)}
.nav button.on{background:linear-gradient(90deg,rgba(124,58,237,.35),rgba(34,211,238,.18));
border-color:var(--line);color:#fff;font-weight:600}
.apibox{margin-top:22px;padding:12px;border:1px solid var(--line);border-radius:12px;
background:rgba(124,58,237,.07);font-size:12.5px;color:var(--mut)}
.apibox b{color:var(--cy)}
main{flex:1;padding:26px 30px;max-width:1460px}
.page{display:none;animation:fade .25s}
.page.on{display:block}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1}}
h2{font-size:26px;margin-bottom:4px}
.sub{color:var(--mut);font-size:13.5px;margin-bottom:20px}
.card{background:linear-gradient(160deg,rgba(20,27,50,.92),rgba(15,21,38,.92));
border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:18px;
box-shadow:0 8px 30px rgba(0,0,0,.35)}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}
.grid4 .card{margin:0;text-align:center}
.big{font-size:30px;font-weight:800;background:linear-gradient(90deg,var(--vio),var(--cy));
-webkit-background-clip:text;background-clip:text;color:transparent}
.lbl{color:var(--mut);font-size:12.5px;margin-top:6px}
.frow{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin-bottom:12px}
.fld{display:flex;flex-direction:column;gap:6px}
.fld label{font-size:12px;color:var(--mut);font-weight:600}
input[type=text],input[type=number],input[type=password],select{background:#0b1120;border:1px solid var(--line);
color:var(--txt);border-radius:10px;padding:10px 13px;font-size:14px;outline:none;min-width:0}
input:focus,select:focus{border-color:var(--vio);box-shadow:0 0 0 3px rgba(124,58,237,.25)}
.btn{background:linear-gradient(90deg,var(--vio),#6d28d9);border:none;color:#fff;font-weight:700;
padding:11px 22px;border-radius:11px;cursor:pointer;font-size:14px;transition:.18s}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 18px rgba(124,58,237,.45)}
.btn.cy{background:linear-gradient(90deg,#0891b2,var(--cy));color:#04222a}
.btn.ghost{background:rgba(124,58,237,.1);border:1px solid var(--line);color:var(--txt)}
.btn.sm{padding:7px 13px;font-size:12.5px;border-radius:8px}
.btn:disabled{opacity:.5;cursor:wait;transform:none}
.tblwrap{overflow-x:auto;border:1px solid var(--line);border-radius:14px}
table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:900px}
th{background:rgba(124,58,237,.14);padding:11px 10px;text-align:left;font-size:12px;
color:var(--cy);text-transform:uppercase;letter-spacing:.5px;white-space:nowrap;
position:sticky;top:0}
td{padding:10px;border-top:1px solid rgba(124,58,237,.1);vertical-align:middle}
tr:hover td{background:rgba(124,58,237,.06)}
.ch{display:flex;gap:10px;align-items:center;min-width:200px}
.ch img{width:38px;height:38px;border-radius:50%;object-fit:cover;border:2px solid var(--line);flex-shrink:0}
.ch .avt{width:38px;height:38px;border-radius:50%;background:linear-gradient(135deg,var(--vio),var(--cy));
display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;flex-shrink:0}
.muted{color:var(--mut);font-size:12px}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11.5px;font-weight:700}
.badge.hot{background:rgba(239,68,68,.18);color:#f87171;border:1px solid rgba(239,68,68,.4)}
.badge.mid{background:rgba(245,158,11,.15);color:#fbbf24;border:1px solid rgba(245,158,11,.35)}
.badge.low{background:rgba(34,211,238,.12);color:var(--cy);border:1px solid rgba(34,211,238,.3)}
.badge.grn{background:rgba(34,197,94,.15);color:#4ade80;border:1px solid rgba(34,197,94,.35)}
.logbox{background:#070b16;border:1px solid var(--line);border-radius:12px;padding:14px;
font-family:Consolas,monospace;font-size:12.5px;max-height:190px;overflow-y:auto;color:#a5b4d4}
.logbox div{padding:2px 0;border-bottom:1px dashed rgba(124,58,237,.08)}
.alert{padding:12px 16px;border-radius:11px;margin-bottom:14px;font-size:13.5px}
.alert.ok{background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);color:#86efac}
.alert.warn{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);color:#fcd34d}
.alert.err{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);color:#fca5a5}
.loader{display:flex;gap:12px;align-items:center;justify-content:center;padding:34px;color:var(--mut)}
.spin{width:26px;height:26px;border:3px solid rgba(124,58,237,.25);border-top-color:var(--vio);
border-radius:50%;animation:sp 0.8s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.chip{background:rgba(34,211,238,.1);border:1px solid rgba(34,211,238,.35);color:var(--cy);
padding:7px 14px;border-radius:20px;cursor:pointer;font-size:13px}
.chip:hover{background:rgba(34,211,238,.22)}
canvas.chartbox{width:100%;height:280px;background:rgba(7,11,22,.6);border:1px solid var(--line);
border-radius:12px;padding:10px}
.kv{display:grid;grid-template-columns:200px 1fr;gap:12px;align-items:center;margin-bottom:12px}
.kv label{font-size:13.5px;color:var(--mut)}
.note{font-size:12.5px;color:var(--mut);line-height:1.7}
.note a{color:var(--cy)}
details.adv{margin:4px 0 12px}
details.adv summary{cursor:pointer;color:var(--cy);font-size:13.5px;font-weight:600}
.advgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:10px}
@media(max-width:1100px){.grid4{grid-template-columns:repeat(2,1fr)}.advgrid{grid-template-columns:repeat(2,1fr)}}
/* ===== ULTRA effects ===== */
.aurora{position:fixed;inset:0;z-index:-1;overflow:hidden;pointer-events:none}
.aurora i{position:absolute;width:520px;height:520px;border-radius:50%;filter:blur(110px);opacity:.32;animation:drift 18s ease-in-out infinite alternate}
.aurora i:nth-child(1){background:#7c3aed;top:-120px;left:-100px}
.aurora i:nth-child(2){background:#22d3ee;bottom:-140px;right:-80px;animation-delay:-6s}
.aurora i:nth-child(3){background:#ec4899;top:40%;left:55%;width:420px;height:420px;animation-delay:-12s;opacity:.2}
@keyframes drift{from{transform:translate(0,0) scale(1)}to{transform:translate(90px,60px) scale(1.15)}}
.card{backdrop-filter:blur(10px)}
.rowanim{animation:rowin .45s cubic-bezier(.2,.7,.3,1) backwards}
@keyframes rowin{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
tr.rowanim:hover td{background:rgba(124,58,237,.12)}
.btn{position:relative;overflow:hidden}
.btn::after{content:"";position:absolute;top:0;left:-80%;width:50%;height:100%;
background:linear-gradient(100deg,transparent,rgba(255,255,255,.35),transparent);
transform:skewX(-20deg);transition:left .55s}
.btn:hover::after{left:130%}
.chlink{color:var(--txt);text-decoration:none;font-weight:700;padding-bottom:2px;
background:linear-gradient(90deg,var(--vio),var(--cy));background-size:0% 2px;background-repeat:no-repeat;
background-position:left bottom;transition:background-size .3s,color .3s}
.chlink:hover{background-size:100% 2px;color:#fff}
.skel{border-radius:8px;background:linear-gradient(90deg,rgba(124,58,237,.08) 25%,rgba(124,58,237,.25) 50%,rgba(124,58,237,.08) 75%);
background-size:200% 100%;animation:shim 1.2s infinite}
@keyframes shim{to{background-position:-200% 0}}
#toasts{position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:10px}
.toast{background:rgba(20,27,50,.96);border:1px solid var(--line);border-left:4px solid var(--cy);
padding:13px 18px;border-radius:12px;font-size:13.5px;min-width:260px;max-width:380px;
box-shadow:0 10px 40px rgba(0,0,0,.5);animation:tin .35s cubic-bezier(.2,.9,.3,1.2)}
.toast.ok{border-left-color:var(--grn)}.toast.err{border-left-color:var(--red)}
.toast.out{animation:tout .3s forwards}
@keyframes tin{from{opacity:0;transform:translateX(60px)}to{opacity:1}}
@keyframes tout{to{opacity:0;transform:translateX(60px)}}
.prog{height:6px;background:rgba(124,58,237,.15);border-radius:6px;overflow:hidden;margin:12px 0}
.prog i{display:block;height:100%;width:30%;border-radius:6px;
background:linear-gradient(90deg,var(--vio),var(--cy));animation:slide 1.1s ease-in-out infinite}
@keyframes slide{0%{margin-left:-30%}100%{margin-left:100%}}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:#0a0e1a}
::-webkit-scrollbar-thumb{background:linear-gradient(var(--vio),var(--cy));border-radius:8px}
.nav button.on::before{content:"";position:absolute;left:2px;top:22%;height:56%;width:3px;border-radius:3px;
background:linear-gradient(var(--vio),var(--cy));box-shadow:0 0 12px var(--cy)}
.logo{animation:glowp 3s ease-in-out infinite}
@keyframes glowp{0%,100%{filter:drop-shadow(0 0 0 transparent)}50%{filter:drop-shadow(0 0 14px rgba(124,58,237,.55))}}
.count{font-variant-numeric:tabular-nums}
@media(max-width:1100px){.grid4{grid-template-columns:repeat(2,1fr)}.advgrid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:800px){.side{display:none}main{padding:16px}.grid4{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="aurora"><i></i><i></i><i></i></div>
<div class="wrap">
<aside class="side">
  <div class="logo">YT NICHE HUNTER<small>PRO ANALYTICS v2</small></div>
  <nav class="nav">
    <button data-p="dash" class="on">📊 Dashboard</button>
    <button data-p="search">🔍 Tìm kênh</button>
    <button data-p="analyze">📈 Phân tích kênh</button>
    <button data-p="similar">🎯 Kênh tương tự</button>
    <button data-p="library">📚 Thư viện</button>
    <button data-p="history">🕘 Lịch sử</button>
    <button data-p="settings">⚙️ Cài đặt</button>
  </nav>
  <div class="apibox">API: <b id="apiStatus">đang kiểm tra…</b><br>
  <span class="muted">Quota hôm nay: <b id="quotaTop">–</b> / 10.000</span></div>
</aside>
<main>
<!-- DASHBOARD -->
<section class="page on" id="pg-dash">
  <h2>📊 Dashboard</h2><div class="sub">Tổng quan công cụ phân tích YouTube & tìm ngách</div>
  <div class="grid4" id="dashStats"></div>
  <div class="card"><h3 style="margin-bottom:12px">🔥 Từ khóa tìm nhiều nhất</h3><div id="topKw" class="muted">Chưa có dữ liệu.</div></div>
  <div class="card"><h3 style="margin-bottom:10px">💡 Chỉ số trong tool</h3>
  <p class="note"><b style="color:var(--cy)">Outlier Score</b> = lượt xem video / trung vị lượt xem của kênh. ≥ 2.0 là video vượt trội, đáng học hỏi format.<br>
  <b style="color:var(--cy)">VPH</b> = lượt xem / số giờ từ lúc đăng. VPH cao = video đang lên nhanh.<br>
  <b style="color:var(--cy)">% Shorts</b> = ước tính từ thời lượng video ≤ 61 giây trong 20 video mới nhất.<br>
  <b style="color:var(--cy)">Tần suất</b> = số video trung bình đăng mỗi tháng (từ 20 video mới nhất).<br>
  <b style="color:var(--cy)">View/Sub</b> = tổng views / subscribers. Cao = kênh có sức lan tỏa tốt so với lượng sub.</p></div>
</section>
<!-- TIM KENH -->
<section class="page" id="pg-search">
  <h2>🔍 Tìm kênh theo từ khóa</h2><div class="sub">Quét kênh YouTube theo chủ đề, xem subs, tần suất đăng, lọc kênh tiềm năng</div>
  <div class="card">
    <div class="frow">
      <div class="fld" style="flex:2;min-width:220px"><label>Từ khóa</label>
        <input type="text" id="sq" placeholder="vd: review phim, game mobile..."></div>
      <div class="fld"><label>Khu vực</label><select id="sRegion"></select></div>
      <div class="fld"><label>Đăng trong</label><select id="sPub">
        <option value="all">Tất cả</option><option value="today">Hôm nay</option>
        <option value="week">Tuần này</option><option value="month" selected>Tháng này</option>
        <option value="3months">3 tháng</option><option value="year">Năm nay</option></select></div>
      <div class="fld" style="width:90px"><label>Max/KW</label>
        <input type="number" id="sMax" value="50" min="5" max="50"></div>
    </div>
    <details class="adv"><summary>▸ Bộ lọc nâng cao</summary>
      <div class="advgrid">
        <div class="fld"><label>Subs tối thiểu</label><input type="number" id="sMinSub" placeholder="vd: 10000"></div>
        <div class="fld"><label>Subs tối đa</label><input type="number" id="sMaxSub" placeholder="vd: 1000000"></div>
        <div class="fld"><label>TB view/video tối thiểu</label><input type="number" id="sMinAvg" placeholder="vd: 5000"></div>
        <div class="fld"><label>Sắp xếp</label><select id="sSort">
          <option value="subs">Subs cao nhất</option><option value="views">Tổng views cao nhất</option>
          <option value="avg">TB view/video cao nhất</option><option value="freq">Đăng nhiều nhất</option>
          <option value="viewsub">View/Sub cao nhất</option></select></div>
      </div>
    </details>
    <div class="frow">
      <button class="btn" id="btnSearch" onclick="doSearch()">▶ Bắt đầu tìm</button>
      <button class="btn cy" onclick="aiExpand()">✨ AI mở rộng từ khóa</button>
      <span style="flex:1"></span>
      <button class="btn ghost sm" onclick="exportLib('csv')">📄 CSV</button>
      <button class="btn ghost sm" onclick="exportLib('json')">📋 JSON</button>
      <button class="btn ghost sm" onclick="exportLib('xlsx')">📥 Excel</button>
    </div>
    <div id="kwChips" class="chips"></div>
    <div class="logbox" id="searchLog" style="margin-bottom:12px"><div>📝 Log tìm kiếm sẽ hiện ở đây…</div></div>
  </div>
  <div id="searchOut"></div>
</section>
<!-- PHAN TICH KENH -->
<section class="page" id="pg-analyze">
  <h2>📈 Phân tích kênh</h2><div class="sub">Dán link kênh → xem Outlier Score, VPH từng video, biểu đồ views</div>
  <div class="card"><div class="frow">
    <div class="fld" style="flex:1;min-width:260px"><label>Link / ID / @handle kênh</label>
      <input type="text" id="aq" placeholder="https://www.youtube.com/@tenkenh hoặc @tenkenh hoặc UC..."></div>
    <button class="btn" onclick="doAnalyze()">Phân tích ⚡</button>
  </div></div>
  <div id="analyzeOut"></div>
</section>
<!-- KENH TUONG TU -->
<section class="page" id="pg-similar">
  <h2>🎯 Kênh tương tự</h2><div class="sub">Từ 1 kênh mẫu → tìm các kênh cùng chủ đề, độ khớp cao</div>
  <div class="card"><div class="frow">
    <div class="fld" style="flex:1;min-width:260px"><label>Kênh mẫu (link / ID / @handle)</label>
      <input type="text" id="mq" placeholder="https://www.youtube.com/@tenkenh ..."></div>
    <div class="fld" style="width:110px"><label>Số lượng</label>
      <input type="number" id="mN" value="15" min="5" max="30"></div>
    <button class="btn" onclick="doSimilar()">Tìm ngay ⚡</button>
  </div></div>
  <div id="similarOut"></div>
</section>
<!-- THU VIEN -->
<section class="page" id="pg-library">
  <h2>📚 Thư viện kênh</h2><div class="sub">Lưu kênh tiềm năng, xuất báo cáo 1 chạm</div>
  <div class="card"><div class="frow">
    <button class="btn cy sm" onclick="exportLib('xlsx')">📥 Xuất Excel</button>
    <button class="btn ghost sm" onclick="exportLib('csv')">📄 Xuất CSV</button>
    <button class="btn ghost sm" onclick="loadLib()">🔄 Tải lại</button>
    <span class="muted" id="libCount"></span>
  </div></div>
  <div id="libOut"></div>
</section>
<!-- LICH SU -->
<section class="page" id="pg-history">
  <h2>🕘 Lịch sử tìm kiếm</h2><div class="sub">Các lần quét kênh đã chạy trên tool này</div>
  <div class="card"><div class="frow">
    <button class="btn ghost sm" onclick="clearHistory()">🗑️ Xóa lịch sử</button>
    <button class="btn ghost sm" onclick="loadHistory()">🔄 Tải lại</button>
  </div></div>
  <div id="histOut"></div>
</section>
<!-- CAI DAT -->
<section class="page" id="pg-settings">
  <h2>⚙️ Cài đặt</h2><div class="sub">API key & tùy chọn quét</div>
  <div class="card"><h3 style="margin-bottom:14px">🔑 YouTube Data API v3</h3>
    <div class="kv"><label>API Key</label>
      <div style="display:flex;gap:8px"><input type="password" id="setKey" style="flex:1" placeholder="AIza...">
      <button class="btn sm" onclick="saveSettings()">Lưu</button>
      <button class="btn ghost sm" onclick="testKey()">Kiểm tra</button></div></div>
    <div id="keyMsg"></div>
    <p class="note">Lấy key miễn phí tại <a href="https://console.cloud.google.com/" target="_blank">console.cloud.google.com</a>:
    tạo project → Library → bật <b>YouTube Data API v3</b> → Credentials → Create API key.
    Hạn mức miễn phí 10.000 điểm/ngày (1 lần tìm ≈ 130-250 điểm).</p>
  </div>
  <div class="card"><h3 style="margin-bottom:14px">🔍 Tùy chọn quét mặc định</h3>
    <div class="kv"><label>Khu vực mặc định</label><select id="setRegion"></select></div>
    <div class="kv"><label>Số video phân tích mỗi kênh</label>
      <select id="setEnrich"><option value="10">10 video (tiết kiệm quota)</option>
      <option value="20" selected>20 video (khuyên dùng)</option>
      <option value="30">30 video (chi tiết)</option></select></div>
    <div class="kv"><label>Max kết quả / từ khóa</label>
      <input type="number" id="setMax" value="50" min="5" max="50"></div>
    <div class="frow"><button class="btn sm" onclick="saveSettings()">💾 Lưu cài đặt</button></div>
  </div>
  <div class="card"><h3 style="margin-bottom:14px">✨ AI mở rộng từ khóa (tùy chọn)</h3>
    <div class="kv"><label>API base (OpenAI-compatible)</label>
      <input type="text" id="setAiBase" placeholder="https://api.openai.com/v1"></div>
    <div class="kv"><label>API key</label><input type="password" id="setAiKey" placeholder="sk-..."></div>
    <p class="note">Khi có key, nút "✨ AI mở rộng từ khóa" sẽ gợi ý thêm từ khóa liên quan để quét rộng hơn.</p>
    <div class="frow"><button class="btn sm" onclick="saveSettings()">💾 Lưu</button></div>
  </div>
</section>
</main>
</div>
<div id="toasts"></div>
<script>
const $=id=>document.getElementById(id);
const REGIONS=[["US","US - United States"],["VN","VN - Viet Nam"],["JP","JP - Japan"],
["KR","KR - Korea"],["GB","UK - United Kingdom"],["DE","DE - Germany"],["FR","FR - France"],
["BR","BR - Brazil"],["MX","MX - Mexico"],["IN","IN - India"],["ID","ID - Indonesia"],
["TH","TH - Thailand"],["TW","TW - Taiwan"],["CA","CA - Canada"],["AU","AU - Australia"]];
let lastResults=[];
function saveChByIdx(i){const c=lastResults[+i];if(c)saveCh(c)}
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;")
.replace(/>/g,"&gt;").replace(/"/g,"&quot;")}
function fmtN(n){n=+n||0;if(n>=1e9)return (n/1e9).toFixed(1)+"B";
if(n>=1e6)return (n/1e6).toFixed(1)+"M";if(n>=1e3)return (n/1e3).toFixed(1)+"K";return ""+n}
function fmtAgo(iso){if(!iso)return "-";const d=(Date.now()-new Date(iso).getTime())/864e5;
if(d<0)return "tương lai";if(d<1)return "Hôm nay";if(d<2)return "Hôm qua";
if(d<30)return Math.floor(d)+" ngày trước";if(d<365)return Math.floor(d/30)+" tháng trước";
return Math.floor(d/365)+" năm trước"}
function loading(el,msg){el.innerHTML="<div class=\"loader\"><div class=\"spin\"></div><div>"+(msg||"Đang tải dữ liệu...")+"</div></div>"}
function errBox(el,msg){el.innerHTML="<div class=\"alert err\">⚠️ "+esc(msg)+"</div>"}
function toast(msg,type){const t=document.createElement("div");t.className="toast "+(type||"");
t.innerHTML=msg;$("toasts").appendChild(t);
setTimeout(()=>{t.classList.add("out");setTimeout(()=>t.remove(),320)},3200)}
function skelTable(el,cols,rows){let h="<div class=\"tblwrap\"><table><tr>";
for(let k=0;k<cols;k++)h+="<th><div class=\"skel\" style=\"height:13px\"></div></th>";
h+="</tr>";
for(let r=0;r<(rows||6);r++){h+="<tr class=\"rowanim\" style=\"animation-delay:"+(r*60)+"ms\">";
for(let k=0;k<cols;k++)h+="<td><div class=\"skel\" style=\"height:15px\"></div></td>";h+="</tr>"}
el.innerHTML=h+"</table></div><div class=\"prog\"><i></i></div>"}
function countUp(el,to){const t0=performance.now(),D=900;
function f(t){const p=Math.min((t-t0)/D,1),e=1-Math.pow(1-p,3);
el.textContent=Math.round(to*e).toLocaleString();if(p<1)requestAnimationFrame(f)}
requestAnimationFrame(f)}
function slog(m){const el=$("searchLog");if(!el)return;const d=document.createElement("div");
d.textContent=m;el.prepend(d)}
async function api(path,method,body){
  const r=await fetch(path,{method:method||"GET",
    headers:body?{"Content-Type":"application/json"}:{},body:body?JSON.stringify(body):undefined});
  const j=await r.json();
  if(!j.ok)throw new Error(j.error||("HTTP "+r.status_code));
  return j;
}
function nav(p){
  document.querySelectorAll(".nav button").forEach(b=>b.classList.toggle("on",b.dataset.p===p));
  document.querySelectorAll(".page").forEach(s=>s.classList.toggle("on",s.id==="pg-"+p));
  if(p==="library")loadLib(); if(p==="history")loadHistory();
  if(p==="dash")loadDash(); if(p==="settings")loadSettings();
}
document.querySelectorAll(".nav button").forEach(b=>b.onclick=()=>nav(b.dataset.p));
function ensureChart(){
  if(window.Chart)return Promise.resolve(true);
  if(window._cl)return window._cl;
  window._cl=new Promise(res=>{const s=document.createElement("script");
    s.src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js";
    const to=setTimeout(()=>{try{s.remove()}catch(e){}res(false)},9000);
    s.onload=()=>{clearTimeout(to);res(!!window.Chart)};
    s.onerror=()=>{clearTimeout(to);try{s.remove()}catch(e){}res(false)};
    document.head.appendChild(s)});
  return window._cl;
}
async function loadStatus(){
  try{const s=await api("/api/status");
    $("apiStatus").textContent=s.has_key?"API thật":"Demo";
    $("quotaTop").textContent=(+s.quota_today||0).toLocaleString();
  }catch(e){$("apiStatus").textContent="lỗi kết nối"}
}
function outBadge(o){if(o>=3)return "<span class=\"badge hot\">🔥 OUTLIER ×"+o+"</span>";
if(o>=2)return "<span class=\"badge mid\">⚡ ×"+o+"</span>";return "<span class=\"muted\">×"+o+"</span>"}
function avatar(c){return c.thumb?"<img src=\""+c.thumb+"\">":"<div class=\"avt\">"+esc((c.title||"?")[0])+"</div>"}
function chCell(c){const u=c.url||("https://www.youtube.com/channel/"+c.channel_id);
const av=c.thumb?"<img src=\""+c.thumb+"\">":"<div class=\"avt\">"+esc((c.title||"?")[0])+"</div>";
return "<div class=\"ch\"><a href=\""+u+"\" target=\"_blank\" rel=\"noopener\" title=\"Mở kênh trên YouTube\">"+av+"</a>"+
"<div><a class=\"chlink\" href=\""+u+"\" target=\"_blank\" rel=\"noopener\" title=\"Mở kênh trên YouTube\">"+esc(c.title)+"</a><br>"+
"<span class=\"muted\">"+esc(c.channel_id||"")+(c.country?" · "+esc(c.country):"")+"</span></div></div>"}
function searchRow(c,i){
  return "<tr class=\"rowanim\" style=\"animation-delay:"+(Math.min(i,20)*35)+"ms\"><td>"+(i+1)+"</td><td>"+chCell(c)+"</td><td><b>"+fmtN(c.subs)+"</b></td>"+
  "<td>"+fmtN(c.videos)+"</td><td>"+fmtN(c.views)+"</td><td>"+fmtN(c.avg_views)+"</td>"+
  "<td>"+fmtN(c.avg10)+"</td><td>"+(c.shorts_pct||0)+"%</td><td>"+fmtAgo(c.last_upload)+"</td>"+
  "<td>"+(c.freq||0)+"/th</td><td>"+(c.view_sub||0)+"</td>"+
  "<td style=\"white-space:nowrap\"><button class=\"btn sm\" data-cid=\""+c.channel_id+
  "\" onclick=\"analyzeCh(this.dataset.cid)\">📈</button> "+
  "<button class=\"btn ghost sm\" data-idx=\""+i+"\" onclick=\"saveChByIdx(this.dataset.idx)\">⭐</button></td></tr>";
}
async function doSearch(){
  const q=$("sq").value.trim(); if(!q){toast("Nhập từ khóa đã nhé!","err");return}
  const out=$("searchOut"); skelTable(out,12,7);
  const btn=$("btnSearch"); btn.disabled=true; btn.textContent="⏳ Đang tìm…";
  $("searchLog").innerHTML="<div>🚀 Bắt đầu quét…</div>";
  try{
    const j=await api("/api/search_channels","POST",{q:q,region:$("sRegion").value,
      pubwin:$("sPub").value,max_results:+$("sMax").value||50,
      min_subs:+$("sMinSub").value||0,max_subs:+$("sMaxSub").value||0,
      min_avg:+$("sMinAvg").value||0,sort:$("sSort").value});
    lastResults=j.items;
    (j.logs||[]).forEach(slog);
    if(!j.items.length){out.innerHTML="<div class=\"alert warn\">Không tìm thấy kênh nào phù hợp.</div>";return}
    let h="<div class=\"alert ok\">✅ Tìm thấy <b>"+j.items.length+"</b> kênh cho \""+esc(q)+"\""+
    (j.demo?" (số liệu demo)":"")+"</div>";
    h+="<div class=\"tblwrap\"><table><tr><th>#</th><th>Tên kênh</th><th>Subs</th><th>Video</th>"+
    "<th>Tổng view</th><th>TB view</th><th>TB 10 video</th><th>%Shorts</th><th>Đăng cuối</th>"+
    "<th>Tần suất</th><th>V/Sub</th><th></th></tr>";
    j.items.forEach((c,i)=>{h+=searchRow(c,i)});
    h+="</table></div>"; out.innerHTML=h;
    loadStatus();
  }catch(e){errBox(out,e.message);slog("❌ LỖI: "+e.message)}
  btn.disabled=false; btn.textContent="▶ Bắt đầu tìm";
}
async function aiExpand(){
  const q=$("sq").value.trim(); if(!q){alert("Nhập từ khóa trước đã!");return}
  const box=$("kwChips"); box.innerHTML="<span class=\"muted\">✨ AI đang nghĩ…</span>";
  try{
    const j=await api("/api/ai_expand","POST",{q:q});
    box.innerHTML="";
    j.keywords.forEach(k=>{const s=document.createElement("span");s.className="chip";
      s.textContent=k;s.onclick=()=>{$("sq").value=k;doSearch()};box.appendChild(s)});
  }catch(e){box.innerHTML="<span class=\"muted\">⚠️ "+esc(e.message)+"</span>"}
}
function analyzeCh(cid){nav("analyze");$("aq").value=cid;doAnalyze()}
async function doAnalyze(){
  const q=$("aq").value.trim(); if(!q){toast("Nhập link/ID/@handle kênh!","err");return}
  const out=$("analyzeOut"); loading(out,"Đang phân tích kênh…");
  try{
    const j=await api("/api/analyze","POST",{q:q});
    const d=j.data, vs=d.videos;
    let h="<div class=\"card\"><div class=\"ch\" style=\"margin-bottom:14px\">"+
      (d.thumb?"<img src=\""+d.thumb+"\" style=\"width:64px;height:64px\">":"")+
      "<div><h3>"+esc(d.title)+"</h3><span class=\"muted\">"+fmtN(d.subs)+" subs · "+
      fmtN(d.views)+" views · "+d.video_count+" video</span></div></div>";
    h+="<div class=\"grid4\">"+
      "<div class=\"card\"><div class=\"big\">"+d.videos.length+"</div><div class=\"lbl\">Video phân tích</div></div>"+
      "<div class=\"card\"><div class=\"big\">"+fmtN(d.median_views)+"</div><div class=\"lbl\">Trung vị views</div></div>"+
      "<div class=\"card\"><div class=\"big\">"+d.shorts_pct+"%</div><div class=\"lbl\">Shorts (ước tính)</div></div>"+
      "<div class=\"card\"><div class=\"big\" style=\"font-size:20px\">v"+VERSION_TAG+"</div><div class=\"lbl\">Phiên bản</div></div></div>";
    h+="<canvas class=\"chartbox\" id=\"vchart\" style=\"margin-bottom:16px\"></canvas>";
    h+="<div class=\"tblwrap\"><table><tr><th>#</th><th>Video</th><th>Views</th><th>VPH</th>"+
    "<th>Outlier</th><th>Likes</th><th>Đăng</th></tr>";
    vs.forEach((v,i)=>{
      h+="<tr class=\"rowanim\" style=\"animation-delay:"+(Math.min(i,20)*30)+"ms\"><td>"+(i+1)+"</td><td style=\"min-width:260px\"><a href=\""+v.url+
      "\" target=\"_blank\" rel=\"noopener\" class=\"chlink\">"+esc(v.title)+"</a></td>"+
      "<td><b>"+fmtN(v.views)+"</b></td><td>"+fmtN(v.vph)+"/h</td><td>"+outBadge(v.outlier)+"</td>"+
      "<td>"+fmtN(v.likes)+"</td><td class=\"muted\">"+fmtAgo(v.published)+"</td></tr>";
    });
    h+="</table></div></div>"; out.innerHTML=h;
    if(await ensureChart()){
      const ctx=$("vchart").getContext("2d");
      new Chart(ctx,{type:"bar",
        data:{labels:vs.map((v,i)=>"V"+(i+1)),
          datasets:[{data:vs.map(v=>v.views),
            backgroundColor:vs.map(v=>v.outlier>=2?"#22c55e":"rgba(124,58,237,.7)"),borderRadius:4}]},
        options:{plugins:{legend:{display:false},
          title:{display:true,text:"Lượt xem từng video (xanh lá = outlier)",color:"#e8edff"}},
          scales:{y:{ticks:{color:"#8b98b8"},grid:{color:"rgba(124,58,237,.12)"}},
                  x:{ticks:{color:"#8b98b8"}}}}});
    }else{
      const cv=$("vchart");
      if(cv)cv.outerHTML="<div class=\"alert warn\">⚠️ Không tải được thư viện biểu đồ (mạng chặn CDN).</div>";
    }
    loadStatus();
  }catch(e){errBox(out,e.message)}
}
async function doSimilar(){
  const q=$("mq").value.trim(); if(!q){toast("Nhập kênh mẫu!","err");return}
  const out=$("similarOut"); skelTable(out,8,6);
  try{
    const j=await api("/api/similar","POST",{q:q,n:+$("mN").value||15});
    lastResults=j.channels;
    let h="<div class=\"alert ok\">🎯 Kênh mẫu: <b>"+esc(j.source.title)+
    "</b> → tìm thấy <b>"+j.channels.length+"</b> kênh tương tự"+(j.demo?" (demo)":"")+"</div>";
    h+="<div class=\"tblwrap\"><table><tr><th>#</th><th>Tên kênh</th><th>Subs</th><th>Video</th>"+
    "<th>Tổng view</th><th>TB view</th><th>Độ khớp</th><th></th></tr>";
    j.channels.forEach((c,i)=>{
      const mb=c.match>=2?"<span class=\"badge grn\">cao</span>":(c.match>=1?"<span class=\"badge mid\">trung bình</span>":"<span class=\"badge low\">thấp</span>");
      h+="<tr class=\"rowanim\" style=\"animation-delay:"+(Math.min(i,20)*35)+"ms\"><td>"+(i+1)+"</td><td>"+chCell(c)+"</td><td><b>"+fmtN(c.subs)+"</b></td>"+
      "<td>"+fmtN(c.videos)+"</td><td>"+fmtN(c.views)+"</td><td>"+fmtN(c.avg_views)+"</td><td>"+mb+"</td>"+
      "<td style=\"white-space:nowrap\"><button class=\"btn sm\" data-cid=\""+c.channel_id+
      "\" onclick=\"analyzeCh(this.dataset.cid)\">📈</button> "+
      "<button class=\"btn ghost sm\" data-idx=\""+i+"\" onclick=\"saveChByIdx(this.dataset.idx)\">⭐</button></td></tr>";
    });
    h+="</table></div>"; out.innerHTML=h; loadStatus();
  }catch(e){errBox(out,e.message)}
}
async function saveCh(c){
  try{await api("/api/library","POST",c);toast("⭐ Đã lưu kênh vào thư viện!","ok")}
  catch(e){toast("⚠️ Lỗi: "+esc(e.message),"err")}
}
async function delCh(cid){
  if(!confirm("Xóa kênh này khỏi thư viện?"))return;
  try{await api("/api/library/"+encodeURIComponent(cid),"DELETE");loadLib()}
  catch(e){alert("Lỗi: "+e.message)}
}
async function loadLib(){
  const out=$("libOut"); if(!out)return; loading(out,"Đang tải thư viện…");
  try{
    const j=await api("/api/library");
    $("libCount").textContent=j.items.length+" kênh đã lưu";
    if(!j.items.length){out.innerHTML="<div class=\"alert warn\">📚 Thư viện trống. Qua trang <b>Tìm kênh</b> bấm ⭐ để thêm.</div>";return}
    let h="<div class=\"tblwrap\"><table><tr><th>#</th><th>Tên kênh</th><th>Subs</th><th>Video</th>"+
    "<th>Tổng view</th><th>TB view</th><th>%Shorts</th><th>Lưu lúc</th><th></th></tr>";
    j.items.forEach((c,i)=>{
      h+="<tr class=\"rowanim\" style=\"animation-delay:"+(Math.min(i,20)*35)+"ms\"><td>"+(i+1)+"</td><td>"+chCell(c)+"</td><td><b>"+fmtN(c.subs)+"</b></td>"+
      "<td>"+fmtN(c.videos)+"</td><td>"+fmtN(c.views)+"</td><td>"+fmtN(c.avg_views)+"</td>"+
      "<td>"+(c.shorts_pct||0)+"%</td><td class=\"muted\">"+fmtAgo(c.added_at)+"</td>"+
      "<td style=\"white-space:nowrap\"><button class=\"btn sm\" data-cid=\""+c.channel_id+
      "\" onclick=\"analyzeCh(this.dataset.cid)\">📈</button> "+
      "<button class=\"btn ghost sm\" data-cid=\""+c.channel_id+
      "\" onclick=\"delCh(this.dataset.cid)\">🗑️</button></td></tr>";
    });
    h+="</table></div>"; out.innerHTML=h;
  }catch(e){errBox(out,e.message)}
}
function exportLib(fmt){window.open("/api/export?fmt="+fmt,"_blank")}
async function loadHistory(){
  const out=$("histOut"); if(!out)return; loading(out,"Đang tải…");
  try{
    const j=await api("/api/history");
    if(!j.items.length){out.innerHTML="<div class=\"alert warn\">Chưa có lịch sử.</div>";return}
    let h="<div class=\"tblwrap\"><table style=\"min-width:600px\"><tr><th>#</th><th>Loại</th>"+
    "<th>Từ khóa</th><th>Khu vực</th><th>Kết quả</th><th>Thời gian</th></tr>";
    j.items.forEach((r,i)=>{
      h+="<tr><td>"+(i+1)+"</td><td>"+(r.kind==="search"?"🔍 Tìm kênh":"🎯 Tương tự")+"</td>"+
      "<td><b>"+esc(r.keyword)+"</b></td><td>"+esc(r.region||"-")+"</td><td>"+r.result_count+"</td>"+
      "<td class=\"muted\">"+esc(r.created_at||"")+"</td></tr>";
    });
    h+="</table></div>"; out.innerHTML=h;
  }catch(e){errBox(out,e.message)}
}
async function clearHistory(){
  if(!confirm("Xóa toàn bộ lịch sử?"))return;
  await api("/api/history/clear","POST"); loadHistory();
}
async function loadDash(){
  try{
    const j=await api("/api/dashboard");
    $("dashStats").innerHTML=
      "<div class=\"card\"><div class=\"big count\" id=\"cLib\">0</div><div class=\"lbl\">📚 Kênh đã lưu</div></div>"+
      "<div class=\"card\"><div class=\"big count\" id=\"cSch\">0</div><div class=\"lbl\">🔍 Lượt quét 24h</div></div>"+
      "<div class=\"card\"><div class=\"big count\" id=\"cQuo\">0</div><div class=\"lbl\">⚡ Quota đã dùng</div></div>"+
      "<div class=\"card\"><div class=\"big\" style=\"font-size:22px\">v"+VERSION_TAG+"</div><div class=\"lbl\">🕒 Phiên bản</div></div>";
    countUp($("cLib"),+j.library_count||0);
    countUp($("cSch"),+j.searches_24h||0);
    countUp($("cQuo"),+j.quota_today||0);
    const tk=$("topKw");
    if(j.top_keywords.length){
      tk.innerHTML=j.top_keywords.map(k=>"<span class=\"chip\" style=\"cursor:default\">"+esc(k.keyword)+
      " ("+k.n+" lần)</span>").join(" ");
    }
  }catch(e){$("dashStats").innerHTML="<div class=\"alert err\">⚠️ "+esc(e.message)+"</div>"}
}
async function loadSettings(){
  try{
    const j=await api("/api/settings");
    if(!$("setKey").value)$("setKey").placeholder=j.yt_api_key||"AIza...";
    const sr=$("sRegion"), gr=$("setRegion");
    if(!sr.options.length){
      REGIONS.forEach(r=>{const o=document.createElement("option");o.value=r[0];o.textContent=r[1];sr.appendChild(o)});
      REGIONS.forEach(r=>{const o=document.createElement("option");o.value=r[0];o.textContent=r[1];gr.appendChild(o)});
    }
    sr.value=j.region||"US"; gr.value=j.region||"US";
    $("setEnrich").value=j.enrich_n||"20"; $("setMax").value=j.max_results||"50";
    $("setAiBase").value=j.ai_base||"";
    if(!$("setAiKey").value)$("setAiKey").placeholder=j.ai_key||"sk-...";
  }catch(e){}
}
async function saveSettings(){
  const body={region:$("setRegion").value,enrich_n:$("setEnrich").value,
    max_results:$("setMax").value,ai_base:$("setAiBase").value.trim()};
  if($("setKey").value)body.yt_api_key=$("setKey").value.trim();
  if($("setAiKey").value)body.ai_key=$("setAiKey").value.trim();
  try{await api("/api/settings","POST",body);
    $("keyMsg").innerHTML="<div class=\"alert ok\">✅ Đã lưu cài đặt!</div>";
    $("setKey").value="";$("setAiKey").value="";loadStatus();
  }catch(e){$("keyMsg").innerHTML="<div class=\"alert err\">⚠️ "+esc(e.message)+"</div>"}
}
async function testKey(){
  $("keyMsg").innerHTML="<div class=\"muted\">⏳ Đang kiểm tra…</div>";
  try{const j=await api("/api/test_key","POST");
    $("keyMsg").innerHTML="<div class=\"alert ok\">✅ "+esc(j.msg)+"</div>"}
  catch(e){$("keyMsg").innerHTML="<div class=\"alert err\">⚠️ "+esc(e.message)+"</div>"}
}
const VERSION_TAG="__VERSION__";
loadStatus(); loadDash(); loadSettings();
$("sq").addEventListener("keydown",e=>{if(e.key==="Enter")doSearch()});
$("aq").addEventListener("keydown",e=>{if(e.key==="Enter")doAnalyze()});
$("mq").addEventListener("keydown",e=>{if(e.key==="Enter")doSimilar()});
</script>
</body>
</html>"""

HTML_PAGE = HTML_PAGE.replace("__VERSION__", VERSION)

# ---------------- Chay ----------------
def main():
    init_db()
    print("=" * 62)
    print(" %s v%s - Web phan tich YouTube" % (APP_NAME, VERSION))
    print(" Mo trinh duyet:  http://127.0.0.1:5000")
    print(" Nhan Ctrl+C de dung.")
    print("=" * 62)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)

if __name__ == "__main__":
    main()
