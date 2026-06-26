"""
緊急救護課程爬蟲 v3
課程 (type=course) 與最新消息 (type=news) 分開標記
"""

import json, re, time, hashlib
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

OUTPUT_JSON = Path("data/courses.json")
OUTPUT_RSS  = Path("docs/feed.xml")
OUTPUT_JSON.parent.mkdir(exist_ok=True)
OUTPUT_RSS.parent.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}

COURSE_KW = ["課程","活動","研討","訓練","工作坊","workshop","course","training","seminar","conference"]
NEWS_KW   = ["公告","消息","通知","聲明","宣布","公文","轉知","說明","重要","notice","announcement"]

def make_id(title, source):
    return hashlib.md5(f"{source}::{title}".encode()).hexdigest()[:12]

def clean(t):
    return re.sub(r"\s+", " ", t or "").strip()

def extract_all_dates(text):
    if not text: return []
    dates = []
    for pat in [r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})",
                r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日"]:
        for m in re.finditer(pat, text):
            try:
                y,mo,d = int(m.group(1)),int(m.group(2)),int(m.group(3))
                if 2020<=y<=2035 and 1<=mo<=12 and 1<=d<=31:
                    dates.append(f"{y:04d}-{mo:02d}-{d:02d}")
            except: pass
    return sorted(set(dates))

def parse_dates_from_title(title):
    title = title or ""
    result = {"event_date": None, "deadline": None}
    deadline_kw = re.search(r"報名截止|截止日期?|報名至|最後報名|deadline|last.day", title, re.I)
    if deadline_kw:
        before = extract_all_dates(title[:deadline_kw.start()])
        after  = extract_all_dates(title[deadline_kw.end():])
        result["deadline"] = before[-1] if before else (after[0] if after else None)
        all_d = extract_all_dates(title)
        rest  = [d for d in all_d if d != result["deadline"]]
        result["event_date"] = sorted(rest)[-1] if rest else (all_d[-1] if all_d else None)
    else:
        all_d = extract_all_dates(title)
        result["event_date"] = all_d[-1] if all_d else None
    return result

def classify_type(title):
    """判斷是課程還是最新消息"""
    t = (title or "").lower()
    is_course = any(k in t for k in COURSE_KW)
    is_news   = any(k in t for k in NEWS_KW)
    if is_course: return "course"
    if is_news:   return "news"
    return "news"  # 預設歸類為消息

# Big5 編碼網站名單（需強制指定編碼）
BIG5_HOSTS = {"www.sgecm.org.tw", "sgecm.org.tw"}

# 需要先訪問首頁取 Cookie 的網站（有 Referer / Session 驗證）
_SESSION_CACHE = {}   # host -> requests.Session

def _get_session(host):
    """取得或建立帶 Cookie 的 Session，針對有反爬機制的網站"""
    if host in _SESSION_CACHE:
        return _SESSION_CACHE[host]
    s = requests.Session()
    s.headers.update({
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    })
    try:
        # 先訪問首頁，讓伺服器設定 Session Cookie
        s.get(f"http://{host}/", timeout=10, allow_redirects=True)
        time.sleep(0.5)
    except: pass
    _SESSION_CACHE[host] = s
    return s

# 使用 Session + Referer 的網站清單
SESSION_HOSTS = {"www.trauma.org.tw", "trauma.org.tw"}

def fetch(url, timeout=15, encoding=None):
    from urllib.parse import urlparse as _up
    _host = _up(url).netloc

    # 自動偵測 Big5 網站
    if _host in BIG5_HOSTS:
        encoding = "big5"

    # 有反爬機制的網站使用 Session + Cookie
    if _host in SESSION_HOSTS:
        _s = _get_session(_host)
        _s.headers["Referer"] = f"http://{_host}/"
        for attempt in range(2):
            try:
                r = _s.get(url, timeout=timeout, allow_redirects=True)
                r.raise_for_status()
                r.encoding = encoding or r.apparent_encoding or "utf-8"
                return BeautifulSoup(r.text, "html.parser")
            except Exception as e:
                if attempt == 0:
                    time.sleep(1)
                    continue
                print(f"    ⚠️ fetch 失敗: {e}")
                return None

    _headers = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    for attempt in range(2):  # 最多重試一次
        try:
            r = requests.get(url, headers=_headers, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            # 指定編碼（Big5 優先，否則自動偵測）
            r.encoding = encoding or r.apparent_encoding or "utf-8"
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            if attempt == 0:
                time.sleep(1)  # 等一秒後重試
                continue
            print(f"    ⚠️ fetch 失敗: {e}")
            return None

def resolve(href, base):
    if not href: return base
    if href.startswith("http"): return href
    from urllib.parse import urljoin
    return urljoin(base, href)

def mk(title, source, cat, url, item_type=None, date=None, deadline=None):
    t = clean(title)
    d = parse_dates_from_title(t)
    return {
        "id":       make_id(t, source),
        "title":    t,
        "source":   source,
        "cat":      cat,
        "type":     item_type or classify_type(t),
        "date":     date or d["event_date"],
        "deadline": deadline or d["deadline"],
        "url":      url,
    }

# ══════════════════════════════════════════════════════
# 各單位爬蟲（逐一對應正確頁面）
# ══════════════════════════════════════════════════════

def scrape_sem():
    """台灣急診醫學會 — 列表頁取課程，詳細頁取截止日"""
    import re as _re
    out = []
    BASE = "https://www.sem.org.tw"

    def _parse_sem_deadline(detail_soup, event_date):
        """從詳細頁內文解析報名截止日（需早於活動日）"""
        if not detail_soup: return None
        text = detail_soup.get_text(" ", strip=True)
        # 台灣急診醫學會格式：「即日起至115年6月30日」或「至YYYY/MM/DD」
        for pat in [
            r"至\s*(\d{3})年(\d{1,2})月(\d{1,2})日",        # 民國年：至115年6月30日
            r"至\s*(\d{4})/(\d{1,2})/(\d{1,2})",             # 西元：至2026/06/30
            r"截止.*?(\d{4})[/.](\d{1,2})[/.](\d{1,2})",    # 截止...2026/06/30
            r"(\d{4})[/.](\d{1,2})[/.](\d{1,2}).*?(?:截止|止)",
        ]:
            m = _re.search(pat, text)
            if not m: continue
            try:
                y = int(m.group(1))
                if y < 200: y += 1911   # 民國轉西元
                mo, d = int(m.group(2)), int(m.group(3))
                if not (2024 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31): continue
                candidate = f"{y:04d}-{mo:02d}-{d:02d}"
                # 截止日必須早於活動日
                if event_date is None or candidate <= event_date:
                    return candidate
            except: pass
        return None

    # ── 課程/活動：列表頁取基本資訊，詳細頁取截止日 ───────────────────────
    course_pages = [
        ("/Activity/A/Index",   "course"),   # 學會主辦積分活動
        ("/Activity/B/Index",   "course"),   # 非學會主辦積分活動
        ("/Activity/AHA/Index", "course"),   # AHA急救教育訓練
    ]
    for path, itype in course_pages:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href*='/Activity/'][href*='/Details/']"):
            raw = clean(a.get_text())
            if len(raw) < 5: continue
            # 列表頁文字格式：「標題 主辦單位 YYYY/MM/DD 積分N 類型 狀態」
            m = _re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", raw)
            date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
            title = raw[:m.start()].strip() if m else raw
            if len(title) < 4: continue
            href = resolve(a.get("href",""), BASE)
            # 進詳細頁抓截止日
            detail_soup = fetch(href)
            deadline = _parse_sem_deadline(detail_soup, date_str)
            time.sleep(0.3)
            out.append(mk(title, "台灣急診醫學會", "台灣學會", href, itype,
                          date=date_str, deadline=deadline))

    # ── 最新消息：列表頁直接取日期，不需進詳細頁 ─────────────────────────
    news_pages = [
        ("/News/11/Index", "news"),   # 學會公告
        ("/News/10/Index", "news"),   # 秘書處公告
        ("/News/12/Index", "news"),   # 年會專區
    ]
    for path, itype in news_pages:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href*='/News/'][href*='/Details/']"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            if any(skip in t for skip in ["學會公告","秘書處公告","年會專區","公文來函","AHA專區"]): continue
            row = a.find_parent("tr")
            date_text = row.get_text() if row else ""
            m = _re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", date_text)
            date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
            href = resolve(a.get("href",""), BASE)
            out.append(mk(t, "台灣急診醫學會", "台灣學會", href, itype, date=date_str))

    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen and len(it.get("title","")) > 3:
            seen.add(it["id"])
            unique.append(it)
    return unique[:40]

