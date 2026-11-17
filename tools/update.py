#!/usr/bin/env python3
"""뉴데이 사이트 업데이트 도구 (많이 본 뉴스).
사용: python3 -I update.py OUTDIR [전체 개수=30] [분야별 개수=15] [이전 news.json]
네이트 뉴스 '많이 본 뉴스' 시사 종합 랭킹(TOP)과 네이버 뉴스 정치·경제·사회·세계 섹션 최신기사를 읽고, 각 기사의 제목·분야·언론사·전송시각·대표사진을 모아
OUTDIR/news.json 과 OUTDIR/img/*.jpg 를 만든다. 사진을 못 받은 기사는 뺀다.
"""
import json, re, sys, os, io, hashlib, html, subprocess, time, urllib.request
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
CAT = {"정치": "정치", "경제": "경제", "사회": "사회", "세계": "국제", "국제": "국제", "IT/과학": "IT",
       "IT": "IT", "생활/문화": "문화", "문화": "문화", "연예": "연예", "스포츠": "스포츠", "오피니언": "오피니언"}

def get(url, n=6_000_000):
    last = None
    for t in (15, 25):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=t) as r:
                return r.read(n)
        except Exception as e:
            last = e
    r = subprocess.run(["curl", "-sL", "--max-time", "30", "-A", UA, url], capture_output=True)
    if r.returncode == 0 and r.stdout:
        return r.stdout[:n]
    raise last

def text(b):
    for enc in ("utf-8", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("cp949", "ignore")

SECTIONS = {"pol": "정치", "eco": "경제", "soc": "사회", "int": "국제"}
NAVER = {"100": "정치", "101": "경제", "102": "사회", "104": "국제"}

def naver_ids(sid):
    for _ in range(3):
        try:
            out = []
            for pg in (1, 2, 3):
                s = text(get(f"https://news.naver.com/main/list.naver?mode=LSD&mid=sec&sid1={sid}&page={pg}"))
                for u in re.findall(r'https?://n\.news\.naver\.com/mnews/article/\d+/\d+', s):
                    if u not in out:
                        out.append(u)
            if out:
                return out
        except Exception as e:
            print("naver list fail", sid, e, file=sys.stderr)
        time.sleep(2)
    return []

def naver_one(url, cat):
    s = text(get(url))
    title = meta(s, "og:title").strip()
    # 사진 기사·외신 사진(영문 제목)·포토 기사는 뺀다
    if not title or not re.search(r"[가-힣]", title) or re.match(r"^\s*\[(포토|사진|화보|영상|PHOTO)\]", title):
        return None
    src = re.sub(r"\s*\|\s*네이버\s*$", "", meta(s, "og:article:author")).strip() or "네이버 뉴스"
    m = re.search(r'data-date-time="(\d{4}-\d\d-\d\d \d\d:\d\d)', s)
    at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=KST).isoformat() if m else datetime.now(KST).isoformat(timespec="minutes")
    img = meta(s, "og:image") or None
    if img and ("static" in img and "pstatic.net/image" not in img):
        img = None
    if re.search(r"(EPA|AP|AFP|로이터|UPI|신화|AAP)\s*연합", src):
        return None
    return {"c": cat, "t": title, "s": src, "u": url, "at": at, "_img": img}

def naver_work(url, cat, out):
    try:
        it = naver_one(url, cat)
        if not it:
            return None
        try:
            ok = save_img(it, out)
        except Exception:
            it.pop("_img", None)
        return it
    except Exception as e:
        print("skip", url, e, file=sys.stderr)
    return None

def ids_of(sc):
    for _ in range(3):
        try:
            s = text(get(f"https://news.nate.com/rank/interest?sc={sc}"))
            out = []
            for vid, mid in re.findall(r'news\.nate\.com/view/(\d{8}n\d+)\?mid=(n\d+)', s):
                if mid in ("n1007", "n1008"):  # 스포츠·연예 제외
                    continue
                if vid not in out:
                    out.append(vid)
            if out:
                return out
        except Exception as e:
            print("rank fail", sc, e, file=sys.stderr)
        time.sleep(2)
    return []

def meta(s, prop):
    m = re.search(r'<meta[^>]+property="%s"[^>]+content="([^"]*)"' % re.escape(prop), s)
    return html.unescape(m.group(1)) if m else ""

