# Option Simulator

A LEAPS call checker built on Suresh Nair's options model. Type a ticker and it pulls everything from Yahoo Finance:

- 1 year of weekly prices and 5 years of monthly prices
- The option chain, starting on Suresh's picks: the furthest January expiry and the strike nearest 20% above the price, priced at the bid-ask midpoint
- The risk-free rate (2-year Treasury yield)
- 21 of the 24 research scorecard items (only guidance, red flags and growth catalyst stay manual)

Then it runs the model: weekly and monthly multipliers, stretch and pattern, confidence and volatility, break-even and required growth, growth scenarios, the Black-Scholes fair-price check, and the verdict.

## What's in this folder

| File | What it does |
|---|---|
| `index.html` | The whole website (page, charts and model math in one file) |
| `api/stock.py` | Fetches Yahoo Finance data with yfinance. Runs as a Vercel function. |
| `server.py` | Runs the site on your own computer |
| `requirements.txt` | The one Python package needed (yfinance) |
| `vercel.json` | Vercel settings |

## Option 1: run it on your computer

You need Python 3.10 or newer ([python.org](https://www.python.org/downloads/); on Windows tick "Add Python to PATH" during install).

Open a terminal in this folder and run:

```
pip install -r requirements.txt
python server.py
```

Your browser opens http://localhost:8000. Press Ctrl+C in the terminal to stop it. (On a Mac, use `pip3` and `python3` if `pip` is not found.)

## Option 2: put it online for free (GitHub + Vercel)

GitHub Pages alone can't do this, because it only hosts static files and the site needs a small server to fetch Yahoo data. So the code lives on GitHub and Vercel runs it. Vercel signs in with your GitHub account, and every time you push a change the site updates by itself.

1. **Create the GitHub repo.** On github.com click **New repository**, name it `option-simulator`, and create it. On the empty repo page click **uploading an existing file**, drag in everything in this folder (including the `api` folder), and click **Commit changes**.
2. **Deploy on Vercel.** Go to [vercel.com](https://vercel.com), choose **Sign up with GitHub** (the free Hobby plan), then **Add New... > Project**, pick `option-simulator`, leave Framework Preset on **Other**, and click **Deploy**.
3. After about a minute you get a public link like `option-simulator.vercel.app`. A green "Live Yahoo data" label next to the title means the data connection is working.

## Good to know

- **Data source.** yfinance is an unofficial library that reads Yahoo Finance's website. It is free and works well for personal use, but Yahoo can change things or slow down heavy traffic. If the site says Yahoo did not respond, wait a minute and try again. A public site with lots of users should move to a licensed data service later; only `api/stock.py` would change.
- **Market hours.** Yahoo's option quotes are delayed about 15 minutes, and bid and ask often show 0 when the market is closed. The site then uses the last trade price and says so. Check the price again during market hours before buying.
- **Everything stays editable.** Any filled-in number can be overwritten. Research items filled from Yahoo show a small "Yahoo" tag, which disappears when you change the answer.
- **Caching.** Each ticker is fetched at most once every 10 minutes, so clicking around doesn't hammer Yahoo.
- **Sharing.** Suresh's original spreadsheet asks people not to share it. Since this site is built on his model, check with him before making the link public.

Not investment advice: the site applies your own rules to Yahoo's numbers.
