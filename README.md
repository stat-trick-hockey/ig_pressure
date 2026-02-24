# NHL Schedule Pressure Cards — GitHub Actions Setup

Automatically generates and publishes daily NHL schedule pressure card images to GitHub Pages.

---

## Files

```
your-repo/
├── .github/
│   └── workflows/
│       └── daily_pressure_card.yml   ← GitHub Actions workflow
├── nhl_schedule_pressure_card.py     ← Main script
├── nhl_arenas.csv                    ← Arena lat/lon data (you provide this)
├── stat_trick_logo.png               ← Your logo (you provide this)
├── requirements.txt                  ← Python dependencies
└── docs/
    └── ig_pressure/                  ← Output images land here (auto-created)
```

---

## One-Time Setup

### 1. Add your GitHub Token as a Secret

1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `GH_TOKEN`
4. Value: your fine-grained PAT with **Contents: Read and Write** permission on this repo
5. Click **Add secret**

> ⚠️ The token is no longer hardcoded in the script. It is read from this secret at runtime.

### 2. Enable GitHub Pages (optional, for public image URLs)

1. Go to **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: `main`, Folder: `/docs`
4. Save

Images will be accessible at:
`https://stat-trick-hockey.github.io/ig_pressure/ig_pressure/ig_schedule_pressure_YYYY-MM-DD_p1.png`

### 3. Commit all files to your repo

Make sure these are in the root of your repo:
- `nhl_schedule_pressure_card.py`
- `nhl_arenas.csv`
- `stat_trick_logo.png`
- `requirements.txt`
- `.github/workflows/daily_pressure_card.yml`

---

## Schedule

The workflow runs automatically at **10:00 AM UTC (6:00 AM ET)** every day.

To run it manually:
1. Go to the **Actions** tab in your repo
2. Click **Daily NHL Schedule Pressure Cards**
3. Click **Run workflow**

---

## Changing the Run Time

Edit the cron line in `.github/workflows/daily_pressure_card.yml`:

```yaml
- cron: '0 10 * * *'   # 10:00 UTC = 6:00 AM ET
- cron: '0 12 * * *'   # 12:00 UTC = 8:00 AM ET
- cron: '0 14 * * *'   # 14:00 UTC = 10:00 AM ET
```

Use https://crontab.guru to build cron expressions.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `GITHUB_TOKEN is not set` | Make sure you added `GH_TOKEN` as a repo secret |
| Empty slides (no games) | Try running later in the morning — NHL API may not have data yet |
| Font fallback (blurry text) | Ubuntu runner uses DejaVu fonts automatically — this is expected |
| Logo missing | Make sure `stat_trick_logo.png` is committed to the repo root |