def one(vid):
    url = f"https://news.nate.com/view/{vid}"
    s = text(get(url))
    title = re.sub(r"\s*:\s*네이트 뉴스\s*$", "", meta(s, "og:title")).strip()
    desc = meta(s, "og:description")
    m = re.search(r"한눈에 보는 오늘\s*:\s*([^-]+?)\s*-", desc)
    c = CAT.get(m.group(1).strip(), "사회") if m else "사회"
    if c in ("연예", "스포츠"):
        return None
    m = re.search(r'class="medium"[^>]*>([^<]+)', s)
    src = html.unescape(m.group(1)).strip() if m else "네이트 뉴스"
    m = re.search(r'firstDate[^>]*>[^<]*<em>(\d{4}-\d\d-\d\d \d\d:\d\d)', s)
    at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=KST).isoformat() if m else datetime.now(KST).isoformat(timespec="minutes")
    img = meta(s, "og:image")
    if not title:
        return None
    if not img or "nate_news" in img or "logo" in img.lower():
        img = None
    elif img.startswith("//"):
        img = "https:" + img
    return {"c": c, "t": title, "s": src, "u": url, "at": at, "_img": img}

def save_img(it, out):
    url = it.pop("_img", None)
    if not url:
        return False
    im = Image.open(io.BytesIO(get(url))).convert("RGB")
    if im.width < 300:
        return False
    if im.width > 640:
        im = im.resize((640, round(im.height * 640 / im.width)), Image.LANCZOS)
    name = hashlib.md5(it["u"].encode()).hexdigest()[:12] + ".jpg"
    im.save(os.path.join(out, "img", name), "JPEG", quality=70, optimize=True, progressive=True)
    it["img"] = "img/" + name
    return True

def work(vid, out, need_img=True):
    try:
        it = one(vid)
        if not it:
            return None
        try:
            ok = save_img(it, out)
        except Exception as e:
            ok = False
            it.pop("_img", None)
        if ok or not need_img:
            return it
    except Exception as e:
        print("skip", vid, e, file=sys.stderr)
    return None

def main(out, n=30, per=15, prev_path=None):
    os.makedirs(os.path.join(out, "img"), exist_ok=True)
    overall = ids_of("sisa")[: n + 8]
    latest_urls = {c: naver_ids(sid)[: per + 30] for sid, c in NAVER.items()}
    with ThreadPoolExecutor(12) as ex:
        top_res = dict(zip(overall, ex.map(lambda v: work(v, out, True), overall)))
        jobs = [(u, c) for c, l in latest_urls.items() for u in l]
        lat_res = list(ex.map(lambda j: naver_work(j[0], j[1], out), jobs))
    items, seen_t = {}, set()
    r = 0
    for v in overall:
        it = top_res.get(v)
        if it and it["t"] not in seen_t and r < n:
            seen_t.add(it["t"]); r += 1
            it["rank"] = r
            items[v] = it
    # 분야별 최신 기사 (네이버): 최신 시각 순, 사진 있는 기사 우선
    for c in latest_urls:
        L = [it for it in lat_res if it and it["c"] == c and it["t"] not in seen_t]
        L.sort(key=lambda it: it["at"], reverse=True)
        L = sorted(L[: per + 6], key=lambda it: (0 if it.get("img") else 1, ))[:per] if sum(1 for it in L[:per] if not it.get("img")) > 3 else L[:per]
        L.sort(key=lambda it: it["at"], reverse=True)
        for k, it in enumerate(L, 1):
            seen_t.add(it["t"])
            it["lc"] = c
            it["lrank"] = k
            items[it["u"]] = it
    keep = list(items.values())
    # 지난 업데이트와 비교해 순위 변동(▲▼/NEW) 계산
    prev = {}
    if prev_path and os.path.exists(prev_path):
        try:
            for it in json.load(open(prev_path, encoding="utf-8")).get("items", []):
                if it.get("rank"):
                    prev[it["u"]] = it["rank"]
        except Exception as e:
            print("prev read fail", e, file=sys.stderr)
    if prev:
        for it in keep:
            if "rank" in it:
                it["delta"] = (prev[it["u"]] - it["rank"]) if it["u"] in prev else "new"
    keep.sort(key=lambda it: (it.get("rank", 999), it.get("lc", ""), it.get("lrank", 999)))
    data = {"updated": datetime.now(KST).isoformat(timespec="minutes"), "source": "네이트 많이 본 뉴스 + 네이버 분야별 최신뉴스", "items": keep}
    json.dump(data, open(os.path.join(out, "news.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    used = {it["img"].split("/")[1] for it in keep if it.get("img")}
    for f in os.listdir(os.path.join(out, "img")):
        if f not in used:
            os.remove(os.path.join(out, "img", f))
    from collections import Counter
    print(f"{len(keep)} articles; top {r}; latest {dict(Counter(i['lc'] for i in keep if 'lc' in i))}; with photo {sum(1 for i in keep if i.get('img'))}")

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 30, int(sys.argv[3]) if len(sys.argv) > 3 else 15, sys.argv[4] if len(sys.argv) > 4 else None)
