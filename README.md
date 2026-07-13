# ITS-FA

**Intelligent Tutoring System for Formal Automata and Formal Languages**

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![Flask](https://img.shields.io/badge/Flask-3.x-black)
![Docker](https://img.shields.io/badge/Docker-Ready-blue)
![Firebase](https://img.shields.io/badge/Firebase-Firestore-orange)
![AI](https://img.shields.io/badge/AI-Google%20Gemini-green)
![Vector%20Search](https://img.shields.io/badge/Vector%20Search-FAISS-purple)

ITS-FA is a Flask-based intelligent tutoring system for Formal Automata. It turns course material into structured learning content, stores course and learner data in Firebase Firestore, and uses Google Gemini plus FAISS vector search to support tutoring, quizzes, adaptive flashcards, video learning, recommendations, gamification, and automata practice.

For a detailed maintainer handoff, see [SUCCESSOR_HANDOFF.md](SUCCESSOR_HANDOFF.md).

## System Overview

The project combines several learning workflows into one web application:

- Course modules generated from automata textbook chapters and subchapters
- AI-generated questions, summaries, and adaptive flashcards
- Retrieval-augmented chat focused on automata theory
- Video learning pages with transcripts, summaries, checkpoints, and Q&A
- Student progress tracking, gamification, badges, streaks, and knowledge tracing
- Interactive regular expression and finite state machine practice tools
- Firebase-backed persistence for generated content and per-student data

Firebase Firestore is the current database, but it is not a permanent requirement. Future maintainers can migrate generated static content and student data to another datastore by replacing the Firestore reads/writes in `app.py`, `services/`, and the loading scripts under `scripts/`.

## Main Technologies

- **Backend:** Python, Flask, Gunicorn
- **Frontend:** Jinja templates, HTML, CSS, JavaScript
- **Database:** Firebase Firestore through Firebase Admin SDK
- **AI:** Google Gemini via `google-generativeai` and LangChain integrations
- **Retrieval:** FAISS vector indexes for textbook and video transcript retrieval
- **Deployment:** Docker, Gunicorn

## Project Structure

| Path | Purpose |
| --- | --- |
| `app.py` | Main Flask application, routes, Firebase setup, sessions, quizzes, videos, chat, flashcards, and APIs. |
| `chatbot.py` | Builds or loads the textbook retrieval chain using FAISS and Gemini embeddings. |
| `services/` | Shared application services for retrieval, video learning, gamification, knowledge tracing, recommendations, and flashcards. |
| `agents/` | Chat tutor agent components: explainer, examiner, diagnoser, and orchestrator. |
| `models/` | Shared type/model definitions. |
| `templates/` | Jinja templates for pages such as course, module, quiz, chat, video, flashcards, login, and dashboard. |
| `templates/home/` | Dashboard partial templates. |
| `static/css/` | Stylesheets. |
| `static/js/` | Frontend JavaScript for video learning and gamification UI. |
| `scripts/` | Data loading and generation scripts for chapters, subchapters, summaries, questions, flashcards, transcripts, and video checkpoints. |
| `sources/` | Source learning materials, including transcript files and textbook PDF assets. |
| `automata_vector_db/` | FAISS index for textbook/course retrieval. |
| `video_vector_db/` | Per-video FAISS indexes for transcript retrieval. |
| `.env.example` | Environment variable template. Do not put real secrets in this file. |
| `Dockerfile` | Container build and Gunicorn startup configuration. |

## Core Features

### Course And Module Learning

Course content is organized into sections such as introduction, finite state machines, context-free languages, Turing machines, complexity, logic/proofs, and applications. Module pages load chapter and subchapter data from Firestore.

### Quizzes And Adaptive Flashcards

The app supports chapter quizzes and adaptive flashcard paths. Generated quiz and flashcard content is stored in Firestore and evaluated through Flask routes. Student answers feed progress, gamification, and knowledge tracing.

### AI Tutor Chat

The chat feature uses a retrieval-augmented pipeline. It retrieves relevant passages from the automata textbook FAISS index and sends them to Gemini with a tutor prompt focused on automata theory.

### Video Learning

Video records are stored in Firestore under `videos`. Each video can include transcript text or transcript segments, checkpoint questions, generated summaries, and concept tags. Video transcript retrieval uses FAISS indexes under `video_vector_db/`.

### Gamification And Knowledge Tracing

The system tracks XP, badges, streaks, daily goals, activity, and concept mastery. These are updated when students complete chapters, answer quizzes or flashcards, watch videos, ask chat questions, or complete automata practice.

### Automata Practice

The drawer/practice tools support regular expression and finite state machine exercises, with AI-assisted relevance checks and feedback.

## Environment Variables

Copy the example file:

```bash
cp .env.example .env
```

Fill in the required values:

| Variable | Purpose |
| --- | --- |
| `FIREBASE_API_KEY` | Firebase web/client API key for frontend auth configuration. |
| `FIREBASE_AUTH_DOMAIN` | Firebase authentication domain. |
| `FIREBASE_PROJECT_ID` | Firebase project ID. |
| `FIREBASE_STORAGE_BUCKET` | Firebase storage bucket. |
| `FIREBASE_APP_ID` | Firebase web app ID. |
| `GOOGLE_API_KEY` | Google Gemini API key for generation and embeddings. |
| `GOOGLE_CREDS_B64` | Base64-encoded Firebase Admin service account JSON for backend access. |
| `SECRET_KEY` | Flask session signing secret. Use a long random value. |

The app can also load Firebase Admin credentials from `GOOGLE_CREDENTIALS_JSON`, but `GOOGLE_CREDS_B64` is the documented default.

Do not commit `.env`, service account JSON files, API keys, or private keys.

## Local Development

Use Python 3.9 or newer. Python 3.11 is recommended.

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Run the app:

```bash
python app.py
```

If the current branch does not start the server directly through `app.py`, run it through Flask:

```bash
flask --app app run --debug
```

Production-style local run:

```bash
gunicorn --bind 0.0.0.0:3000 app:app
```

## Docker

Build:

```bash
docker build -t its-fa .
```

Run:

```bash
docker run --env-file .env -p 3000:3000 its-fa
```

The container exposes port `3000` by default and starts Gunicorn with:

```bash
gunicorn --bind 0.0.0.0:${PORT:-3000} app:app
```

## Data Generation And Loading

The `scripts/` directory contains utilities for preparing and loading course data:

- `extractChapters.py`: extract chapter records
- `extractSubChapters.py`: extract subchapter records
- `extractSummaries.py`: generate summaries
- `generateQuestions.py`: generate quiz questions
- `generateAdaptiveFlashcards.py`: generate adaptive flashcards
- `import_video_transcript.py`: import video transcripts into Firestore
- `generateVideoCheckpoints.py`: generate video checkpoint questions

Some older scripts reference hardcoded paths such as `firebase.json` or `Automata Books/AutomataTheoryBook.pdf`. Check the script paths and credential loading before rerunning them.

## Important Routes

| Route | Purpose |
| --- | --- |
| `/` | Landing/index page. |
| `/login` | Login flow. |
| `/course` | Course overview. |
| `/module/<module_id>` | Chapter/module detail page. |
| `/quiz/<chapter_id>` | Chapter quiz page. |
| `/adaptive_flashcards/<chapter_id>` | Chapter adaptive flashcards. |
| `/adaptive_flashcards/section/<section_key>` | Section adaptive flashcards. |
| `/videos/section/<section_key>` | Section video list/page. |
| `/video/<video_id>` | Video learning page. |
| `/chat` | Tutor chat page. |
| `/drawer` | Automata/regex practice interface. |
| `/api/check-regex` | Regex practice evaluation API. |
| `/api/check-fsm` | FSM practice evaluation API. |
| `/api/knowledge_profile` | Learner knowledge profile API. |
| `/api/knowledge_recommendations` | Learner recommendation API. |

## Security Notes

Never share or commit:

- `.env`
- `service-account.json`
- `firebase.json`
- `GOOGLE_CREDS_B64`
- `GOOGLE_API_KEY`
- `SECRET_KEY`
- Any Firebase service account JSON/key file

If a service account key is exposed, rotate or delete it in Google Cloud IAM and generate a new one.

## Maintainer Notes

- `SUCCESSOR_HANDOFF.md` contains the more thorough handoff notes for a future maintainer.
- `.env.example` is safe to share; `.env` is not.
- Existing FAISS indexes let the app run without rebuilding embeddings.
- Rebuilding indexes or generated content requires valid Google/Firebase credentials.
- Firebase is the current database, but the generated static content and student tracking data can be migrated to another datastore if the Firestore access layer is replaced.
