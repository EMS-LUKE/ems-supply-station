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
    """台灣老人急重症醫學會"""
    out = []
    BASE = "http://www.sgecm.org.tw"
    for path in ["/news/news_list.asp", "/"]:
        soup = fetch(BASE + path)
        if not soup: continue
        for a in soup.select("a[href*='news'], a[href*='course'], a[href*='activity']"):
            t = clean(a.get_text())
            if len(t) < 5 or len(t) > 150: continue
            parent = a.find_parent(["tr","li","td"])
            date = extract_all_dates(parent.get_text() if parent else t)
            out.append(mk(t, "台灣老人急重症醫學會", "台灣學會",
                          resolve(a.get("href",""), BASE), None,
                          date[-1] if date else None))
    return out[:25]

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
    # ── 台灣協會 ──
    ("中華緊急救護技術員協會",      scrape_emt),
    ("台灣野外地區緊急救護協會",    scrape_taiwanwma),
    ("台灣災難醫療隊發展協會",      scrape_twdmat),
    ("台灣運動安全暨急救技能推廣協會", scrape_sfast),
    ("彰化縣防災協會",              scrape_dpac),
    ("台灣緊急應變管理協會",        scrape_webtema),
    # ── 台灣急救社群 ──
    ("復甦照護小學堂",              scrape_tsorcc),
    ("安妮怎麼了",                  scrape_anne),
    # ── 國際 ──
    ("NAEMT",                       scrape_naemt),
    ("ERC",                         scrape_erc),
    ("Wilderness Medical Society",  scrape_wms),
]

def main():
    print(f"\n{'='*55}")
    print(f"緊急救護課程爬蟲 v3  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    all_items = []
    for name, fn in SCRAPERS:
        print(f"\n→ {name}")
        try:
            results = fn()
            courses = [r for r in results if r.get("type")=="course"]
            news    = [r for r in results if r.get("type")=="news"]
            print(f"  課程:{len(courses)}  消息:{len(news)}")
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

    # 排序
    unique.sort(key=lambda x: x.get("date") or "0000", reverse=True)

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