def scrape_seccm():
    """台灣急救加護醫學會 — 正確表格結構"""
    out = []
    BASE = "https://www.seccm.org.tw"
    # 各類課程列表頁（表格：日期 / 標題 / 地點）
    course_pages = [
        "/news/acls_course.asp",       # ACLS委員會課程
        "/news/acls_union.asp",        # 聯甄課程
        "/news/acls_class.asp",        # ACLS課程舉辦單位
        "/news/acls_NotUnion.asp",     # 非聯甄課程
    ]
    for path in course_pages:
        soup = fetch(BASE + path)
        if not soup: continue
        for row in soup.select("table tr"):
            cols = row.find_all(["td","th"])
            if len(cols) < 2: continue
            # 找標題欄位（含連結或文字）
            for i, col in enumerate(cols):
                a = col.find("a")
                t = clean(col.get_text())
                if a:
                    t = clean(a.get_text())
                if len(t) < 8 or len(t) > 200: continue
                if any(k in t for k in ["課程","ACLS","PALS","訓練","工作坊","研討","Provider","Instructor"]):
                    # 日期從其他欄位取得
                    all_text = row.get_text()
                    date = extract_all_dates(all_text)
                    href = resolve(a.get("href","") if a else "", BASE)
                    out.append(mk(t, "台灣急救加護醫學會", "台灣學會",
                                  href, "course", date[-1] if date else None))
                    break
    # 最新消息（公告列表）
    soup2 = fetch(BASE + "/orgNews/News_list.asp")
    if soup2:
        for row in soup2.select("table tr"):
            a = row.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 5: continue
            date = extract_all_dates(row.get_text())
            out.append(mk(t, "台灣急救加護醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), "news",
                          date[-1] if date else None))
    # 去重
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:50]

