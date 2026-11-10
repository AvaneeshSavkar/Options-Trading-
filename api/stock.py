"""Yahoo Finance data for the Option Simulator.

GET /api/stock?ticker=NVDA                  everything the page needs
GET /api/stock?ticker=NVDA&expiry=2028-01-21  just the call chain for one expiry
GET /api/stock?ping=1                       health check, tells the page live data is available

Runs as a Vercel Python function (the `handler` class) and is also used by server.py locally.
Data comes from yfinance, an unofficial Yahoo Finance library.
"""
import datetime as dt
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import yfinance as yf

CACHE_SECONDS = 600
_cache = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- helpers
def _num(x):
    """Plain float, or None for missing / NaN / non-numbers."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _history_lines(df):
    """Price table as 'YYYY-MM-DD<TAB>price' lines, newest first (the page's parser reads this)."""
    if df is None or len(df) == 0:
        return ''
    col = 'Adj Close' if 'Adj Close' in df.columns else 'Close'
    rows = []
    for idx, row in df.iterrows():
        p = _num(row.get(col))
        if p is None:
            p = _num(row.get('Close'))
        if p is not None and p > 0:
            rows.append((idx.strftime('%Y-%m-%d'), round(p, 4)))
    rows.sort(reverse=True)
    return '\n'.join(f'{d}\t{p}' for d, p in rows)


def _history(symbol, period, interval):
    df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False, actions=False)
    return df.dropna(how='all') if df is not None else None


def _calls(symbol, expiry):
    calls = yf.Ticker(symbol).option_chain(expiry).calls
    out = []
    for _, r in calls.iterrows():
        k = _num(r.get('strike'))
        if k is None:
            continue
        out.append({
            'strike': k,
            'bid': round(_num(r.get('bid')) or 0.0, 2),
            'ask': round(_num(r.get('ask')) or 0.0, 2),
            'last': round(_num(r.get('lastPrice')) or 0.0, 2),
            'oi': int(_num(r.get('openInterest')) or 0),
            'volume': int(_num(r.get('volume')) or 0),
            'iv': _num(r.get('impliedVolatility')),
        })
    out.sort(key=lambda c: c['strike'])
    return out


def pick_expiry(expiries):
    """Suresh's rule: the furthest-out January expiry (LEAPS). Falls back to the furthest expiry."""
    if not expiries:
        return None
    jan = [e for e in expiries if e[5:7] == '01']
    return max(jan) if jan else max(expiries)


def pick_strike(chain, price):
    """The listed strike nearest 20% above the current price, preferring contracts that actually trade."""
    if not chain or not price:
        return None
    live = [c for c in chain if c['bid'] > 0 or c['ask'] > 0 or c['oi'] > 0] or chain
    target = price * 1.2
    return min(live, key=lambda c: (abs(c['strike'] - target), -c['strike']))


def option_price(row):
    """Midpoint of bid and ask when the market is quoting, otherwise the last trade."""
    if row is None:
        return None, None
    if row['bid'] > 0 and row['ask'] > 0:
        return round((row['bid'] + row['ask']) / 2, 2), 'midpoint'
    if row['last'] > 0:
        return round(row['last'], 2), 'last trade'
    return None, None


def _chain_part(symbol, expiry, price):
    chain = _calls(symbol, expiry)
    row = pick_strike(chain, price)
    p, src = option_price(row)
    return {'expiry': expiry, 'chain': chain,
            'pick': None if row is None else {**row, 'price': p, 'priceSource': src}}


def _risk_free():
    """2-year Treasury yield (Yahoo 2YY=F), falling back to the 13-week T-bill (^IRX)."""
    for sym, label in (('2YY=F', '2-year Treasury yield'), ('^IRX', '13-week T-bill yield')):
        try:
            closes = _history(sym, '5d', '1d')['Close'].dropna()
            v = _num(closes.iloc[-1])
            if v is not None and 0 < v < 20:
                return {'value': round(v, 2), 'source': f'{label} ({sym})'}
        except Exception:
            continue
    return None


