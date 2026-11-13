# Deploy & open on Android (all free, all from the phone browser)

Use Chrome on Android; tick **Desktop site** in the Chrome menu for GitHub/Streamlit setup pages if they look cramped.

## 1. Put the code on GitHub
1. Create a free account at github.com → **+** → **New repository** → name `bc-research-ai`, **Public**.
2. Download this project as a ZIP (from where you received it) and extract it using the Files app.
3. In the repo page choose **Add file → Upload files**, upload the extracted contents (keep the folders; `app.py` and `requirements.txt` must be at the top level), then **Commit changes**.
   *Easier alternative:* open **github.com/codespaces** and use the browser editor to upload the folder and run `pytest`.
4. Do **not** upload `.env` or any secrets (`.gitignore` already excludes them).

## 2. Deploy on Streamlit Community Cloud
1. Go to **share.streamlit.io** → sign in with GitHub.
2. **Create app** → pick the repo, branch `main`, main file `app.py`.
3. *Advanced settings*: choose Python 3.11 or 3.12 → **Deploy**. First build takes a few minutes.
4. Your app URL looks like `https://<name>.streamlit.app`.

## 3. Open it on your phone
Open the URL in Chrome → menu ⋮ → **Add to Home screen** → **Install**. You get a ₹1,000 virtual demo budget immediately. Tap the **>** arrow at top-left for controls.

## 4. Optional persistence (Supabase)
Follow "Supabase" in the README. Without it, data may reset when the free app restarts or sleeps.

## Troubleshooting
- *Build fails on psycopg*: remove the `psycopg` line from `requirements.txt` (demo mode doesn't need it).
- *Balances reset*: ephemeral disk on free hosting; use Supabase.
- *Research summary empty*: sidebar → **Run background research now**, wait a minute, reload.
- Free apps sleep when idle; the first load after sleep is slow. Background jobs do not run while asleep.
