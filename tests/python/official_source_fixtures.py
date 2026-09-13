"""Synthetic samples of the pinned provider grammars; never live market evidence."""
from datetime import datetime, timezone
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import hepta_market_official_source_extractor as extractor

OBSERVED = int(datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc).timestamp()*1000)

def payloads():
    timezone_lines = '''BEGIN:VTIMEZONE
TZID:US-Eastern
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZNAME:EDT
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZNAME:EST
END:STANDARD
END:VTIMEZONE'''
    ics = '''BEGIN:VCALENDAR
PRODID:-//Department of Labor//Bureau of Labor Statistics//EN
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:PUBLISH
SUMMARY:BLS.gov Economic News Release Schedule
X-WR-CALNAME:BLS.gov Economic News Release Schedule
X-WR-TIMEZONE:US-Eastern
''' + timezone_lines + '\n'
    for i, date in enumerate(('20260901T083000', '20260920T083000'), 1):
        ics += f'''BEGIN:VEVENT
SEQUENCE:1
CLASS:PUBLIC
UID:00000000-0000-0000-0000-{i:012d}
DTSTART;TZID=US-Eastern:{date}
DURATION:PT0M
SUMMARY:Synthetic scheduled release {i}
LOCATION:Washington\\, DC
TRANSP:TRANSPARENT
CATEGORIES:IMPORTANT, BLS
END:VEVENT
'''
    ics += 'END:VCALENDAR\n'
    fed = '<h3>Meeting calendars, statements, and minutes (2026)</h3>The FOMC holds eight regularly scheduled meetings during the year'
    fed += '<div class="panel panel-default"><div class="panel-heading"><h4><a id="2026">2026 FOMC Meetings</a></h4></div>'
    for month in ('January', 'March', 'April', 'June', 'July', 'September', 'October', 'December'):
        fed += f'<div class="row fomc-meeting" "><div class="fomc-meeting__month col"><strong>{month}</strong></div><div class="fomc-meeting__date col">27-28</div></div>'
    fed += '<div class=\'lastUpdate\' id="lastUpdate">Last Update: January 01, 2026</div>'
    ecb = f'<link rel="canonical" href="{extractor.ECB_CALENDAR_URL}"><h1>Schedules for the meetings of the Governing Council and General Council of the ECB and related press conferences</h1><div class="definition-list -zebra"><meta property="article:published_time" content="2026-01-01">'
    for date in ('01/02/2026', '01/12/2026'):
        ecb += f'<dt>{date}</dt><dd>Governing Council of the ECB: Synthetic meeting<br></dd>'
    pub = 'Sat, 12 Sep 2026 15:00:00 GMT'
    fed_rss = f'''<rss version="2.0"><channel><title>FRB: Press Release - All Releases</title><link>https://www.federalreserve.gov/feeds/feeds.htm</link><description>All recent press releases from the Federal Reserve Board</description><language>en</language><item><title>Synthetic release</title><link>https://www.federalreserve.gov/newsevents/pressreleases/fixture.htm</link><guid>https://www.federalreserve.gov/newsevents/pressreleases/fixture.htm</guid><description>Synthetic fixture only</description><category>Monetary policy</category><pubDate>{pub}</pubDate></item></channel></rss>'''
    ecb_rss = f'''<rss version="2.0"><channel><title>ECB - European Central Bank</title><link>https://www.ecb.europa.eu/</link><description>Latest releases on the ECB website - Press releases, speeches and interviews, press conferences.</description><language>en</language><copyright>ECB</copyright><webMaster>fixture@example.invalid</webMaster><lastBuildDate>{pub}</lastBuildDate><category>news</category><generator>fixture</generator><docs>https://www.rssboard.org/rss-specification</docs><item><title>Synthetic release</title><link>https://www.ecb.europa.eu/press/fixture.html</link><guid>https://www.ecb.europa.eu/press/fixture.html</guid><pubDate>{pub}</pubDate></item></channel></rss>'''
    return {extractor.BLS_URL:ics.encode(), extractor.FED_CALENDAR_URL:fed.encode(), extractor.ECB_CALENDAR_URL:ecb.encode(), extractor.FED_PRESS_URL:fed_rss.encode(), extractor.ECB_PRESS_URL:ecb_rss.encode()}