def _spx_above_200():
    closes = _history('^GSPC', '1y', '1d')['Close'].dropna()
    if len(closes) < 200:
        return None
    return 'Yes' if closes.iloc[-1] >= closes.iloc[-200:].mean() else 'No'


def _earnings_soon(cal):
    dates = (cal or {}).get('Earnings Date') or []
    today = dt.date.today()
    future = [d for d in dates if isinstance(d, dt.date) and d >= today]
    if not future:
        return None, None
    nxt = min(future)
    return ('Yes' if (nxt - today).days <= 14 else 'No'), nxt.isoformat()


def _beats(df):
    if df is None or len(df) == 0 or 'epsActual' not in df.columns or 'epsEstimate' not in df.columns:
        return None
    df = df.sort_index().tail(4)
    n = 0
    for _, r in df.iterrows():
        a, e = _num(r.get('epsActual')), _num(r.get('epsEstimate'))
        if a is not None and e is not None and a > e:
            n += 1
    return str(n)


def _eps_revisions(df):
    """Current-year EPS estimate today versus 90 days ago: Up / Flat / Down (1% band)."""
    if df is None or len(df) == 0 or 'current' not in df.columns or '90daysAgo' not in df.columns:
        return None
    for period in ('0y', '+1y'):
        if period in df.index:
            now, then = _num(df.loc[period, 'current']), _num(df.loc[period, '90daysAgo'])
            if now is not None and then not in (None, 0):
                ch = (now - then) / abs(then)
                return 'Up' if ch > 0.01 else 'Down' if ch < -0.01 else 'Flat'
    return None


def _research(info, cal, hist, trend, spx):
    info = info or {}
    pct = lambda k: None if _num(info.get(k)) is None else round(_num(info[k]) * 100, 1)
    r = {}
    r['earn'], next_earn = _earnings_soon(cal)
    r['rev'] = pct('revenueGrowth')
    r['eps'] = pct('earningsQuarterlyGrowth') if _num(info.get('earningsQuarterlyGrowth')) is not None else pct('earningsGrowth')
    r['opm'] = pct('operatingMargins')
    fcf = _num(info.get('freeCashflow'))
    r['fcf'] = None if fcf is None else ('Yes' if fcf > 0 else 'No')
    de = _num(info.get('debtToEquity'))
    r['de'] = None if de is None else round(de, 1)
    r['beats'] = _beats(hist)
    r['rev90'] = _eps_revisions(trend)
    peg = _num(info.get('pegRatio'))
    if peg is None:
        peg = _num(info.get('trailingPegRatio'))
    r['peg'] = None if peg is None else round(peg, 2)
    r['target'] = _num(info.get('targetMeanPrice'))
    r['rating'] = _num(info.get('recommendationMean'))
    dma = _num(info.get('twoHundredDayAverage'))
    r['dma'] = None if dma is None else round(dma, 2)
    ch, spx_ch = _num(info.get('52WeekChange')), _num(info.get('SandP52WeekChange'))
    r['beatSpx'] = None if ch is None or spx_ch is None else ('Yes' if ch > spx_ch else 'No')
    r['spx200'] = spx
    cap = _num(info.get('marketCap'))
    r['cap'] = None if cap is None else round(cap / 1e9, 1)
    r['short'] = pct('shortPercentOfFloat')
    return {k: v for k, v in r.items() if v is not None}, next_earn


def _latest_close(df):
    """(date, price) of the most recent daily close."""
    if df is None or len(df) == 0 or 'Close' not in df.columns:
        return None
    s = df['Close'].dropna()
    return (s.index[-1].date(), float(s.iloc[-1])) if len(s) else None


