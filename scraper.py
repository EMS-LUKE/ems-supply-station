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
    deadline_kw = re.search(r"報名截止|截止日|deadline", title, re.I)
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

def fetch(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
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
    """台灣急診醫學會"""
    out = []
    BASE = "https://www.sem.org.tw"
    # 課程
    for path in ["/Activity/A/Index", "/Activity/AHA/Index"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href*='/Activity/']"):
            t = clean(a.get_text())
            if len(t) < 5: continue
            out.append(mk(t, "台灣急診醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), "course"))
    # 最新消息
    for path in ["/News/11/Index", "/News/10/Index", "/News/9/Index"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href*='/News/Details/']"):
            t = clean(a.get_text())
            if len(t) < 5: continue
            parent = a.find_parent(["tr","li","div"])
            row_text = parent.get_text() if parent else ""
            date = extract_all_dates(row_text)
            out.append(mk(t, "台灣急診醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), "news",
                          date[-1] if date else None))
    return out

def scrape_seccm():
    """台灣急救加護醫學會"""
    out = []
    BASE = "https://www.seccm.org.tw"
    # 課程 - ACLS
    for path in ["/news/acls_class.asp", "/news/acls_course.asp", "/news/acls_NotUnion.asp"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5: continue
            if any(k in t for k in ["ACLS","課程","訓練","工作坊","研討"]):
                parent = a.find_parent(["tr","li","td"])
                date = extract_all_dates(parent.get_text() if parent else "")
                out.append(mk(t, "台灣急救加護醫學會", "台灣學會",
                              resolve(a.get("href",""), BASE), "course",
                              date[-1] if date else None))
    # 最新消息
    soup = fetch(BASE + "/orgNews/News_list.asp")
    if soup:
        for a in soup.select("a[href*='orgNews'], a[href*='News_detail']"):
            t = clean(a.get_text())
            if len(t) < 5: continue
            parent = a.find_parent(["tr","li","td"])
            date = extract_all_dates(parent.get_text() if parent else "")
            out.append(mk(t, "台灣急救加護醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:40]

def scrape_trauma():
    """台灣外傷醫學會"""
    out = []
    BASE = "http://www.trauma.org.tw"
    # 課程
    for path in ["/active/listA.asp", "/active/listB.asp"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for li in soup.select("li"):
            a = li.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 5: continue
            date = extract_all_dates(li.get_text())
            out.append(mk(t, "台灣外傷醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), "course",
                          date[-1] if date else None))
    # 最新消息
    soup = fetch(BASE + "/news/listA.asp")
    if soup:
        for li in soup.select("li"):
            a = li.find("a")
            if not a: continue
            t = clean(a.get_text())
            if len(t) < 5: continue
            date = extract_all_dates(li.get_text())
            out.append(mk(t, "台灣外傷醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:40]

def scrape_disaster():
    """台灣災難醫學會"""
    out = []
    BASE = "http://disaster.org.tw"
    # 此網站課程頁面為動態，嘗試抓公告
    soup = fetch(BASE + "/index/index.aspx")
    if soup:
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 100: continue
            href = a.get("href","")
            if any(k in href for k in ["class","News","bulletin","notice","Ch4"]):
                date = extract_all_dates(t)
                out.append(mk(t, "台灣災難醫學會", "台灣學會",
                              resolve(href, BASE), None,
                              date[-1] if date else None))
    return out[:20]

def scrape_twparamedicine():
    """台灣醫療救護學會 - 課程在 training.html，消息在首頁"""
    out = []
    BASE = "https://twparamedicine.org"
    # 課程
    soup = fetch(BASE + "/training.html")
    if soup:
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5: continue
            if any(k in t for k in ["課程","ACLS","CPD","TCCC","TECC","PHTLS","訓練","工作坊"]):
                date = extract_all_dates(t)
                out.append(mk(t, "台灣醫療救護學會", "台灣學會",
                              resolve(a.get("href",""), BASE), "course",
                              date[-1] if date else None))
    # 消息（首頁動態載入，改抓靜態可見連結）
    soup2 = fetch(BASE + "/")
    if soup2:
        for a in soup2.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 100: continue
            if any(k in t for k in ["公告","消息","通知","聲明"]):
                out.append(mk(t, "台灣醫療救護學會", "台灣學會",
                              resolve(a.get("href",""), BASE), "news"))
    return out[:30]

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
    """中華緊急救護技術員協會"""
    out = []
    BASE = "https://www.emt.org.tw/temtaf"
    # 課程列表頁
    for course in ["EMT-1","EMT-1A","EMT-2","EMTP","BLS","AED","CPR"]:
        soup = fetch(f"{BASE}/LeCourseList?course={course}")
        if not soup: continue
        for a in soup.select("a[href*='LeCourse']"):
            t = clean(a.get_text())
            if len(t) < 5: continue
            parent = a.find_parent(["tr","li","td"])
            date = extract_all_dates(parent.get_text() if parent else "")
            out.append(mk(t, "中華緊急救護技術員協會", "台灣協會",
                          resolve(a.get("href",""), BASE), "course",
                          date[-1] if date else None))
        time.sleep(0.5)
    # 公告
    soup2 = fetch(f"{BASE}/GpBulletinList")
    if soup2:
        for a in soup2.select("a[href*='Bulletin']"):
            t = clean(a.get_text())
            if len(t) < 5: continue
            parent = a.find_parent(["tr","li","td"])
            date = extract_all_dates(parent.get_text() if parent else "")
            out.append(mk(t, "中華緊急救護技術員協會", "台灣協會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:40]

def scrape_taiwanwma():
    """台灣野外地區緊急救護協會"""
    out = []
    BASE = "https://taiwanwma.org"
    for path in ["/", "/news", "/events", "/course"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("article a, .post a, .entry a, h2 a, h3 a"):
            t = clean(a.get_text())
            if len(t) < 5: continue
            date = extract_all_dates(t)
            out.append(mk(t, "台灣野外地區緊急救護協會", "台灣協會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:20]

def scrape_tsorcc():
    """復甦照護小學堂"""
    out = []
    BASE = "https://www.tsorcc.org.tw"
    soup = fetch(BASE + "/")
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
    """臺灣燒傷暨傷口照護學會"""
    out = []
    BASE = "https://www.burn.org.tw"
    soup = fetch(BASE + "/")
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
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 120: continue
        if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","年會"]):
            date = extract_all_dates(t)
            out.append(mk(t, "台灣呼吸道處理醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:20]

def scrape_tana():
    """台灣麻醉專科護理學會"""
    out = []
    BASE = "https://www.tana.org.tw"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 120: continue
        if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","年會"]):
            date = extract_all_dates(t)
            out.append(mk(t, "台灣麻醉專科護理學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:20]

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
    """中華民國重症醫學會"""
    out = []
    BASE = "https://www.tsccm.org.tw"
    # 課程
    soup = fetch(BASE + "/news/news_list.asp")
    if soup:
        for a in soup.select("a[href*='news']"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            parent = a.find_parent(["tr","li","td","div"])
            date = extract_all_dates(parent.get_text() if parent else "")
            out.append(mk(t, "中華民國重症醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    # 從首頁抓課程公告
    soup2 = fetch(BASE + "/")
    if soup2:
        for a in soup2.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            if any(k in t for k in ["課程","研討","工作坊","訓練","活動","公告","災難"]):
                parent = a.find_parent(["li","div","td"])
                date = extract_all_dates(parent.get_text() if parent else t)
                out.append(mk(t, "中華民國重症醫學會", "台灣學會",
                              resolve(a.get("href",""), BASE), None,
                              date[-1] if date else None))
    return out[:30]

def scrape_sgecm():
    """台灣老人急重症醫學會 — 學術活動列表（ASP表格結構）"""
    out = []
    BASE = "http://www.sgecm.org.tw"
    # 學術活動列表頁（表格結構）
    soup = fetch(BASE + "/htm/cont_1_2.asp")
    if soup:
        for row in soup.select("table tr"):
            cols = row.find_all("td")
            if len(cols) < 3: continue
            # 第1欄：日期，第2欄：課程代號，第3欄：活動名稱（含連結）
            date_text = clean(cols[0].get_text())
            date = parse_dates_from_title(date_text).get("event_date") or extract_all_dates(date_text)
            if isinstance(date, list): date = date[-1] if date else None
            a = cols[2].find("a") if len(cols) > 2 else None
            if a:
                t = clean(a.get_text())
                if len(t) < 5: continue
                href = resolve(a.get("href",""), BASE)
                out.append(mk(t, "台灣老人急重症醫學會", "台灣學會",
                              href, "course", date))
            else:
                t = clean(cols[2].get_text()) if len(cols) > 2 else ""
                if len(t) < 5: continue
                out.append(mk(t, "台灣老人急重症醫學會", "台灣學會",
                              BASE, "course", date))
    # 最新消息
    soup2 = fetch(BASE + "/htm/cont_2_1.asp")
    if soup2:
        for a in soup2.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            parent = a.find_parent(["tr","li","td"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "台灣老人急重症醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), "news",
                          date[-1] if date else None))
    return out[:30]

def scrape_tamis():
    """台灣心肌梗塞學會"""
    out = []
    BASE = "https://tamis.org.tw"
    # 學術活動列表頁
    soup = fetch(BASE + "/activity")
    if soup:
        for a in soup.select("a[href*='/activity/']"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            parent = a.find_parent(["li","div","article"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "台灣心肌梗塞學會", "台灣學會",
                          resolve(a.get("href",""), BASE), "course",
                          date[-1] if date else None))
    return out[:20]

def scrape_tebma():
    """台灣實證醫學學會"""
    out = []
    BASE = "https://www.tebma.org.tw"
    # 課程報名列表
    for path in ["/seminar/list", "/event/list"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href*='/event/'], a[href*='/seminar/']"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            parent = a.find_parent(["li","div","article","tr"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "台灣實證醫學學會", "台灣學會",
                          resolve(a.get("href",""), BASE), "course",
                          date[-1] if date else None))
    return out[:20]

def scrape_tmed():
    """臺灣醫事繼續教育學會"""
    out = []
    BASE = "https://www.tmed.com.tw"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t for k in ["課程","活動","研討","訓練","工作坊","報名","公告","消息"]):
            date = extract_all_dates(t)
            out.append(mk(t, "臺灣醫事繼續教育學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:20]

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
    """台灣急重症模擬醫學會 (改良版)"""
    out = []
    BASE = "https://simulation.org.tw"
    for path in ["/news/", "/activity/", "/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            href = a.get("href","")
            # 跳過導覽與重複連結
            if any(skip in t for skip in ["委員介紹","委員會","首頁","關於","聯絡","友站","協力","加入"]): continue
            if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","競賽","年會"]):
                parent = a.find_parent(["li","div","article"])
                date = extract_all_dates(parent.get_text() if parent else t)
                out.append(mk(t, "台灣急重症模擬醫學會", "台灣學會",
                              resolve(href, BASE), None,
                              date[-1] if date else None))
    return out[:25]

def scrape_tafm():
    """台灣家庭醫學醫學會"""
    out = []
    BASE = "https://www.tafm.org.tw"
    # 課程列表（已知格式）
    for path in ["/ehc-tafm/s/w/edu/scheduleInfo1",
                 "/ehc-tafm/s/w/news_news/articleList"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息"]):
                parent = a.find_parent(["li","div","tr"])
                date = extract_all_dates(parent.get_text() if parent else t)
                out.append(mk(t, "台灣家庭醫學醫學會", "台灣學會",
                              resolve(a.get("href",""), BASE), None,
                              date[-1] if date else None))
    return out[:25]

def scrape_sfast():
    """台灣運動安全暨急救技能推廣協會"""
    out = []
    BASE = "https://www.sfast.org"
    soup = fetch(BASE + "/index.php?lang=tw")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","AED","急救"]):
            date = extract_all_dates(t)
            out.append(mk(t, "台灣運動安全暨急救技能推廣協會", "台灣協會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:15]

def scrape_dpac():
    """彰化縣防災協會"""
    out = []
    BASE = "https://dpac.org.tw"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","防災","急救"]):
            date = extract_all_dates(t)
            out.append(mk(t, "彰化縣防災協會（DPAC）", "台灣協會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:15]

def scrape_webtema():
    """台灣緊急應變管理協會"""
    out = []
    BASE = "https://webtema.org"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","應變"]):
            date = extract_all_dates(t)
            out.append(mk(t, "台灣緊急應變管理協會", "台灣協會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:15]

def scrape_anne():
    """安妮怎麼了"""
    out = []
    BASE = "https://www.anne.education"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","CPR","AED","急救"]):
            date = extract_all_dates(t)
            out.append(mk(t, "安妮怎麼了", "台灣急救社群",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:15]


def scrape_mgems():
    """中華民國大型活動緊急救護協會"""
    out = []
    BASE = "https://www.mgems.org"
    # 課程
    for path in ["/category/ems-course/", "/category/on-line-checkin/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("h2 a, h3 a, .entry-title a, article a"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            parent = a.find_parent(["article","div"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "中華民國大型活動緊急救護協會", "台灣協會",
                          resolve(a.get("href",""), BASE), "course",
                          date[-1] if date else None))
    # 最新消息（所有文章）
    soup2 = fetch(BASE + "/")
    if soup2:
        for a in soup2.select("h2 a, h3 a, .entry-title a"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            date = extract_all_dates(t)
            out.append(mk(t, "中華民國大型活動緊急救護協會", "台灣協會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:30]

def scrape_ntuch():
    """臺大兒童醫院急重症兒童轉院外接醫療團隊"""
    out = []
    BASE = "https://www.ntuh.gov.tw"
    soup = fetch(BASE + "/ntuch/Index.action")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t for k in ["課程","活動","研討","訓練","公告","消息","轉院","外接"]):
            date = extract_all_dates(t)
            out.append(mk(t, "臺大兒童醫院急重症外接醫療團隊", "台灣急救社群",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:15]

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
    """台灣新生兒科醫學會"""
    out = []
    BASE = "https://tsn-neonatology.com"
    soup = fetch(BASE + "/")
    if not soup: return out
    for a in soup.select("a[href]"):
        t = clean(a.get_text())
        if len(t) < 8 or len(t) > 150: continue
        if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","年會","NRP"]):
            date = extract_all_dates(t)
            out.append(mk(t, "台灣新生兒科醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:20]

def scrape_sma():
    """台灣運動醫學學會"""
    out = []
    BASE = "http://www.sma.org.tw"
    # 活動訊息頁
    for path in ["/activity.htm", "/news.htm", "/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            if any(k in t for k in ["課程","活動","研討","訓練","工作坊","公告","消息","年會"]):
                date = extract_all_dates(t)
                out.append(mk(t, "台灣運動醫學學會", "台灣學會",
                              resolve(a.get("href",""), BASE), None,
                              date[-1] if date else None))
    return out[:20]

def scrape_tasm():
    """台灣運動醫學醫學會"""
    out = []
    BASE = "https://www.tasm.org.tw"
    # 最新消息列表頁（已確認結構）
    soup = fetch(BASE + "/news/list.asp")
    if soup:
        for a in soup.select("a[href*='news/info']"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            parent = a.find_parent(["tr","li","td"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "台灣運動醫學醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:20]

def scrape_pediatr():
    """臺灣兒科醫學會"""
    out = []
    BASE = "https://www.pediatr.org.tw"
    # 學會公告列表（已確認結構）
    soup = fetch(BASE + "/news/news_list.asp")
    if soup:
        for a in soup.select("a[href*='news_info']"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            parent = a.find_parent(["tr","li","td"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "臺灣兒科醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:20]

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
    BASE = "https://cbrnprofessionals.com"
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
    """SOMA — Special Operations Medical Association"""
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
    """Next Generation Combat Medic"""
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
    BASE = "https://tacmedsolutions.com"
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
    BASE = "https://trilogyems.com"
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
    """CoTCCC"""
    out = []
    BASE = "https://jts.health.mil"
    # CoTCCC 指南更新頁
    soup = fetch(BASE + "/index.cfm/committees/cotccc")
    if soup:
        for a in soup.select("a[href]"):
            t = clean(a.get_text())
            if len(t) < 8 or len(t) > 150: continue
            if any(k in t.lower() for k in ["guideline","update","tccc","recommendation","handbook","news"]):
                out.append(mk(t, "CoTCCC", "戰術救護",
                              resolve(a.get("href",""), BASE), "news"))
    return out[:10]

def scrape_crisis():
    """Crisis Medicine"""
    out = []
    BASE = "https://www.crisis-medicine.com"
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
    BASE = "https://ctomsinc.com"
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
    BASE = "https://www.deployedmedicine.com"
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