def scrape_trauma():
    """台灣外傷醫學會 — 課程活動 + 學會公告，從連結文字解析日期與截止日"""
    import re as _re
    out = []
    BASE = "http://www.trauma.org.tw"
    SKIP = {"回首頁","關於學會","最新消息","活動專區","會員專區","積分申請","專科甄審",
            "聯絡我們","分享","噗浪","twitter","line","facebook"}

    def _parse_trauma_dates(text):
        """從外傷醫學會連結文字解析：活動日期（民國/西元）和截止日"""
        # 西元日期
        dates_ce = []
        for m in _re.finditer(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2024 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31:
                dates_ce.append((f"{y:04d}-{mo:02d}-{d:02d}", m.start()))
        for m in _re.finditer(r"(\d{4})/(\d{1,2})/(\d{1,2})", text):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2024 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31:
                dates_ce.append((f"{y:04d}-{mo:02d}-{d:02d}", m.start()))
        # 民國年
        for m in _re.finditer(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日", text):
            y_roc = int(m.group(1))
            if 110 <= y_roc <= 120:   # 民國110~120 = 西元2021~2031
                y, mo, d = y_roc + 1911, int(m.group(2)), int(m.group(3))
                if 1 <= mo <= 12 and 1 <= d <= 31:
                    dates_ce.append((f"{y:04d}-{mo:02d}-{d:02d}", m.start()))

        if not dates_ce:
            return None, None

        dates_ce.sort(key=lambda x: x[0])  # 依日期排序
        all_dates = [d for d, _ in dates_ce]

        # 截止日判斷：在「至...止」結構中的日期
        deadline = None
        dl_m = _re.search(r"至(\d{2,4})年(\d{1,2})月(\d{1,2})日.*?止", text)
        if dl_m:
            y_r = int(dl_m.group(1))
            if y_r < 200: y_r += 1911
            mo, d = int(dl_m.group(2)), int(dl_m.group(3))
            if 2024 <= y_r <= 2035:
                deadline = f"{y_r:04d}-{mo:02d}-{d:02d}"

        # 活動日：排除截止日後，取最大（最晚）的日期（即課程當天）
        if deadline:
            rest = [d for d in all_dates if d != deadline]
            event_date = sorted(rest)[-1] if rest else all_dates[-1]
        else:
            event_date = all_dates[-1] if all_dates else None

        return event_date, deadline

    # ── 課程/活動 ─────────────────────────────────────────────────────────
    for path, item_type in [
        ("/active/listA.asp", "course"),   # 教育課程
        ("/active/listB.asp", "course"),   # 學術研討會
        ("/active/listC.asp", "course"),   # 其他單位活動
    ]:
        page_url = BASE + path
        soup = fetch(page_url)
        if not soup: continue

        # 偵測最大頁數（從分頁連結 listX.asp?/N.html 取最大 N）
        max_page = 1
        list_file = path.split("/")[-1]   # e.g. "listA.asp"
        for pg_a in soup.select(f"a[href*='{list_file}?/']"):
            href = pg_a.get("href", "")
            m = _re.search(r"\?/(\d+)\.html", href)
            if m:
                max_page = max(max_page, int(m.group(1)))

        # 逐頁抓取（第 1 頁已有，從第 2 頁起再 fetch）
        soups = [soup]
        for pg in range(2, max_page + 1):
            pg_url = f"{BASE}/active/{list_file}?/{pg}.html"
            pg_soup = fetch(pg_url)
            if pg_soup:
                soups.append(pg_soup)
            time.sleep(0.3)

        # 從所有頁面中提取活動連結
        for s in soups:
            for a in s.select("a[href*='/active/index.asp']"):
                raw = clean(a.get_text())
                if len(raw) < 5: continue
                href = resolve(a.get("href",""), page_url)
                event_date, deadline = _parse_trauma_dates(raw)
                title = raw.split("一、")[0].strip() if "一、" in raw else raw[:80].strip()
                if len(title) < 5:
                    title = raw[:60].strip()
                out.append(mk(title, "台灣外傷醫學會", "台灣學會",
                              href, item_type,
                              date=event_date, deadline=deadline))

    # ── 學會公告（消息）───────────────────────────────────────────────────
    for path, item_type in [
        ("/news/listA.asp", "news"),
        ("/news/listC.asp", "news"),
    ]:
        page_url = BASE + path
        soup = fetch(page_url)
        if not soup: continue
        # 只選消息詳細頁連結（href 含 /News/index.asp?/ 或 /news/index.asp?/）
        for a in soup.select("a[href*='index.asp?/']"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 300: continue
            href = resolve(a.get("href",""), page_url)
            parent_text = a.find_parent().get_text() if a.find_parent() else ""
            date = extract_all_dates(parent_text)
            out.append(mk(t, "台灣外傷醫學會", "台灣學會",
                          href, item_type,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:50]

def scrape_disaster():
    """台灣災難醫學會 — 網站已改版為單頁靜態，爬首頁 + 解析內嵌內容"""
    out = []
    BASE = "http://www.disaster.org.tw"
    soup = fetch(BASE + "/")
    if soup:
        # 找首頁內所有連結與標題（含活動資訊）
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            href = a.get("href", "")
            if len(t) < 5 or len(t) > 300: continue
            if any(skip in t for skip in ["加入","捐款","入會","章程","理監","投稿"]): continue
            if any(k in t for k in ["年會","研討","課程","活動","訓練","公告","甄審","災難"]):
                date = extract_all_dates(t)
                out.append(mk(t, "台灣災難醫學會", "台灣學會",
                              href if href.startswith("http") else BASE + href,
                              None, date[-1] if date else None))
        # 解析首頁正文中的活動文字（h2~h4 含日期）
        for tag in soup.select("h2, h3, h4, p, li"):
            t = clean(tag.get_text())
            if len(t) < 10 or len(t) > 300: continue
            dates = extract_all_dates(t)
            if dates and any(k in t for k in ["年會","研討","課程","活動","訓練","公告","甄審"]):
                a_in = tag.find("a")
                href = resolve(a_in.get("href",""), BASE) if a_in else BASE
                out.append(mk(t, "台灣災難醫學會", "台灣學會",
                              href, None, dates[-1]))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_twparamedicine():
    """台灣醫療救護學會 — 課程與最新消息"""
    out = []
    BASE = "https://twparamedicine.org"
    # 課程頁面
    for path, itype in [
        ("/training.html", "course"),
        ("/index.html", None),
        ("/news.html", "news"),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for row in soup.select("article, .post, li, tr"):
            a = row.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 200: continue
            # 過濾導覽連結
            if any(skip in t for skip in ["關於","聯絡","首頁","登入","English"]): continue
            date = extract_all_dates(row.get_text())
            out.append(mk(t, "台灣醫療救護學會", "台灣學會",
                          resolve(a.get("href",""), BASE), itype,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:30]

def scrape_taemsp():
    """台灣緊急救護醫療指導醫師學會"""
    out = []
    BASE = "https://www.taemsp.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 120: continue
        href = a.get("href","")
        # 跳過導覽連結
        if any(skip in t for skip in ["關於","About","聯絡","Contact","首頁","Home","會員"]): continue
        date = extract_all_dates(t)
        out.append(mk(t, "台灣緊急救護醫療指導醫師學會", "台灣學會",
                      resolve(href, BASE), None,
                      date[-1] if date else None))
    return out[:20]

def scrape_simulation():
    """台灣急重症模擬醫學會"""
    out = []
    BASE = "https://simulation.org.tw"
    soup = fetch(BASE + "/")
    if not soup: return out
    # 抓文章連結
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 120: continue
        href = a.get("href","")
        if any(k in href for k in ["/20","/news","/activity","?p="]):
            date = extract_all_dates(t)
            out.append(mk(t, "台灣急重症模擬醫學會", "台灣學會",
                          resolve(href, BASE), None,
                          date[-1] if date else None))
    return out[:20]

def scrape_emt():
    """中華緊急救護技術員協會 — 課程與公告"""
    out = []
    BASE = "https://www.emt.org.tw/temtaf"
    # 課程列表
    for path, itype in [
        ("/LeCourseList", "course"),
        ("/LeCourseList?course=EMT-1", "course"),
        ("/LeCourseList?course=EMT-2", "course"),
        ("/LeCourseList?course=EMTP", "course"),
        ("/LeCourseList?course=BLS", "course"),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for row in soup.select("table tr"):
            a = row.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            date = extract_all_dates(row.get_text())
            out.append(mk(t, "中華緊急救護技術員協會", "台灣協會",
                          resolve(a.get("href",""), BASE), itype,
                          date[-1] if date else None))
        time.sleep(0.5)
    # 公告列表
    for path in ["/GpBulletinList", "/GpNewsList"]:
        soup2 = fetch(BASE + path)
        if not soup2: continue
        for row in soup2.select("table tr"):
            a = row.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            date = extract_all_dates(row.get_text())
            out.append(mk(t, "中華緊急救護技術員協會", "台灣協會",
                          resolve(a.get("href",""), BASE), "news",
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:40]

def scrape_taiwanwma():
    """台灣野外緊急救護協會
    網站為 Vue SPA，活動資料硬編碼於 JS bundle（index-Cppx5rTh.js），無後端 API。
    直接對應網站實際的 6 筆活動，連結指向各活動詳細頁。
    """
    BASE = "https://taiwanwma.org"
    ACTIVITIES_PAGE = BASE + "/activities"

    # 對應網站 JS 中 ws = ae([...]) 的 6 筆活動
    activities = [
        {
            "id": 1,
            "title": "台灣野外緊急救護協會 Facebook 粉絲專頁公告",
            "type": "news",
            "date": None,
        },
        {
            "id": 2,
            "title": "野編公告｜協會 LINE 官方帳號、Threads、WEMS 課程整合通知",
            "type": "news",
            "date": None,
        },
        {
            "id": 3,
            "title": "2022年高山PAC攜帶型加壓艙建置計畫 相關媒體報導",
            "type": "news",
            "date": None,
        },
        {
            "id": 4,
            "title": "115年度 PAC攜帶型加壓艙操作者認證課程 報名開放中",
            "type": "course",
            "date": "2026-01-01",
        },
        {
            "id": 5,
            "title": "115年度 BLS基本救命術暨野外活動常見傷害與急救密集訓練班 報名開放中",
            "type": "course",
            "date": "2026-01-01",
        },
        {
            "id": 6,
            "title": "115年度 WMAI國際野外急救課程（WFA / WAFA / Bridge to WFR）報名開放中",
            "type": "course",
            "date": "2026-01-01",
        },
    ]

    out = []
    for act in activities:
        url = f"{ACTIVITIES_PAGE}?activity={act['id']}"
        out.append(mk(
            act["title"],
            "台灣野外緊急救護協會",
            "台灣協會",
            url,
            act["type"],
            date=act["date"],
        ))
    return out

def scrape_tsorcc():
    """台灣復甦照護學會（www.tsorcc.org.tw，舊 tsorcc.org 已失效）"""
    out = []
    BASE = "https://www.tsorcc.org.tw"
    soup = fetch(BASE + "/%E5%AD%B8%E8%A1%93%E6%B4%BB%E5%8B%95")  # 學術活動頁
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 120: continue
        if any(k in t for k in ["課程","CPR","復甦","活動","研討","訓練","公告","消息"]):
            date = extract_all_dates(t)
            out.append(mk(t, "復甦照護小學堂", "台灣急救社群",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:20]

def scrape_burn():
    """臺灣燒傷暨傷口照護學會（www.burn.org.tw，www.burnwound.org.tw 已失效）"""
    out = []
    BASE = "https://www.burn.org.tw"
    soup = fetch(BASE + "/index.php/news")  # 最新消息頁
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 120: continue
        if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","年會"]):
            date = extract_all_dates(t)
            out.append(mk(t, "臺灣燒傷暨傷口照護學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:20]

def scrape_tsamairway():
    """台灣呼吸道處理醫學會"""
    out = []
    BASE = "https://www.tsamairway.org.tw"
    for path, itype in [
        ("/news/index.asp", "news"),
        ("/activity/index.asp", "course"),
        ("/", None),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            if any(skip in t for skip in ["關於","聯絡","首頁","登入","English"]): continue
            if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","年會","呼吸道"]):
                date = extract_all_dates(t)
                out.append(mk(t, "台灣呼吸道處理醫學會", "台灣學會",
                              resolve(a.get("href",""), BASE), itype,
                              date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_tana():
    """台灣麻醉專科護理學會"""
    out = []
    BASE = "https://www.tana.org.tw"
    for path, itype in [
        ("/news/index.asp", "news"),
        ("/activity/index.asp", "course"),
        ("/", None),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            if any(skip in t for skip in ["關於","聯絡","首頁","登入","English"]): continue
            if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","年會","麻醉"]):
                date = extract_all_dates(t)
                out.append(mk(t, "台灣麻醉專科護理學會", "台灣學會",
                              resolve(a.get("href",""), BASE), itype,
                              date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_twdmat():
    """台灣災難醫療隊發展協會"""
    out = []
    BASE = "https://twdmat.org"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 120: continue
        if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","DMAT"]):
            date = extract_all_dates(t)
            out.append(mk(t, "台灣災難醫療隊發展協會", "台灣協會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:20]

def scrape_naemt():
    """NAEMT — TCCC/TECC 課程"""
    out = []
    BASE = "https://www.naemt.org"
    soup = fetch(BASE + "/education/trauma-education")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 5: continue
        if any(k in t.upper() for k in ["TCCC","TECC","PHTLS","AMLS","EPC","COURSE","TRAINING"]):
            out.append(mk(t, "NAEMT", "戰術救護",
                          resolve(a.get("href",""), BASE), "course"))
    return out[:15]

def scrape_erc():
    """ERC — European Resuscitation Council"""
    out = []
    BASE = "https://www.erc.edu"
    for path in ["/news", "/education", "/courses"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 120: continue
            href = a.get("href","")
            if any(k in href.lower() for k in ["/news/","/event","/course"]):
                parent = a.find_parent(["article","li","div"])
                date = extract_all_dates(parent.get_text() if parent else "")
                out.append(mk(t, "ERC", "國際期刊/組織",
                              resolve(href, BASE), None,
                              date[-1] if date else None))
    return out[:15]

def scrape_wms():
    """Wilderness Medical Society"""
    out = []
    BASE = "https://wms.org"
    for path in ["/events", "/courses", "/education"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 120: continue
            if any(k in t.lower() for k in ["course","conference","event","workshop","training"]):
                parent = a.find_parent(["article","li","div"])
                date = extract_all_dates(parent.get_text() if parent else "")
                out.append(mk(t, "Wilderness Medical Society", "國際期刊/組織",
                              resolve(a.get("href",""), BASE), "course",
                              date[-1] if date else None))
    return out[:10]


def scrape_tsccm():
    """中華民國重症醫學會 — 正確頁面結構"""
    out = []
    BASE = "https://www.tsccm.org.tw"
    COURSE_BASE = "http://course.tsccm.org.tw"
    # 1. 學術活動：其他學會申請重症學分課程（有日期/標題結構）
    soup = fetch(BASE + "/Academic/index_other.asp")
    if soup:
        # 每筆課程在 table 中，格式：日期 / 主辦單位 / 課程名稱 / 地點 / 學分
        for row in soup.select("table tr"):
            cols = row.find_all("td")
            if len(cols) < 2: continue
            row_text = row.get_text()
            date = extract_all_dates(row_text)
            # 找課程名稱（通常是最長的欄位）
            title_col = max(cols, key=lambda c: len(c.get_text().strip()), default=None)
            if not title_col: continue
            a = title_col.find("a")
            t = clean(a.get_text() if a else title_col.get_text())
            if len(t) < 8 or len(t) > 200: continue
            href = resolve(a.get("href","") if a else "", BASE)
            out.append(mk(t, "中華民國重症醫學會", "台灣學會",
                          href, "course", date[-1] if date else None))
    # 2. 訊息公告（國內外相關訊息）
    soup2 = fetch(COURSE_BASE + "/news/df_list.asp")
    if soup2:
        for row in soup2.select("table tr, .news-item, li"):
            a = row.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 200: continue
            date = extract_all_dates(row.get_text())
            out.append(mk(t, "中華民國重症醫學會", "台灣學會",
                          resolve(a.get("href",""), COURSE_BASE), "news",
                          date[-1] if date else None))
    # 去重
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:40]

def scrape_sgecm():
    """台灣老人急重症醫學會 — Big5 ASP 網站，爬列表+詳細頁取截止日"""
    import re as _re
    out = []
    BASE = "http://www.sgecm.org.tw"  # https 無效，Big5 編碼

    def parse_sgecm_detail(detail_url):
        """從詳細頁取出：活動日期、截止日期"""
        soup_d = fetch(detail_url)  # fetch 自動用 big5
        if not soup_d: return None, None
        text = soup_d.get_text(" ", strip=True)
        # 活動時間格式：YYYY/MM/DD HH:MM ~ ...
        event_m = _re.search(r"(\d{4})[/.](\d{1,2})[/.](\d{1,2})\s*\d{2}:\d{2}\s*[~～]", text)
        event_date = None
        if event_m:
            y,mo,d = int(event_m.group(1)),int(event_m.group(2)),int(event_m.group(3))
            if 2020 <= y <= 2035:
                event_date = f"{y:04d}-{mo:02d}-{d:02d}"
        # 截止日格式：多種關鍵字
        deadline = None
        for kw_pat in [
            r"(\d{4})年(\d{1,2})月(\d{1,2})日.{0,10}(?:前|截止|以前)",
            r"(?:報名截止|截止日期?|報名至|報名期限)[^\d]{0,10}(\d{4})[/.](\d{1,2})[/.](\d{1,2})",
            r"(\d{4})[/.](\d{1,2})[/.](\d{1,2}).{0,5}(?:前|截止|以前)",
        ]:
            m = _re.search(kw_pat, text)
            if m:
                try:
                    y,mo,d = int(m.group(1)),int(m.group(2)),int(m.group(3))
                    if 2020 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31:
                        candidate = f"{y:04d}-{mo:02d}-{d:02d}"
                        # 截止日應早於或等於活動日
                        if event_date is None or candidate <= event_date:
                            deadline = candidate
                            break
                except: pass
        return event_date, deadline

    # ── 課程活動列表 ──────────────────────────────────────
    for list_url, itype in [
        (BASE + "/educate/edu_list.asp?SPONSERFLAG=0", "course"),   # 本會學術活動
        (BASE + "/educate/edu_list1.asp?SPONSERFLAG=1", "course"),  # 其他學術活動
    ]:
        soup = fetch(list_url)
        if not soup: continue
        for row in soup.select("table tr"):
            cols = row.find_all("td")
            if len(cols) < 3: continue
            # 欄結構：日期 | 代號 | 活動名稱（含連結）
            # 先從列表頁取粗略日期
            date_text = clean(cols[0].get_text())
            rough_date = extract_all_dates(date_text)
            rough_date = rough_date[-1] if rough_date else None
            # 標題與連結在第3欄（index 2）
            a = cols[2].find("a") if len(cols) > 2 else None
            t = clean(a.get_text() if a else cols[2].get_text())
            if len(t) < 5 or len(t) > 300: continue
            if a:
                href = resolve(a.get("href",""), BASE)
                # 讀詳細頁取精確日期與截止日
                event_date, deadline = parse_sgecm_detail(href)
                time.sleep(0.4)
            else:
                href = BASE
                event_date, deadline = rough_date, None
            out.append(mk(t, "台灣老人急重症醫學會", "台灣學會",
                          href, itype,
                          date=event_date or rough_date,
                          deadline=deadline))

    # ── 最新消息 ──────────────────────────────────────────
    for news_url, news_type in [
        (BASE + "/news/news_list.asp?NType=1", "news"),  # 學會消息
        (BASE + "/news/news_list.asp?NType=3", "news"),  # 秘書處消息
        (BASE + "/news/news_list.asp?NType=4", "news"),  # 積分公告
    ]:
        soup2 = fetch(news_url)
        if not soup2: continue
        for row in soup2.select("table tr"):
            a = row.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            # 跳過導覽連結
            href = a.get("href","")
            if not href or "news_list" in href or href.startswith("/")==False and "sgecm" not in href: 
                pass
            date = extract_all_dates(row.get_text())
            out.append(mk(t, "台灣老人急重症醫學會", "台灣學會",
                          resolve(href, BASE), news_type,
                          date[-1] if date else None))

    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen and len(it.get("title","")) > 3:
            seen.add(it["id"])
            unique.append(it)
    return unique[:30]

def scrape_tamis():
    """台灣心肌梗塞學會"""
    out = []
    BASE = "https://tamis.org.tw"
    # 學術活動列表頁
    for path in ["/activity", "/news", "/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href*='/activity/'], a[href*='/news/'], h2 a, h3 a, .entry-title a"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            if len(t) < 5 or len(t) > 150: continue
            parent = a.find_parent(["li","div","article"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "台灣心肌梗塞學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_tebma():
    """台灣實證醫學學會"""
    out = []
    BASE = "https://www.tebma.org.tw"
    # 課程報名列表
    for path in ["/seminar/list", "/event/list", "/news/list", "/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href*='/event/'], a[href*='/seminar/'], h2 a, h3 a, .entry-title a, a[href*='/news/']"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            parent = a.find_parent(["li","div","article","tr"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "台灣實證醫學學會", "台灣學會",
                          resolve(a.get("href",""), BASE), "course",
                          date[-1] if date else None))
    return out[:20]

def scrape_tmed():
    """臺灣醫事繼續教育學會 — 正確網址 www.tmed.com.tw"""
    out = []
    BASE = "https://www.tmed.com.tw"
    # 課程列表頁
    for path, itype in [
        ("/site_item_list_1.php", "course"),   # 課程活動
        ("/", None),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 200: continue
            if any(skip in t for skip in ["登入","登出","首頁","關於","聯絡","隱私","服務"]): continue
            if any(k in t for k in ["課程","活動","研討","訓練","工作坊","報名","公告","消息","護理","醫事"]):
                date = extract_all_dates(t)
                out.append(mk(t, "臺灣醫事繼續教育學會", "台灣學會",
                              resolve(a.get("href",""), BASE), itype,
                              date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_medinfo():
    """台灣醫學資訊學會"""
    out = []
    BASE = "https://www.medinfo.org.tw"
    # JCMIT 2026 研討會已知
    out.append(mk("JCMIT 2026 國際醫學資訊聯合研討會", "台灣醫學資訊學會", "台灣學會",
                  "https://jcmit2026.medinfo.org.tw/", "course", "2026-12-19"))
    soup = fetch(BASE + "/")
    if soup:
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","年會"]):
                date = extract_all_dates(t)
                out.append(mk(t, "台灣醫學資訊學會", "台灣學會",
                              resolve(a.get("href",""), BASE), None,
                              date[-1] if date else None))
    return out[:20]

def scrape_simulation_v2():
    """台灣急重症模擬醫學會 — WordPress 結構"""
    out = []
    BASE = "https://simulation.org.tw"
    # 最新消息列表（WordPress 文章列表）
    for path, itype in [
        ("/?cat=2", "course"),   # 學術活動分類
        ("/?cat=1", "news"),     # 最新消息分類
        ("/", None),             # 首頁
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        # WordPress 標準文章連結
        for a in soup.select("h2 a, h3 a, .entry-title a, .post-title a"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            parent = a.find_parent(["article","div","li"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "台灣急重症模擬醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), itype,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:25]

def scrape_tafm():
    """台灣家庭醫學醫學會 — 課程與消息"""
    out = []
    BASE = "https://www.tafm.org.tw"
    # 課程列表
    for path, itype in [
        ("/ehc-tafm/s/w/edu/scheduleInfo1", "course"),    # 教育課程列表
        ("/ehc-tafm/s/w/news_news/articleList", "news"),  # 最新消息
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        # 表格或列表結構
        for row in soup.select("tr, li, .list-item, article"):
            a = row.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            date = extract_all_dates(row.get_text())
            out.append(mk(t, "台灣家庭醫學醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), itype,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:30]

def scrape_sfast():
    """台灣運動安全暨急救技能推廣協會"""
    out = []
    BASE = "https://www.sfast.org"
    for path, itype in [
        ("/index.php?option=com_content&view=category&layout=blog&id=8&Itemid=108&lang=tw", "course"),
        ("/index.php?option=com_content&view=category&layout=blog&id=9&Itemid=109&lang=tw", "news"),
        ("/index.php?lang=tw", None),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 200: continue
            if any(skip in t for skip in ["關於","聯絡","首頁","登入","更多","English"]): continue
            if any(k in t for k in ["課程","活動","訓練","工作坊","公告","消息","AED","急救","研討"]):
                date = extract_all_dates(t)
                out.append(mk(t, "台灣運動安全暨急救技能推廣協會", "台灣協會",
                              resolve(a.get("href",""), BASE), itype,
                              date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_dpac():
    """彰化縣防災協會（DPAC）— WordPress"""
    out = []
    BASE = "https://www.dpac.org.tw"
    for path, itype in [
        ("/category/課程/", "course"),
        ("/category/活動/", "course"),
        ("/category/公告/", "news"),
        ("/", None),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("h2 a, h3 a, .entry-title a, article h2 a, .post-title a"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            parent = a.find_parent(["article","div"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "彰化縣防災協會（DPAC）", "台灣協會",
                          resolve(a.get("href",""), BASE), itype,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_webtema():
    """台灣緊急應變管理協會 — WordPress（webtema.org，無 www）"""
    out = []
    BASE = "https://webtema.org"
    for path, itype in [
        ("/category/course/", "course"),
        ("/category/news/", "news"),
        ("/category/event/", "course"),
        ("/", None),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("h2 a, h3 a, .entry-title a, article h2 a"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            parent = a.find_parent(["article","div"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "台灣緊急應變管理協會", "台灣協會",
                          resolve(a.get("href",""), BASE), itype,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_anne():
    """安妮怎麼了 — 主站 www.anne.education + 課程平台 school.anne.education"""
    out = []
    MAIN  = "https://www.anne.education"
    SCHOOL = "https://school.anne.education"
    # 主站：抓課程報名連結與最新活動
    for url, itype in [
        (MAIN + "/flippedcpr.html", "course"),   # 翻轉急救 CPR+AED
        (MAIN + "/",                None),        # 首頁
    ]:
        soup = fetch(url)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            href = a.get("href","")
            if len(t) < 5 or len(t) > 250: continue
            if any(skip in t for skip in ["關於","捐款","聯絡","登入","首頁","English"]): continue
            if any(k in t for k in ["課程","CPR","AED","急救","訓練","活動","報名","實體"]):
                date = extract_all_dates(t)
                out.append(mk(t, "安妮怎麼了", "台灣急救社群",
                              href if href.startswith("http") else MAIN + href,
                              itype, date[-1] if date else None))
    # 課程平台：列出實體報名課程
    soup2 = fetch(SCHOOL + "/courses/cpraedpractice/")
    if soup2:
        for a in soup2.select("a[href]"):
            t = clean(a.get_text())
            href = a.get("href","")
            if len(t) < 5 or len(t) > 250: continue
            if any(k in t for k in ["報名","實體","課程","CPR","AED","場次"]):
                date = extract_all_dates(t)
                out.append(mk(t, "安妮怎麼了", "台灣急救社群",
                              href if href.startswith("http") else SCHOOL + href,
                              "course", date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]


def scrape_mgems():
    """中華民國大型活動緊急救護協會 — WordPress（www.mgems.org）"""
    out = []
    BASE = "https://www.mgems.org"
    for path, itype in [
        ("/category/on-line-checkin/", "course"),  # 線上報名頁（含各課程）
        ("/category/ems-course/", "course"),        # EMS課程分類
        ("/", None),                                # 首頁（含最新活動）
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("h2 a, h3 a, .entry-title a, article h2 a, article h3 a"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            parent = a.find_parent(["article","div"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "中華民國大型活動緊急救護協會", "台灣協會",
                          resolve(a.get("href",""), BASE), itype,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:30]

def scrape_ntuch():
    """臺大兒童醫院 — 最新消息（www.ntuh.gov.tw/ntuch）"""
    out = []
    BASE = "https://www.ntuh.gov.tw"
    for path, itype in [
        ("/ntuch/News.action", "news"),    # 最新消息
        ("/ntuch/Index.action", None),     # 首頁
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 200: continue
            if any(skip in t for skip in ["掛號","交通","地圖","聯絡","English","醫療團隊","醫師查詢","就醫"]): continue
            if any(k in t for k in ["課程","活動","研討","訓練","公告","消息","兒科","急診","CPR","PALS","NRP"]):
                date = extract_all_dates(t)
                out.append(mk(t, "臺大兒童醫院", "台灣急救社群",
                              resolve(a.get("href",""), BASE), itype,
                              date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:15]

def scrape_hear_aed():
    """臺灣愛陌生安全推廣協會（聽見你我的AED）"""
    out = []
    BASE = "https://www.hear-aed.com.tw"
    for path in ["/", "/news", "/course", "/event"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            if any(k in t for k in ["課程","活動","研討","訓練","公告","消息","AED","CPR","急救"]):
                date = extract_all_dates(t)
                out.append(mk(t, "臺灣愛陌生安全推廣協會", "台灣急救社群",
                              resolve(a.get("href",""), BASE), None,
                              date[-1] if date else None))
    return out[:15]

def scrape_tsn():
    """台灣新生兒科醫學會 — WordPress"""
    out = []
    BASE = "https://tsn-neonatology.com"
    for path, itype in [
        ("/category/活動課程/", "course"),
        ("/category/最新消息/", "news"),
        ("/", None),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("h2 a, h3 a, .entry-title a, article h2 a"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            parent = a.find_parent(["article","div"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "台灣新生兒科醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), itype,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_sma():
    """台灣運動醫學學會 — 舊式 ASP 網站"""
    out = []
    BASE = "https://www.sma.org.tw"
    for path, itype in [
        ("/news_list.asp", "news"),
        ("/activity_list.asp", "course"),
        ("/", None),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for row in soup.select("table tr, li, .list-item"):
            a = row.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            if any(skip in t for skip in ["關於","聯絡","首頁","登入","相關連結"]): continue
            date = extract_all_dates(row.get_text())
            out.append(mk(t, "台灣運動醫學學會", "台灣學會",
                          resolve(a.get("href",""), BASE), itype,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_tasm():
    """台灣運動醫學醫學會"""
    out = []
    BASE = "https://www.tasm.org.tw"
    for path, itype in [
        ("/news/list.asp", "news"),
        ("/activity/list.asp", "course"),
        ("/course/list.asp", "course"),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for row in soup.select("table tr, li, .list-item"):
            a = row.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            date = extract_all_dates(row.get_text())
            out.append(mk(t, "台灣運動醫學醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), itype,
                          date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:25]

def scrape_pediatr():
    """臺灣兒科醫學會"""
    out = []
    BASE = "https://www.pediatr.org.tw"
    # 學會公告列表（已確認結構）
    for path, itype in [
        ("/news/news_list.asp", "news"),
        ("/activity/activity_list.asp", "course"),
        ("/", None),
    ]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 200: continue
            href = a.get("href","")
            if any(skip in t for skip in ["關於","聯絡","首頁","登入","English","常見問題"]): continue
            if any(k in href for k in ["news_info","activity_info","course"]) or                any(k in t for k in ["課程","活動","研討","訓練","公告","消息","年會"]):
                parent = a.find_parent(["tr","li","td","div"])
                date = extract_all_dates(parent.get_text() if parent else t)
                out.append(mk(t, "臺灣兒科醫學會", "台灣學會",
                              resolve(href, BASE), itype,
                              date[-1] if date else None))
    seen, unique = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique[:20]

def scrape_aha():
    """AHA — American Heart Association"""
    out = []
    BASE = "https://www.heart.org"
    soup = fetch(BASE + "/en/cpr/center-for-cpr-training")
    if soup:
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            if any(k in t.lower() for k in ["cpr","bls","acls","pals","course","training","class"]):
                out.append(mk(t, "AHA — American Heart Association", "國際期刊/組織",
                              resolve(a.get("href",""), BASE), "course"))
    return out[:15]

def scrape_itls():
    """ITLS — International Trauma Life Support"""
    out = []
    BASE = "https://www.itrauma.org"
    soup = fetch(BASE + "/education/")
    if not soup:
        soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 5 or len(t) > 150: continue
        if any(k in t.lower() for k in ["course","training","workshop","conference","education","itls"]):
            out.append(mk(t, "ITLS", "國際期刊/組織",
                          resolve(a.get("href",""), BASE), "course"))
    return out[:15]

def scrape_jema():
    """JAMA — 最新文章"""
    out = []
    soup = fetch("https://jamanetwork.com/journals/jama/issue/current")
    if not soup: return out
    for a in soup.select("a[href*='/journals/jama/fullarticle']"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 200: continue
        out.append(mk(t, "JAMA", "國際期刊/組織",
                      resolve(a.get("href",""), "https://jamanetwork.com"), "news"))
    return out[:10]

def scrape_jems_news():
    """JEMS — Journal of Emergency Medical Services"""
    out = []
    BASE = "https://www.jems.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        href = a.get("href","")
        if any(k in href for k in ["/articles/","/news/","/training/"]):
            out.append(mk(t, "JEMS", "國際期刊/組織",
                          resolve(href, BASE), None))
    return out[:15]

def scrape_emsworld_news():
    """EMS World"""
    out = []
    BASE = "https://www.emsworld.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("h2 a, h3 a, .article-title a, .entry-title a"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        out.append(mk(t, "EMS World", "資料庫/媒體",
                      resolve(a.get("href",""), BASE), "news"))
    return out[:15]

def scrape_ems1_news():
    """EMS1"""
    out = []
    BASE = "https://www.ems1.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        href = a.get("href","")
        if any(k in href for k in ["/ems-news/","/ems-products/","/training/","/articles/"]):
            out.append(mk(t, "EMS1", "資料庫/媒體",
                          resolve(href, BASE), "news"))
    return out[:15]

def scrape_foamfrat_news():
    """FOAMfrat"""
    out = []
    BASE = "https://www.foamfrat.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("h2 a, h3 a, .entry-title a, article a"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        out.append(mk(t, "FOAMfrat", "資料庫/媒體",
                      resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_tml_news():
    """The Medical Lounge"""
    out = []
    BASE = "https://www.themedicallounge.co.uk"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("h2 a, h3 a, .entry-title a, article a"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        out.append(mk(t, "The Medical Lounge", "資料庫/媒體",
                      resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_wem():
    """World Extreme Medicine"""
    out = []
    BASE = "https://worldextrememedicine.com"
    for path in ["/events", "/courses", "/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            if any(k in t.lower() for k in ["course","conference","event","expedition","training","workshop"]):
                parent = a.find_parent(["article","li","div"])
                date = extract_all_dates(parent.get_text() if parent else t)
                out.append(mk(t, "World Extreme Medicine", "國際期刊/組織",
                              resolve(a.get("href",""), BASE), "course",
                              date[-1] if date else None))
    return out[:10]

def scrape_ctecc():
    """C-TECC"""
    out = []
    BASE = "https://www.c-tecc.org"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t.lower() for k in ["guideline","update","news","tecc","course","training","publication"]):
            out.append(mk(t, "C-TECC", "戰術救護",
                          resolve(a.get("href",""), BASE), None))
    return out[:10]

def scrape_cbrn():
    """CBRN Professionals"""
    out = []
    BASE = "https://www.cbrnprofessionals.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t.lower() for k in ["news","training","course","event","article","update","cbrn"]):
            out.append(mk(t, "CBRN Professionals", "戰術救護",
                          resolve(a.get("href",""), BASE), None))
    return out[:10]

def scrape_soma():
    """SOMA — Special Operations Medical Association（specialoperationsmedicine.org）"""
    out = []
    BASE = "https://specialoperationsmedicine.org"
    for path in ["/events", "/news", "/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            if any(k in t.lower() for k in ["conference","course","event","training","symposium","jsom","news"]):
                date = extract_all_dates(t)
                out.append(mk(t, "SOMA", "戰術救護",
                              resolve(a.get("href",""), BASE), None,
                              date[-1] if date else None))
    return out[:10]

def scrape_ngcm():
    """Next Generation Combat Medic（nextgencombatmedic.com，舊網址 ngcombatmedic.com 已失效）"""
    out = []
    BASE = "https://nextgencombatmedic.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("h2 a, h3 a, .entry-title a, article a"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        out.append(mk(t, "Next Generation Combat Medic", "戰術救護",
                      resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_nar():
    """North American Rescue"""
    out = []
    BASE = "https://www.narescue.com"
    soup = fetch(BASE + "/pages/news")
    if not soup:
        soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t.lower() for k in ["news","update","product","training","article"]):
            out.append(mk(t, "North American Rescue", "戰術救護",
                          resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_tacmed():
    """TacMed Solutions"""
    out = []
    BASE = "https://www.tacmedsolutions.com"
    soup = fetch(BASE + "/blogs/news")
    if not soup:
        soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("h2 a, h3 a, .article__title a, a.blog-item__title"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        out.append(mk(t, "TacMed Solutions", "戰術救護",
                      resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_trilogy():
    """Trilogy EMS"""
    out = []
    BASE = "https://www.trilogyems.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t.lower() for k in ["course","training","class","event","news","update"]):
            out.append(mk(t, "Trilogy EMS", "戰術救護",
                          resolve(a.get("href",""), BASE), None))
    return out[:10]


def scrape_aemta():
    """American EMT Academy"""
    out = []
    BASE = "https://americanemtacademy.com"
    # 課程頁面（有明確日期）
    for path in ["/emt-classes/", "/orange-county-emt-school/", "/evening-emt-program-interest-form/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            if any(k in t.lower() for k in ["emt","course","class","training","cpr","bls"]):
                parent = a.find_parent(["div","li","p"])
                date = extract_all_dates(parent.get_text() if parent else t)
                out.append(mk(t, "American EMT Academy", "國際期刊/組織",
                              resolve(a.get("href",""), BASE), "course",
                              date[-1] if date else None))
    # 主頁最新消息
    soup2 = fetch(BASE + "/")
    if soup2:
        for a in soup2.select("h2 a, h3 a, .entry-title a"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            out.append(mk(t, "American EMT Academy", "國際期刊/組織",
                          resolve(a.get("href",""), BASE), "news"))
    return out[:15]

def scrape_bmj():
    """BMJ Journals"""
    out = []
    BASE = "https://journals.bmj.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        href = a.get("href","")
        if any(k in href.lower() for k in ["/content/","/news/","/article"]):
            out.append(mk(t, "BMJ Journals", "國際期刊/組織",
                          resolve(href, BASE), "news"))
    return out[:10]

def scrape_emj():
    """Emergency Medicine Journal"""
    out = []
    BASE = "https://emj.bmj.com"
    soup = fetch(BASE + "/content/current")
    if not soup:
        soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href*='/content/']"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 200: continue
        out.append(mk(t, "Emergency Medicine Journal", "國際期刊/組織",
                      resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_jem():
    """Journal of Emergency Medicine"""
    out = []
    BASE = "https://www.jem-journal.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 200: continue
        href = a.get("href","")
        if any(k in href.lower() for k in ["/article/","/issues/","/S0736-"]):
            out.append(mk(t, "Journal of Emergency Medicine", "國際期刊/組織",
                          resolve(href, BASE), "news"))
    return out[:10]

def scrape_jtacs():
    """JTACS — Journal of Trauma and Acute Care Surgery"""
    out = []
    BASE = "https://journals.lww.com"
    soup = fetch(BASE + "/jtrauma/pages/currenttoc.aspx")
    if not soup:
        soup = fetch(BASE + "/jtrauma/pages/default.aspx")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 200: continue
        href = a.get("href","")
        if any(k in href.lower() for k in ["/fulltext/","/abstract/","DOI"]):
            out.append(mk(t, "JTACS", "國際期刊/組織",
                          resolve(href, BASE), "news"))
    return out[:10]

def scrape_nejm():
    """NEJM — New England Journal of Medicine"""
    out = []
    BASE = "https://www.nejm.org"
    soup = fetch(BASE + "/toc/nejm/medical-journal/")
    if not soup:
        soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href*='/doi/']"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 200: continue
        out.append(mk(t, "NEJM", "國際期刊/組織",
                      resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_resus():
    """Resuscitation Journal"""
    out = []
    soup = fetch("https://www.resuscitationjournal.com/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 200: continue
        href = a.get("href","")
        if any(k in href.lower() for k in ["/article/","/issue/","/S0300-"]):
            out.append(mk(t, "Resuscitation Journal", "國際期刊/組織",
                          resolve(href, "https://www.resuscitationjournal.com"), "news"))
    return out[:10]

def scrape_cotccc():
    """CoTCCC — CoTCCC 指南在 deployedmedicine.com"""
    out = []
    BASE = "https://deployedmedicine.com"
    for path in ["/market/cotccc", "/cotccc"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            if any(k in t.lower() for k in ["guideline","update","tccc","recommendation","cpg","change","news"]):
                out.append(mk(t, "CoTCCC", "戰術救護",
                              resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_crisis():
    """Crisis Medicine"""
    out = []
    BASE = "https://www.crisismedicine.com"
    for path in ["/", "/courses", "/news", "/events"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("h2 a, h3 a, .entry-title a, article a, a[href*='/course'], a[href*='/event']"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            date = extract_all_dates(t)
            out.append(mk(t, "Crisis Medicine", "戰術救護",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:10]

def scrape_ctoms():
    """CTOMS"""
    out = []
    BASE = "https://ctoms.ca"
    for path in ["/", "/courses", "/training", "/news"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            if any(k in t.lower() for k in ["course","training","workshop","news","update","tccc","tactical"]):
                date = extract_all_dates(t)
                out.append(mk(t, "CTOMS", "戰術救護",
                              resolve(a.get("href",""), BASE), None,
                              date[-1] if date else None))
    return out[:10]

def scrape_deployed():
    """Deployed Medicine"""
    out = []
    BASE = "https://deployedmedicine.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t.lower() for k in ["tccc","guideline","course","update","training","module","news"]):
            out.append(mk(t, "Deployed Medicine", "戰術救護",
                          resolve(a.get("href",""), BASE), None))
    return out[:10]

def scrape_jts():
    """JTS — Joint Trauma System"""
    out = []
    BASE = "https://jts.health.mil"
    soup = fetch(BASE + "/index.cfm/PI_CPGs/cpgs")
    if not soup:
        soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t.lower() for k in ["cpg","guideline","update","clinical","trauma","tccc"]):
            out.append(mk(t, "JTS — Joint Trauma System", "戰術救護",
                          resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_safeguard():
    """Safeguard Medical"""
    out = []
    BASE = "https://safeguardmedical.com"
    for path in ["/blogs/news", "/pages/news", "/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("h2 a, h3 a, .article__title a, .blog-item a"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            out.append(mk(t, "Safeguard Medical", "戰術救護",
                          resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_snakestaff():
    """Snakestaff Systems"""
    out = []
    BASE = "https://www.snakestaffsystems.com"
    for path in ["/blogs/news", "/pages/blog", "/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("h2 a, h3 a, .article__title a"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            out.append(mk(t, "Snakestaff Systems", "戰術救護",
                          resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_tacmedicine():
    """Tactical Medicine Training & Equipment"""
    out = []
    BASE = "https://www.tactical-medicine.com"
    for path in ["/blog", "/news", "/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("h2 a, h3 a, .entry-title a, article a"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            date = extract_all_dates(t)
            out.append(mk(t, "Tactical Medicine Training & Equipment", "戰術救護",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:10]

# ══════════════════════════════════════════════════════
# RSS
# ══════════════════════════════════════════════════════
def build_rss(items):
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    xml = ""
    for it in items[:60]:
        t = it["title"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        type_label = "【課程】" if it.get("type")=="course" else "【消息】"
        xml += f"""
  <item>
    <title>{type_label}{t}</title>
    <link>{it.get('url','')}</link>
    <guid>{it.get('url','') or it['id']}</guid>
    <pubDate>{it.get('date','')}</pubDate>
    <category>{it.get('source','')} - {it.get('cat','')}</category>
    <description>{type_label}{t}（來源：{it.get('source','')}）</description>
  </item>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>緊急救護資訊入口網 — 課程與最新消息</title>
  <link>https://emsipbyluke.netlify.app</link>
  <description>自動彙整台灣及國際急救相關單位課程與最新消息</description>
  <language>zh-TW</language>
  <lastBuildDate>{now}</lastBuildDate>
  <ttl>180</ttl>{xml}
</channel>
</rss>"""

# ══════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════
SCRAPERS = [
    # ── 台灣學會 ──
    ("台灣急診醫學會",              scrape_sem),
    ("台灣急救加護醫學會",          scrape_seccm),
    ("台灣外傷醫學會",              scrape_trauma),
    ("台灣災難醫學會",              scrape_disaster),
    ("台灣醫療救護學會",            scrape_twparamedicine),
    ("台灣緊急救護醫療指導醫師學會", scrape_taemsp),
    ("台灣急重症模擬醫學會",        scrape_simulation_v2),
    ("中華民國重症醫學會",          scrape_tsccm),
    ("台灣老人急重症醫學會",        scrape_sgecm),
    ("台灣心肌梗塞學會",            scrape_tamis),
    ("台灣實證醫學學會",            scrape_tebma),
    ("臺灣醫事繼續教育學會",        scrape_tmed),
    ("台灣醫學資訊學會",            scrape_medinfo),
    ("台灣家庭醫學醫學會",          scrape_tafm),
    ("臺灣燒傷暨傷口照護學會",      scrape_burn),
    ("台灣呼吸道處理醫學會",        scrape_tsamairway),
    ("台灣麻醉專科護理學會",        scrape_tana),
    ("台灣新生兒科醫學會",          scrape_tsn),
    ("台灣運動醫學學會",            scrape_sma),
    ("台灣運動醫學醫學會",          scrape_tasm),
    ("臺灣兒科醫學會",              scrape_pediatr),
    # ── 台灣協會 ──
    ("中華民國大型活動緊急救護協會", scrape_mgems),
    ("中華緊急救護技術員協會",      scrape_emt),
    ("台灣野外地區緊急救護協會",    scrape_taiwanwma),
    ("台灣災難醫療隊發展協會",      scrape_twdmat),
    ("台灣運動安全暨急救技能推廣協會", scrape_sfast),
    ("彰化縣防災協會",              scrape_dpac),
    ("台灣緊急應變管理協會",        scrape_webtema),
    # ── 台灣急救社群 ──
    ("復甦照護小學堂",              scrape_tsorcc),
    ("安妮怎麼了",                  scrape_anne),
    ("臺大兒童醫院",                scrape_ntuch),
    ("臺灣愛陌生安全推廣協會",      scrape_hear_aed),
    # ── 國際期刊/組織 ──
    ("AHA",                         scrape_aha),
    ("ITLS",                        scrape_itls),
    ("JAMA",                        scrape_jema),
    ("JEMS",                        scrape_jems_news),
    ("World Extreme Medicine",      scrape_wem),
    ("ERC",                         scrape_erc),
    ("Wilderness Medical Society",  scrape_wms),
    ("NAEMT",                       scrape_naemt),
    # ── 資料庫/媒體 ──
    ("EMS World",                   scrape_emsworld_news),
    ("EMS1",                        scrape_ems1_news),
    ("FOAMfrat",                    scrape_foamfrat_news),
    ("The Medical Lounge",          scrape_tml_news),
    # ── 戰術救護 ──
    ("C-TECC",                      scrape_ctecc),
    ("CBRN Professionals",          scrape_cbrn),
    ("CoTCCC",                      scrape_cotccc),
    ("Crisis Medicine",             scrape_crisis),
    ("CTOMS",                       scrape_ctoms),
    ("Deployed Medicine",           scrape_deployed),
    ("JTS",                         scrape_jts),
    ("SOMA",                        scrape_soma),
    ("Next Generation Combat Medic", scrape_ngcm),
    ("North American Rescue",       scrape_nar),
    ("Safeguard Medical",           scrape_safeguard),
    ("Snakestaff Systems",          scrape_snakestaff),
    ("Tactical Medicine",           scrape_tacmedicine),
    ("TacMed Solutions",            scrape_tacmed),
    ("Trilogy EMS",                 scrape_trilogy),
    # ── 補充國際期刊 ──
    ("American EMT Academy",        scrape_aemta),
    ("BMJ Journals",                scrape_bmj),
    ("Emergency Medicine Journal",  scrape_emj),
    ("Journal of Emergency Medicine", scrape_jem),
    ("JTACS",                       scrape_jtacs),
    ("NEJM",                        scrape_nejm),
    ("Resuscitation Journal",       scrape_resus),
]

MAX_PER_MONTH = 5  # 每個單位每月同類型最多5筆

def limit_per_source(results):
    """
    課程：同月份最多5筆，未來月份無上限全部保留
    消息：同月份最多5筆
    """
    from collections import defaultdict
    today = datetime.now().strftime("%Y-%m")

    # ── 課程 ──────────────────────────────────────────
    courses = [r for r in results if r.get("type") == "course"]
    future_courses = []
    month_bucket_c = defaultdict(list)
    for c in courses:
        d = (c.get("date") or "")[:7]
        if d and d > today:
            future_courses.append(c)   # 未來月份：無上限全保留
        else:
            month_bucket_c[d or "0000-00"].append(c)
    past_courses = []
    for mo, items in sorted(month_bucket_c.items(), reverse=True):
        past_courses.extend(items[:MAX_PER_MONTH])
    out_courses = future_courses + past_courses

    # ── 消息：同月份最多5筆 ───────────────────────────
    news = [r for r in results if r.get("type") == "news"]
    month_bucket_n = defaultdict(list)
    for n in news:
        d = (n.get("date") or "")[:7]
        month_bucket_n[d or "0000-00"].append(n)
    out_news = []
    for mo, items in sorted(month_bucket_n.items(), reverse=True):
        out_news.extend(items[:MAX_PER_MONTH])

    return out_courses + out_news

def main():
    print(f"\n{'='*55}")
    print(f"緊急救護課程爬蟲 v3  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"每單位：課程(同月最多{MAX_PER_MONTH}筆,未來無限) + 消息(同月最多{MAX_PER_MONTH}筆)")
    print(f"{'='*55}")

    all_items = []
    for name, fn in SCRAPERS:
        print(f"\n→ {name}")
        try:
            raw = fn()
            results = limit_per_source(raw)
            courses = [r for r in results if r.get("type")=="course"]
            news    = [r for r in results if r.get("type")=="news"]
            print(f"  課程:{len(courses)}  消息:{len(news)}  (原始:{len(raw)}筆)")
            all_items.extend(results)
        except Exception as e:
            print(f"  ❌ {e}")
        time.sleep(1.5)

    # 去重
    seen, unique = set(), []
    for it in all_items:
        if it["id"] not in seen and len(it.get("title","")) > 3:
            seen.add(it["id"])
            unique.append(it)

    # 過濾：過期資料只保留近3個月
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    filtered = []
    for it in unique:
        d = it.get("date")
        if not d:
            filtered.append(it)       # 無日期：保留
        elif d >= cutoff:
            filtered.append(it)       # 3個月內或未來：保留
        # 超過3個月的過期資料：捨棄
    unique = filtered

    # 排序：日期由近至遠（未來在上，無日期在最後）
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    def sort_key(x):
        d = x.get("date")
        if not d:
            return (2, "9999-99-99")   # 無日期排最後
        if d >= today:
            return (0, d)              # 未來/今天：由近至遠排最前
        else:
            return (1, d)              # 已過期（3個月內）：排中間，由近至遠
    unique.sort(key=sort_key)

    output = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "count":   len(unique),
        "course_count": sum(1 for i in unique if i.get("type")=="course"),
        "news_count":   sum(1 for i in unique if i.get("type")=="news"),
        "items": unique
    }

    OUTPUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ JSON: {len(unique)} 筆（課程:{output['course_count']} 消息:{output['news_count']}）")

    OUTPUT_RSS.write_text(build_rss(unique), encoding="utf-8")
    print(f"✅ RSS 已輸出")

if __name__ == "__main__":
    main()