def _add_latest(lines, latest, monthly=False):
    """Yahoo sometimes leaves out the current week (or month). Add today's close as its own row when missing."""
    if not lines or not latest:
        return lines
    d, p = latest
    newest = dt.date.fromisoformat(lines.split('\n', 1)[0].split('\t')[0])
    missing = (d.year, d.month) > (newest.year, newest.month) if monthly else (d - newest).days >= 7
    return f'{d.isoformat()}\t{round(p, 4)}\n{lines}' if missing else lines


# ---------------------------------------------------------------- main entry points
def _cached(key, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def get_stock(symbol):
    symbol = symbol.strip().upper()
    jobs = {
        'weekly': lambda: _history(symbol, '1y', '1wk'),
        'monthly': lambda: _history(symbol, '5y', '1mo'),
        'daily': lambda: _history(symbol, '5d', '1d'),
        'expiries': lambda: list(yf.Ticker(symbol).options),
        'info': lambda: yf.Ticker(symbol).info,
        'calendar': lambda: yf.Ticker(symbol).calendar,
        'hist': lambda: yf.Ticker(symbol).earnings_history,
        'trend': lambda: yf.Ticker(symbol).eps_trend,
        'spx': _spx_above_200,
        'rf': _risk_free,
    }
    res, errors = {}, {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {k: ex.submit(f) for k, f in jobs.items()}
        for k, f in futs.items():
            try:
                res[k] = f.result(timeout=40)
            except Exception as e:  # one failed piece should not sink the rest
                res[k] = None
                errors[k] = str(e)[:200]

    latest = _latest_close(res.get('daily'))
    weekly = _add_latest(_history_lines(res['weekly']), latest)
    monthly = _add_latest(_history_lines(res['monthly']), latest, monthly=True)
    if not weekly:
        return 404, {'error': f'No price history found for {symbol}. Check the ticker symbol.', 'details': errors}
    price = float(weekly.split('\n')[0].split('\t')[1])
    info = res['info'] or {}

    out = {
        'ticker': symbol,
        'name': info.get('longName') or info.get('shortName') or symbol,
        'currency': info.get('currency'),
        'fetchedAt': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'price': price,
        'weekly': weekly,
        'monthly': monthly,
        'rf': res['rf'],
        'expiries': res['expiries'] or [],
        'research': {},
    }
    out['research'], out['nextEarnings'] = _research(info, res['calendar'], res['hist'], res['trend'], res['spx'])

    exp = pick_expiry(out['expiries'])
    if exp:
        try:
            out.update(_chain_part(symbol, exp, price))
        except Exception as e:
            errors['chain'] = str(e)[:200]
    if errors:
        out['warnings'] = errors
    return 200, out


def get_chain(symbol, expiry, price=None):
    symbol = symbol.strip().upper()
    if price is None:
        weekly = _history_lines(_history(symbol, '5d', '1d'))
        price = float(weekly.split('\n')[0].split('\t')[1]) if weekly else None
    return 200, _chain_part(symbol, expiry, price)


def respond(query):
    """(status, dict) for a parsed query string. Shared by Vercel and server.py."""
    q = {k: v[0] for k, v in query.items() if v}
    if q.get('ping'):
        return 200, {'ok': True}
    t = (q.get('ticker') or '').strip().upper()
    if not t or len(t) > 12 or not all(ch.isalnum() or ch in '.-^=' for ch in t):
        return 400, {'error': 'Enter a ticker symbol, like NVDA.'}
    try:
        if q.get('expiry'):
            price = _num(q.get('price'))
            return _cached(('chain', t, q['expiry']), lambda: get_chain(t, q['expiry'], price))
        return _cached(('stock', t), lambda: get_stock(t))
    except Exception as e:
        return 502, {'error': f'Yahoo Finance did not respond for {t}. Try again in a minute.', 'details': str(e)[:300]}


class handler(BaseHTTPRequestHandler):
    """Vercel entry point."""

    def do_GET(self):
        status, body = respond(parse_qs(urlparse(self.path).query))
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 's-maxage=600, stale-while-revalidate=300' if status == 200 else 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
