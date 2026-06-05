"""
NSE market-calendar helpers — holiday calendar, market-open check, and the
"has a session traded since publication" logic.

Pure (stdlib only); extracted verbatim from app.py so the trading-hours rules
live in one small, testable place. app.py imports these names back, so the
runtime behaviour is identical.
"""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


# NSE market holidays keyed by year → set of (month, day) tuples.
# Bug #12 fix: extended to multi-year so the system stays correct beyond 2026.
# Update this dict each year when NSE releases the official holiday calendar.
NSE_HOLIDAYS_2026 = {
    (1, 26),   # Republic Day
    (3, 3),    # Holi
    (3, 26),   # Ram Navami
    (3, 31),   # Mahavir Jayanti
    (4, 3),    # Good Friday
    (4, 14),   # Dr. Ambedkar Jayanti
    (5, 1),    # Maharashtra Day
    (5, 28),   # Bakri Id (Eid ul Adha)
    (6, 26),   # Muharram
    (8, 15),   # Independence Day
    (9, 14),   # Ganesh Chaturthi
    (10, 2),   # Gandhi Jayanti
    (10, 20),  # Dussehra
    (11, 8),   # Diwali - Laxmi Puja
    (11, 10),  # Diwali (Balipratipada)
    (11, 24),  # Guru Nanak Jayanti
    (12, 25),  # Christmas
}

# Approximate 2027 holidays — update with official NSE calendar once released.
NSE_HOLIDAYS_2027 = {
    (1, 26),   # Republic Day
    (3, 17),   # Holi (approx)
    (3, 30),   # Ugadi / Gudi Padwa (approx)
    (4, 2),    # Good Friday
    (4, 14),   # Dr. Ambedkar Jayanti
    (4, 21),   # Ram Navami (approx)
    (5, 1),    # Maharashtra Day
    (8, 15),   # Independence Day
    (8, 16),   # Janmashtami (approx)
    (10, 2),   # Gandhi Jayanti
    (10, 20),  # Dussehra (approx)
    (10, 29),  # Diwali - Laxmi Puja (approx)
    (10, 30),  # Diwali Balipratipada (approx)
    (12, 25),  # Christmas
}

_NSE_HOLIDAYS_BY_YEAR = {
    2026: NSE_HOLIDAYS_2026,
    2027: NSE_HOLIDAYS_2027,
}


def is_market_holiday(month, day, year=None):
    """Return True if (month, day) is an NSE holiday for the given year."""
    if year is None:
        year = datetime.now().year
    holidays = _NSE_HOLIDAYS_BY_YEAR.get(year, NSE_HOLIDAYS_2026)  # fallback to 2026 set
    return (month, day) in holidays

def is_market_open():
    """Return True if Indian stock market is currently open (Mon-Fri, 9:15 AM – 3:30 PM IST, non-holiday)."""
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    weekday = now_ist.weekday()  # 0=Mon … 4=Fri
    if weekday >= 5:
        return False
    # Bug #12 fix: use year-aware holiday check
    if is_market_holiday(now_ist.month, now_ist.day, now_ist.year):
        return False
    t = now_ist.hour * 60 + now_ist.minute  # minutes since midnight
    # Bug #23 fix: market closes AT 15:30, so use strict < (not <=)
    return (9 * 60 + 15) <= t < (15 * 60 + 30)


def published_after_market_hours(dt_str):
    """Return True when the provided news date occurs outside NSE trading hours."""
    if not dt_str:
        return False
    try:
        dt = parsedate_to_datetime(dt_str)
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ist = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
        weekday = ist.weekday()
        if weekday >= 5:
            return True
        minutes = ist.hour * 60 + ist.minute
        return minutes < (9 * 60 + 15) or minutes > (15 * 60 + 30)
    except Exception:
        return False


def has_market_traded_since(published_dt_str):
    """
    Returns True if at least one market session has occurred or is currently occurring
    since the news was published.
    """
    if not published_dt_str:
        return True
    try:
        if isinstance(published_dt_str, datetime):
            dt = published_dt_str
        elif ',' in published_dt_str:
            dt = parsedate_to_datetime(published_dt_str)
        else:
            # SQL format 'YYYY-MM-DD HH:MM:SS' is UTC
            dt = datetime.strptime(published_dt_str, '%Y-%m-%d %H:%M:%S')
            dt = dt.replace(tzinfo=timezone.utc)
            
        if dt is None:
            return True
            
        ist = timezone(timedelta(hours=5, minutes=30))
        published_ist = dt.astimezone(ist)
        now_ist = datetime.now(ist)
        
        # If published in the future
        if published_ist >= now_ist:
            return False
            
        def is_trading_day(d):
            if d.weekday() >= 5:
                return False
            return not is_market_holiday(d.month, d.day, d.year)

        curr_date = published_ist.date()
        end_date = now_ist.date()
        
        while curr_date <= end_date:
            if is_trading_day(curr_date):
                market_start = datetime.combine(curr_date, datetime.min.time()).replace(tzinfo=ist) + timedelta(hours=9, minutes=15)
                market_end = datetime.combine(curr_date, datetime.min.time()).replace(tzinfo=ist) + timedelta(hours=15, minutes=30)
                
                overlap_start = max(published_ist, market_start)
                overlap_end = min(now_ist, market_end)
                if overlap_start < overlap_end:
                    return True
            curr_date += timedelta(days=1)
            
        return False
    except Exception as e:
        print(f"Error in has_market_traded_since: {e}")
        return True
