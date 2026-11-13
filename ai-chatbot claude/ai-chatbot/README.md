# AI Chatbot (Flask + Google Gemini)

A simple web chat app. You ask questions, Gemini answers, and every chat is saved to a CSV file. The menu shows history, statistics, charts, and available models.

## Features

- Chat with Google Gemini in a clean, responsive interface
- Candid, nonjudgmental conversations across a wide range of topics
- Typing indicator, Enter-to-send, and auto-growing text box
- Chat history (last 5 chats)
- Statistics with NumPy (average, median, slowest response, prediction)
- Charts with Matplotlib and Seaborn
- List of Gemini models your key can use
- Reset the conversation at any time

## Requirements

- Python 3.10 or newer
- A Gemini API key from Google AI Studio

## Setup

1. **Open a terminal in the project folder**

   ```bash
   cd ai-chatbot
   ```

2. **Create and activate a virtual environment (recommended)**

   ```bash
   # macOS / Linux
   python3 -m venv venv
   source venv/bin/activate

   # Windows
   python -m venv venv
   venv\Scripts\activate
   ```

3. **Install the libraries**

   ```bash
   pip install -r requirements.txt
   ```

4. **Add your API key**

   Copy `.env.example` to a new file named `.env` and replace the placeholder with your key:

   ```text
   GEMINI_API_KEY=your_api_key_here
   ```

   Generate a private Flask session key with `python -c "import secrets; print(secrets.token_hex(32))"` and set it as `SECRET_KEY` in `.env`. Never reuse the example placeholder. Optionally set `GEMINI_MODEL` to a model available to your API key; the default is `gemini-flash-latest`.

## Run

```bash
python app.py
```

Open **http://127.0.0.1:5000** in your browser. Press `Ctrl + C` in the terminal to stop the server.

## Deploy to Render

1. Push this project to your GitHub repository, making sure `.env` is not committed.
2. In Render, create a **New Web Service** and connect the repository, or create a Blueprint from `render.yaml`.
3. For a dashboard-created service, use these settings:

   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`
   - **Branch:** `main`

4. For a dashboard-created service, add these environment variables in Render's private service settings:

   - `GEMINI_API_KEY` — your Google AI Studio API key (keep it in Render's private environment settings only)
   - `SECRET_KEY` — a unique, randomly generated value of at least 32 characters; generate one locally with `python -c "import secrets; print(secrets.token_hex(32))"`. The Blueprint generates this value automatically.
   - `GEMINI_MODEL` — optional for the Blueprint; defaults to `gemini-flash-latest`

The checked-in `render.yaml` defines the Python web service, `main` branch, build/start commands, `/health` check, secure-cookie setting, and non-secret model default. It does not contain the Gemini API key.

The app isolates each browser session's Gemini conversation, history, statistics, chart files, and chart URLs. It uses signed, HttpOnly, SameSite cookies, CSRF checks on write APIs, request limits, a per-session chat limit, and an additional process-wide chat limit. There are no user accounts: clearing browser cookies creates a new workspace, while anyone with access to that browser session can see its history. Do not use shared/public devices for sensitive chats. Rate limits and conversation state are in-process and apply per Render instance; a shared, distributed limiter and session store are needed before running multiple workers or instances.

Render's free filesystem is ephemeral, so saved chat history and charts may be lost after a restart or redeploy. Gemini conversation state is kept in process memory and resets after a restart or when an inactive session is evicted. Keep the Gunicorn worker count at one unless conversation and rate-limit state are moved to shared storage. Do not enable debug mode in a public deployment.

## Project structure

| File | Purpose |
|------|---------|
| `app.py` | Flask routes that connect the browser to the chatbot |
| `config.py` | Settings (API key, model, system prompt, folders) |
| `render.yaml` | Render web service, health check, and production commands |
| `.python-version` | Python runtime selected for Render |
| `gemini_client.py` | Talks to Gemini |
| `chat_logger.py` | Saves chats to `data/chat_history.csv` |
| `analytics.py` | Statistics and charts (saved to `charts/`) |
| `chatbot.py` | Original command-line version (still works) |
| `templates/index.html` | Page layout |
| `static/css/style.css` | Styling |
| `static/js/app.js` | Browser logic and API calls |

## API routes

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | Main page |
| POST | `/api/ask` | Send `{"question": "..."}` and get the answer |
| POST | `/api/reset` | Start a new Gemini conversation |
| GET | `/api/history` | Last 5 chats |
| GET | `/api/stats` | Summary statistics |
| POST | `/api/charts` | Create charts and list the images |
| GET | `/api/models` | Models your API key can use |
| GET | `/charts/<file>` | Serve a chart image |
| GET | `/health` | Render service health check |

## Troubleshooting

- **Chatbot is not configured**: Set `GEMINI_API_KEY` in `.env` (local) or Render's private environment settings.
- **"Set SECRET_KEY..."**: Generate a unique secret with `python -c "import secrets; print(secrets.token_hex(32))"` and set it as `SECRET_KEY` in `.env` or Render's private environment settings.
- **"Address already in use"**: Another program uses port 5000. In PowerShell, run `$env:PORT=5001` before `python app.py`.
- **Charts say "Ask at least 2 questions first"**: Ask at least two questions, then try again.
- **Stats or charts look wrong after deleting the CSV**: Delete the `charts/` folder too, then ask a new question.

## Security notes

- Keep `.env` out of Git; `.gitignore` excludes `.env` and local secrets. `.env.example` contains placeholders only.
- Never place `GEMINI_API_KEY` or `SECRET_KEY` in frontend files or commit either secret.
- Rate limits and session conversations are process-local; use a shared session store and distributed limiter before scaling to multiple workers or instances.
