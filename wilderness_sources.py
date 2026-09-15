"""Additional wilderness course sources, using the existing course schema."""
import hashlib
import re
from datetime import date
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

MIASAN = '米亞桑戶外中心'
FORMOSA = '小島探險'
TIGER = '羌虎跨域生活'
NEW_SOURCES = {MIASAN, FORMOSA, TIGER}


def canonical_url(url):
    parsed = urlsplit(url or '')
    # Tracking parameters on Miasan event URLs do not identify another course.
    if parsed.hostname == 'miasan.com' and parsed.path.startswith('/event/'):
        return 'https://miasan.com' + parsed.path.rstrip('/')
    return url or ''


def _kind(title):
    match = re.search(r'(?<![A-Za-z])(WEMS|WALS|WAFA|WFA|WFR|BRIDGE)(?![A-Za-z])', title, re.I)
    return match.group(1).upper() if match else None


def _is_course(title):
    return bool(_kind(title) or re.search(r'野外.*(?:急救|救護|醫療)|急救.*(?:訓練|課程)', title))


def _start_date(text):
    patterns = [r'(\d{4})[./-](\d{1,2})[./-](\d{1,2})',
                r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日']
    for pattern in patterns:
        for match in re.finditer(pattern, text or ''):
            try:
                return date(*map(int, match.groups())).isoformat()
            except ValueError:
                continue
    return None


def _deadline(text):
    match = re.search(r'(?:報名截止|截止日期)\s*[:：]?\s*(\d{4}[./-]\d{1,2}[./-]\d{1,2}|\d{4}年\s*\d{1,2}月\s*\d{1,2}日)', text or '')
    return _start_date(match.group(1)) if match else None


def _fetch(url, log):
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        response.raise_for_status()
        response.encoding = 'utf-8'
        return BeautifulSoup(response.text, 'html.parser')
    except requests.RequestException as exc:
        log(f'    [warn] 野外課程來源讀取失敗 {url}: {exc}')
        return None


def _next_page(soup, current, host, path_prefix):
    # Follow only pagination links that actually occur in the source page.
    for selector in ('a[rel="next"]', '.tribe-events-nav-next a', '.pagination a', '#lecture_pagination a'):
        for link in soup.select(selector):
            url = urljoin(current, link.get('href', ''))
            parts = urlsplit(url)
            if parts.hostname != host or not parts.path.startswith(path_prefix):
                continue
            label = link.get_text(' ', strip=True)
            if link.get('rel') == ['next'] or re.search(r'下一頁|下一個|Next|»|›', label, re.I):
                return url
    return None


def parse_miasan_detail(soup, title, url, mk):
    heading = soup.select_one('h1.tribe-events-single-event-title, h1.entry-title, h1')
    if heading and _is_course(heading.get_text(' ', strip=True)):
        title = heading.get_text(' ', strip=True)
    start = soup.select_one('.tribe-events-start-datetime[title], .dtstart[title]')
    # The visible local date is authoritative; JSON-LD on this site uses UTC.
    event_date = _start_date(start.get('title', '')) if start else None
    event_date = event_date or _start_date(title)
    item = mk(title, MIASAN, '台灣急救社群', url, 'course', date=event_date)
    item['date'] = event_date
    content = soup.select_one('.tribe-events-single-event-description') or soup
    item['deadline'] = _deadline(content.get_text(' ', strip=True))
    item['id'] = hashlib.md5(f'{MIASAN}::{canonical_url(url)}'.encode()).hexdigest()[:12]
    return item


def scrape_miasan(mk, log):
    current = 'https://miasan.com/events/'
    visited, event_urls, out = set(), set(), []
    while current and current not in visited and len(visited) < 20:
        visited.add(current)
        soup = _fetch(current, log)
        if soup is None:
            break
        for link in soup.select('.tribe-events-list-event-title a, .tribe-events-calendar-list__event-title a'):
            title = link.get_text(' ', strip=True)
            url = urljoin(current, link.get('href', ''))
            if urlsplit(url).hostname != 'miasan.com' or not urlsplit(url).path.startswith('/event/'):
                continue
            if not _is_course(title) or canonical_url(url) in event_urls:
                continue
            event_urls.add(canonical_url(url))
            detail = _fetch(url, log)
            if detail is None:
                detail = BeautifulSoup('', 'html.parser')
            out.append(parse_miasan_detail(detail, title, url, mk))
        current = _next_page(soup, current, 'miasan.com', '/events/')
    # WEMS is discovered through the event list, rather than permanently seeded.
    return out


def parse_formosa(soup, mk):
    out = []
    base = 'https://www.formosa-adventure.com/'
    for card in soup.select('.product_box .pro'):
        link = card.select_one('.pro_name a[href]')
        if not link:
            continue
        title = link.get_text(' ', strip=True)
        if not _is_course(title):
            continue
        text = card.get_text(' ', strip=True)
        # Associate each year only with its own cohort section, not prices/hours.
        sections = list(re.finditer(r'(\d{4})\s*(?:年)?\s*梯次\s*[:：]?', text))
        for index, section in enumerate(sections):
            year = int(section.group(1))
            cohort_text = text[section.end():sections[index + 1].start() if index + 1 < len(sections) else len(text)]
            pattern = r'(?:(\d{4})/)?(\d{1,2})/(\d{1,2})\s*[-–~～]\s*(?:(\d{1,2})/)?(\d{1,2})'
            for match in re.finditer(pattern, cohort_text):
                y = int(match.group(1) or year)
                month, day = int(match.group(2)), int(match.group(3))
                end_month, end_day = int(match.group(4) or month), int(match.group(5))
                try:
                    start = date(y, month, day)
                    end = date(y + (end_month < month), end_month, end_day)
                    if end < start:
                        continue
                except ValueError:
                    continue
                dated_title = f'{start:%Y/%m/%d}-{end:%m/%d} {title}'
                url = urljoin(base, link['href'])
                item = mk(dated_title, FORMOSA, '台灣急救社群', url, 'course', date=start.isoformat())
                item['date'] = start.isoformat()
                item['deadline'] = None
                # Each publishing source retains its own course record.
                out.append(item)
    return out


def scrape_formosa(mk, log):
    soup = _fetch('https://www.formosa-adventure.com/product.php?CNo=27', log)
    return parse_formosa(soup, mk) if soup is not None else []


def parse_tiger(soup, current, mk, log, fetcher=None):
    out = []
    # Restrict to course cards, excluding footer/navigation and classic reviews.
    for link in soup.select('#lecture_new a[href]'):
        url = urljoin(current, link['href'])
        title_node = link.select_one('h2, h3, .title, .name')
        title = (title_node or link).get_text(' ', strip=True)
        if urlsplit(url).hostname != 'mttigertw.com' or not _is_course(title):
            continue
        detail = (fetcher or (lambda u: _fetch(u, log)))(url)
        event_date = _start_date(link.get_text(' ', strip=True))
        deadline = None
        if detail is not None:
            heading = detail.select_one('h1')
            if heading and _is_course(heading.get_text(' ', strip=True)):
                title = heading.get_text(' ', strip=True)
            content = detail.select_one('main, article, .act_content, .content') or detail
            text = content.get_text(' ', strip=True)
            dated = re.search(r'(?:活動日期|課程日期|上課日期|訓練時間|活動時間)\s*[:：]?\s*([^\n]{0,100})', text)
            event_date = (_start_date(dated.group(1)) if dated else None) or event_date or _start_date(title)
            deadline = _deadline(text)
        item = mk(title, TIGER, '台灣急救社群', url, 'course', date=event_date)
        item['date'], item['deadline'] = event_date, deadline
        item['id'] = hashlib.md5(f'{TIGER}::{url}'.encode()).hexdigest()[:12]
        out.append(item)
    return out


def scrape_tiger(mk, log):
    current = 'https://mttigertw.com/lecture/wmaitw/1/'
    out, visited = [], set()
    while current and current not in visited and len(visited) < 20:
        visited.add(current)
        soup = _fetch(current, log)
        if soup is None:
            break
        out.extend(parse_tiger(soup, current, mk, log))
        current = _next_page(soup, current, 'mttigertw.com', '/lecture/wmaitw/')
    return out


def build_scrapers(mk, log):
    return [(MIASAN, lambda: scrape_miasan(mk, log)),
            (FORMOSA, lambda: scrape_formosa(mk, log)),
            (TIGER, lambda: scrape_tiger(mk, log))]
