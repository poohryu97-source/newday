#!/usr/bin/env python3
"""뉴데이 사이트 업데이트 도구 (많이 본 뉴스).
사용: python3 -I update.py OUTDIR [전체 개수=30] [분야별 개수=15]
네이트 뉴스 '많이 본 뉴스' 시사 종합 랭킹과 정치·경제·사회·국제 분야별 랭킹을 읽고, 각 기사의 제목·분야·언론사·전송시각·대표사진을 모아
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
    if not title or not img or "nate_news" in img or "logo" in img.lower():
        return None
    if img.startswith("//"):
        img = "https:" + img
    return {"c": c, "t": title, "s": src, "u": url, "at": at, "_img": img}

def save_img(it, out):
    im = Image.open(io.BytesIO(get(it.pop("_img")))).convert("RGB")
    if im.width < 300:
        return False
    if im.width > 640:
        im = im.resize((640, round(im.height * 640 / im.width)), Image.LANCZOS)
    name = hashlib.md5(it["u"].encode()).hexdigest()[:12] + ".jpg"
    im.save(os.path.join(out, "img", name), "JPEG", quality=70, optimize=True, progressive=True)
    it["img"] = "img/" + name
    return True

def work(vid, out):
    try:
        it = one(vid)
        if it and save_img(it, out):
            return it
    except Exception as e:
        print("skip", vid, e, file=sys.stderr)
    return None

def main(out, n=30, per=15):
    os.makedirs(os.path.join(out, "img"), exist_ok=True)
    overall = ids_of("sisa")[: n + 8]
    sec = {c: ids_of(sc)[: per + 4] for sc, c in SECTIONS.items()}
    allids = list(dict.fromkeys(overall + [v for l in sec.values() for v in l]))
    with ThreadPoolExecutor(8) as ex:
        res = dict(zip(allids, ex.map(lambda v: work(v, out), allids)))
    items, seen_t = {}, set()
    for v in allids:
        it = res.get(v)
        if it and it["t"] not in seen_t:
            seen_t.add(it["t"]); items[v] = it
    # 분야 랭킹 페이지에 나온 기사는 그 분야로 확정하고 분야 내 순위를 매긴다
    for c, l in sec.items():
        r = 0
        for v in l:
            if v in items and "crank" not in items[v] and r < per:
                r += 1
                items[v]["c"] = c
                items[v]["crank"] = r
    r = 0
    for v in overall:
        if v in items and r < n:
            r += 1
            items[v]["rank"] = r
    keep = [it for it in items.values() if "rank" in it or "crank" in it]
    keep.sort(key=lambda it: (it.get("rank", 999), it.get("crank", 999)))
    data = {"updated": datetime.now(KST).isoformat(timespec="minutes"), "source": "네이트 뉴스 많이 본 뉴스", "items": keep}
    json.dump(data, open(os.path.join(out, "news.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    used = {it["img"].split("/")[1] for it in keep}
    for f in os.listdir(os.path.join(out, "img")):
        if f not in used:
            os.remove(os.path.join(out, "img", f))
    from collections import Counter
    print(f"{len(keep)} articles with photos; overall {r}; by section {dict(Counter(i['c'] for i in keep))}")

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 30, int(sys.argv[3]) if len(sys.argv) > 3 else 15)
