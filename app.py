from flask import Flask, render_template, redirect, url_for, abort, request, session, jsonify
from dotenv import load_dotenv
import os, base64
import json
import logging
import re
import firebase_admin
from firebase_admin import credentials, firestore, auth, initialize_app
from whitenoise import WhiteNoise
from chatbot import create_chain
from agents.diagnoser_agent import DiagnoserAgent
from agents.examiner_agent import ExaminerAgent
from agents.explainer_agent import ExplainerAgent
from agents.orchestrator import ChatOrchestrator
import random 
import traceback
import google.generativeai as genai
from services.chat_state_service import ChatStateService
from services.adaptive_flashcard_service import (
    LEVELS,
    answer_flashcard,
    build_progress_update_payload,
    choose_next_flashcard,
    default_progress_state,
    get_level_statuses,
    get_user_flashcard_progress,
    normalize_progress_state,
    sync_progress_with_flashcards,
    validate_adaptive_flashcards,
)
from services.retrieval_service import RetrievalService
from services.video_catalog import build_section_video_meta, get_video_by_id, list_section_videos
from services.video_retrieval_service import VideoRetrievalService
from services.video_recommendation_service import get_recommended_videos
from services.video_summary_service import VideoSummaryService
from services.video_transcript_service import ensure_video_transcript, extract_youtube_video_id


SECTION_CONFIGS = [
    {"key": "introduction", "title": "Introduction", "chapter_ids": list(range(1, 5))},
    {"key": "fsm_regular", "title": "Finite State Machines & Regular Languages", "chapter_ids": list(range(5, 10))},
    {"key": "cfl_pda", "title": "Context-Free Languages and Pushdown Automata", "chapter_ids": list(range(10, 15))},
    {"key": "tm_undecidability", "title": "Turing Machines and Undecidability", "chapter_ids": list(range(16, 26))},
    {"key": "complexity", "title": "Complexity", "chapter_ids": list(range(27, 31))},
    {"key": "logic_proofs", "title": "Logics, Theories, and Proofs", "chapter_ids": list(range(33, 38))},
    {"key": "applications", "title": "Applications Throughout the World", "chapter_ids": list(range(38, 49))},
]
SECTION_LOOKUP = {config["key"]: config for config in SECTION_CONFIGS}
SECTION_LEVEL_CARD_COUNT = 10

load_dotenv()
b64 = os.environ.get("GOOGLE_CREDS_B64")
if not b64:
    raise RuntimeError("Missing GOOGLE_CREDS_B64")

creds_dict = json.loads(base64.b64decode(b64))
initialize_app(credentials.Certificate(creds_dict))
db = firestore.client()
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel("gemini-2.5-flash-lite")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
app.wsgi_app = WhiteNoise(app.wsgi_app, root='static/', prefix='static/')

# Directories for static content
STATIC_DIR = "static"
VIDEOS_DIR = os.path.join(STATIC_DIR, "videos")
CHATS_DIR = "chats"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize RAG chain
try:
    chain = create_chain()
    if chain is None:
        raise Exception("Failed to initialize the RAG chain")
except Exception as e:
    logging.error(f"Chain initialization error: {e}")
    chain = None 

retrieval_service = RetrievalService()
video_retrieval_service = VideoRetrievalService()
video_summary_service = VideoSummaryService()
chat_state_service = ChatStateService()
explainer_agent = ExplainerAgent(retrieval_service)
examiner_agent = ExaminerAgent()
diagnoser_agent = DiagnoserAgent(retrieval_service)
chat_orchestrator = ChatOrchestrator(
    explainer_agent=explainer_agent,
    examiner_agent=examiner_agent,
    diagnoser_agent=diagnoser_agent,
    chat_state_service=chat_state_service,
)

def shuffle_list(seq):
    shuffled = list(seq)
    random.shuffle(shuffled)
    return shuffled

app.jinja_env.filters['shuffle'] = shuffle_list


def _load_video_learning_progress() -> dict:
    progress = session.get("video_learning_progress", {})
    if not isinstance(progress, dict):
        progress = {}
    return progress


def _save_video_learning_progress(progress: dict) -> None:
    session["video_learning_progress"] = progress
    session.modified = True


def _get_video_progress(video_id: str) -> dict:
    progress = _load_video_learning_progress()
    current = progress.get(video_id, {})
    if not isinstance(current, dict):
        current = {}
    return {
        "selected_video_id": progress.get("selected_video_id"),
        "answered_checkpoint_ids": list(current.get("answered_checkpoint_ids", [])),
        "summary_shown": bool(current.get("summary_shown", False)),
        "completed": bool(current.get("completed", False)),
        "watched_seconds": float(current.get("watched_seconds", 0.0) or 0.0),
        "duration_seconds": float(current.get("duration_seconds", 0.0) or 0.0),
        "watched_percentage": float(current.get("watched_percentage", 0.0) or 0.0),
    }


def _merge_video_progress(video_id: str, updates: dict) -> dict:
    progress = _load_video_learning_progress()
    current = _get_video_progress(video_id)
    current.update(updates)
    answered_ids = current.get("answered_checkpoint_ids", [])
    current["answered_checkpoint_ids"] = list(dict.fromkeys(str(item) for item in answered_ids if str(item)))
    progress[video_id] = current
    progress["selected_video_id"] = video_id
    _save_video_learning_progress(progress)
    return current

# Routes
@app.route("/")
def index():
    if not session.get('user_id'):
         return redirect(url_for("login"))
    user_id = session['user_id']
    user_doc = db.collection("Users").document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    chapters_read = user_data.get("chapters_read", 0)
    quizzes_attempted = user_data.get("quizzes_attempted", 0)
    quizzes_completed = user_data.get("quizzes_completed", 0)

    return render_template(
        "home.html",
        chapters_read=chapters_read,
        quizzes_attempted=quizzes_attempted,
        quizzes_completed=quizzes_completed
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    # If user is already logged in, send them home
    if session.get("user_id"):
        return redirect(url_for("index"))

    if request.method == "POST":
        # Get the password typed by the user
        input_passcode = request.form.get("password", "").strip()
        
        # Query the 'passwords' collection 
        # We look for any document where the field 'passcode' matches input
        docs_stream = db.collection("passwords").where("password", "==", input_passcode).limit(1).stream()
        docs = list(docs_stream)

        if docs:
            # Login Successful!
            found_doc = docs[0]
            
            # We use the document ID (e.g., "group1") as the User ID
            # This allows multiple people to share the "group1" login and progress
            user_id = found_doc.id 
            session["user_id"] = user_id
            
            # Ensure a User document exists in 'Users' collection so progress can be saved
            user_ref = db.collection("Users").document(user_id)
            if not user_ref.get().exists:
                user_ref.set({
                    "uid": user_id,
                    "email": "passcode_user", # Placeholder
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "chapters_read": 0,
                    "read_chapters": [],
                    "quizzes_attempted": 0,
                    "quizzes_completed": 0,
                    "answers": {}
                })
            
            return redirect(url_for("index"))
        else:
            # Login Failed
            return render_template("login_simple.html", error="Invalid Passcode. Please try again.")

    return render_template("login_simple.html")

@app.route('/logout', methods=['GET', 'POST']) 
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

# @app.route("/signup", methods=["GET", "POST"])
# def signup():
#     firebase_config = {
#         "firebase_api_key": os.getenv("FIREBASE_API_KEY"),
#         "firebase_auth_domain": os.getenv("FIREBASE_AUTH_DOMAIN"),
#         "firebase_project_id": os.getenv("FIREBASE_PROJECT_ID"),
#         "firebase_storage_bucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
#         "firebase_app_id": os.getenv("FIREBASE_APP_ID")
#     }
#     if request.method == "POST":
#         email = request.form.get("email")
#         password = request.form.get("password")

#         try:
#             user = auth.get_user_by_email(email)
#             return render_template("signup.html", error="This email is already in use", **firebase_config)
#         except auth.UserNotFoundError:
#             pass
#         password_pattern = r"^(?=.*[A-Z])(?=.*\d)(?=.*[\W_]).{8,}$"
#         if not re.match(password_pattern, password):
#             return render_template("signup.html", error="Password must be at least 8 characters long, contain at least one number, one uppercase letter, and one symbol. ", **firebase_config)

#         try:
#             # Create user with Firebase Authentication
#             user = auth.create_user(email=email, password=password)
#             session["user_id"] = user.uid  # Store user session

#             # Store user in Firestore
#             user_data = {
#                 "email": email,
#                 "uid": user.uid,
#                 "created_at": firestore.SERVER_TIMESTAMP,  # Adds timestamp
#                 "chapters_read": 0,
#                 "read_chapters": [],
#                 "quizzes_attempted": 0,
#                 "quizzes_completed": 0
#             }
#             db.collection("Users").document(user.uid).set(user_data)

#             return redirect(url_for("index"))  # Redirect to index on successful signup

#         except Exception as e:
#             logging.error(f"Error signing up: {e}, traceback: {traceback.format_exc()}")
#             return render_template("signup.html", error="Unable to signup, please make sure you have a valid email and password.", **firebase_config)

#     return render_template("signup.html", **firebase_config)

# @app.route("/login", methods=["GET", "POST"])
# def login():
#     firebase_config = {
#         'firebase_api_key': os.getenv('FIREBASE_API_KEY'),
#         'firebase_auth_domain': os.getenv('FIREBASE_AUTH_DOMAIN'),
#         'firebase_project_id': os.getenv('FIREBASE_PROJECT_ID'),
#         'firebase_storage_bucket': os.getenv('FIREBASE_STORAGE_BUCKET'),
#         'firebase_app_id': os.getenv('FIREBASE_APP_ID'),
#     }
#     return render_template("login.html", **firebase_config)


# @app.route('/logout', methods=['POST'])
# def logout():
#     session.pop('user_id', None) 
#     return redirect(url_for('index'))

# @app.route("/validate_token", methods=["POST"])
# def validate_token():
#     # Get the ID token from the request
#     data = request.get_json()
#     id_token = data.get('idToken')

#     if not id_token:
#         return jsonify({"error": "No token provided"}), 400

#     try:
#         # Verify the ID token
#         decoded_token = auth.verify_id_token(id_token)
#         uid = decoded_token['uid']

#         # Store user session after verifying token
#         session["user_id"] = uid
#         return jsonify({"success": True})  # User authenticated successfully

#     except Exception as e:
#         logging.error(f"Error verifying token: {e}")
#         return jsonify({"error": "Invalid token or session expired"}), 401

@app.before_request
def before_request():
  if not session.get('user_id') and request.endpoint not in ['login', 'static', 'index', 'validate_token']:
        return redirect(url_for('login'))

@app.route('/save_answer', methods=['POST'])
def save_answer():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session['user_id']
    try:
        data = request.get_json() 
        print(f"Received data: {data}")
        question_id = data.get("questionId")
        answer = data.get("answer")

        if not question_id or answer is None:
            return jsonify({"error": "Invalid data: questionId and answer are required"}), 400

    except Exception as e:
        print(f"Error parsing JSON: {e}")
        return jsonify({"error": f"Invalid JSON: {str(e)}"}), 400 

    try:
        user_ref = db.collection("Users").document(user_id)

        # Get existing answers and update only the new one
        user_doc = user_ref.get()
        existing_data = user_doc.to_dict() or {}
        existing_answers = existing_data.get("answers", {})

        # Update only the relevant answer
        existing_answers[question_id] = answer

        user_ref.set({
            "answers": existing_answers
        }, merge=True)

        return jsonify({"message": "Answer saved successfully"})

    except Exception as e:
        print(f"Firestore error: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/increment_chapter_read/<int:chapter_id>', methods=['POST'])
def increment_chapter_read(chapter_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session['user_id']
    user_ref = db.collection("Users").document(user_id)

    try:
        user_doc = user_ref.get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            current_count = user_data.get('chapters_read', 0)
            user_ref.update({'chapters_read': current_count + 1})
            read_chapters = user_data.get('read_chapters', [])

            if chapter_id not in read_chapters:
                read_chapters.append(chapter_id)
                user_ref.update({"read_chapters": read_chapters})

        else:
            user_ref.set({
                'chapters_read': 1,
                'read_chapters': [chapter_id]
            }, merge=True)

        return jsonify({"message": "Chapter read count incremented"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/mark_unread/<int:chapter_id>', methods=['POST'])
def mark_unread(chapter_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session['user_id']
    user_ref = db.collection("Users").document(user_id)

    try:
        user_doc = user_ref.get()
        if not user_doc.exists:
            return jsonify({"error": "User not found"}), 404
        
        user_data = user_doc.to_dict()
        current_count = user_data.get('chapters_read', 0)
        if current_count > 0:
            user_ref.update({'chapters_read': current_count - 1})
        
        read_chapters = user_data.get('read_chapters', [])
        if chapter_id in read_chapters:
            read_chapters.remove(chapter_id)
            user_ref.update({'read_chapters': read_chapters})

        return jsonify({"message": "Chapter successfully marked as unread"}), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/lessons")
def lessons():
    return redirect(url_for("course_page"))


@app.route("/course")
def course_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))  # or however you handle unauthorized access

    user_id = session['user_id']
    user_doc = db.collection("Users").document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    user_answers = user_data.get("answers", {})
    read_chapters = user_data.get("read_chapters", [])
    adaptive_progress = user_data.get("adaptive_flashcard_progress", {})
    adaptive_section_progress = user_data.get("adaptive_flashcard_section_progress", {})
    chapters_ref = db.collection("chapters")
    chapters = []

    docs = chapters_ref.stream()
    for doc in docs:
        chapter = doc.to_dict()
        # Convert document ID to integer for easier sorting
        chapter["id"] = int(doc.id)
        chapter_progress = adaptive_progress.get(str(chapter["id"]), {})
        chapter["flashcard_path_completed"] = bool(chapter_progress.get("path_completed", False))
        chapters.append(chapter)
    chapters.sort(key=lambda x: x["id"])

    section_flashcard_meta = {}
    for config in SECTION_CONFIGS:
        section_key = config["key"]
        section_chapters = [chapter for chapter in chapters if chapter["id"] in config["chapter_ids"]]
        section_has_flashcards = any(chapter.get("adaptive_flashcards") for chapter in section_chapters)
        section_progress = adaptive_section_progress.get(section_key, {})
        section_flashcard_meta[section_key] = {
            "available": section_has_flashcards,
            "completed": bool(section_progress.get("path_completed", False)),
        }
    section_video_meta = build_section_video_meta(db, SECTION_CONFIGS)

    return render_template(
        "course.html",
        chapters=chapters,
        user_answers=user_answers,
        read_chapters=read_chapters,
        section_flashcard_meta=section_flashcard_meta,
        section_video_meta=section_video_meta,
    )



@app.route("/module/<int:module_id>")
def module_detail(module_id):
    chapter_ref = db.collection("chapters").document(str(module_id))
    chapter = chapter_ref.get()

    if not chapter.exists:
        abort(404) 

    chapter_data = chapter.to_dict()
    chapter_data["id"] = module_id
    subchapters_ref = db.collection("subchapters").where("chapter_id", "==", module_id)
    subchapters = []
    subchapter_docs = subchapters_ref.stream()

    for doc in subchapter_docs:
        subchapter = doc.to_dict()
        subchapter["id"] = doc.id
        subchapters.append(subchapter)

    return render_template(
        "module_detail.html", module=chapter_data, subchapters=subchapters
    )


@app.route("/videos/section/<section_key>")
def section_video_page(section_key):
    section_config = SECTION_LOOKUP.get(section_key)
    if not section_config:
        abort(404)

    section_videos = list_section_videos(db, section_key)
    if not section_videos:
        abort(404)

    selected_video_id = request.args.get("video_id", "").strip()
    if selected_video_id and any(video["id"] == selected_video_id for video in section_videos):
        return redirect(url_for("video_learning_page", video_id=selected_video_id))
    return redirect(url_for("video_learning_page", video_id=section_videos[0]["id"]))


@app.route("/video/<video_id>")
def video_learning_page(video_id):
    video_record = get_video_by_id(db, video_id)
    if not video_record:
        abort(404)

    section_key = str(video_record.get("section_key", "")).strip()
    section_config = SECTION_LOOKUP.get(section_key)
    section_videos = list_section_videos(db, section_key) if section_key else []

    transcript_status = None
    try:
        video_record, transcript_status = ensure_video_transcript(db, video_record)
    except Exception as exc:
        logging.warning("Video transcript bootstrap failed for %s: %s", video_id, exc)
        transcript_status = "The transcript could not be loaded automatically."

    if video_record.get("transcript_text"):
        try:
            video_retrieval_service.ensure_vector_store(video_record)
        except Exception as exc:
            logging.warning("Video vector store bootstrap failed for %s: %s", video_id, exc)

    progress = _merge_video_progress(video_id, {})
    summary_payload = video_record.get("generated_summary")
    transcript_preview = str(video_record.get("transcript_text", "")).strip()[:1400]
    transcript_available = bool(str(video_record.get("transcript_text", "")).strip())
    youtube_video_id = str(video_record.get("youtube_video_id", "")).strip()
    if not youtube_video_id:
        youtube_video_id = extract_youtube_video_id(str(video_record.get("url", "")).strip()) or ""
    summary_visible = transcript_available or (
        isinstance(summary_payload, dict) and bool(summary_payload.get("narrative_summary"))
    )

    return render_template(
        "video_page.html",
        video=video_record,
        section_title=section_config["title"] if section_config else "Video Learning",
        section_key=section_key,
        section_videos=section_videos,
        transcript_available=transcript_available,
        transcript_status=transcript_status,
        transcript_preview=transcript_preview,
        summary_payload=summary_payload if isinstance(summary_payload, dict) else None,
        summary_visible=summary_visible,
        video_progress=progress,
        youtube_video_id=youtube_video_id,
        ask_endpoint=url_for("video_question_api", video_id=video_id),
        progress_endpoint=url_for("video_progress_api", video_id=video_id),
        summary_endpoint=url_for("video_summary_api", video_id=video_id),
        transcript_refresh_endpoint=url_for("video_transcript_refresh_api", video_id=video_id),
    )


@app.route("/video/<video_id>/transcript/refresh", methods=["POST"])
def video_transcript_refresh_api(video_id):
    video_record = get_video_by_id(db, video_id)
    if not video_record:
        return jsonify({"error": "Video not found"}), 404

    try:
        updated_record, transcript_status = ensure_video_transcript(db, video_record)
    except Exception as exc:
        logging.error("Manual transcript refresh failed for %s: %s", video_id, exc)
        return jsonify(
            {
                "transcript_available": False,
                "message": "Transcript retrieval failed.",
                "selected_video_id": video_id,
            }
        ), 200

    transcript_available = bool(str(updated_record.get("transcript_text", "")).strip())
    if transcript_available:
        try:
            video_retrieval_service.ensure_vector_store(updated_record, force_rebuild=True)
        except Exception as exc:
            logging.warning("Video vector store rebuild failed for %s: %s", video_id, exc)

    return jsonify(
        {
            "transcript_available": transcript_available,
            "message": transcript_status or ("Transcript loaded successfully." if transcript_available else "Transcript is unavailable."),
            "transcript_preview": str(updated_record.get("transcript_text", "")).strip()[:1400],
            "selected_video_id": video_id,
        }
    )


@app.route("/video/<video_id>/ask", methods=["POST"])
def video_question_api(video_id):
    request_data = request.get_json(silent=True) or {}
    user_message = str(request_data.get("message", "")).strip()
    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    video_record = get_video_by_id(db, video_id)
    if not video_record:
        return jsonify({"error": "Video not found"}), 404

    try:
        video_record, transcript_status = ensure_video_transcript(db, video_record)
    except Exception as exc:
        logging.error("Video transcript ensure failed for %s: %s", video_id, exc)
        transcript_status = "The video transcript is currently unavailable."

    payload = video_retrieval_service.answer_video_question(video_record, user_message)
    if transcript_status and not str(video_record.get("transcript_text", "")).strip():
        payload["response_text"] = transcript_status
    return jsonify(payload)


@app.route("/video/<video_id>/summary", methods=["POST"])
def video_summary_api(video_id):
    video_record = get_video_by_id(db, video_id)
    if not video_record:
        return jsonify({"error": "Video not found"}), 404

    try:
        video_record, transcript_status = ensure_video_transcript(db, video_record)
    except Exception as exc:
        logging.error("Summary transcript ensure failed for %s: %s", video_id, exc)
        transcript_status = "The transcript is currently unavailable."

    summary_payload, error_message = video_summary_service.load_or_generate_summary(db, video_record)
    if summary_payload:
        progress = _merge_video_progress(video_id, {"summary_shown": True})
        return jsonify(
            {
                "summary": summary_payload,
                "selected_video_id": video_id,
                "summary_shown": progress["summary_shown"],
                "message": "Summary ready.",
            }
        )

    return jsonify(
        {
            "summary": None,
            "selected_video_id": video_id,
            "message": error_message or transcript_status or "Summary unavailable.",
        }
    )


@app.route("/video/<video_id>/progress", methods=["POST"])
def video_progress_api(video_id):
    request_data = request.get_json(silent=True) or {}
    current_progress = _get_video_progress(video_id)

    checkpoint_id = str(request_data.get("answered_checkpoint_id", "")).strip()
    answered_checkpoint_ids = current_progress["answered_checkpoint_ids"]
    if checkpoint_id and checkpoint_id not in answered_checkpoint_ids:
        answered_checkpoint_ids.append(checkpoint_id)

    watched_seconds = request_data.get("watched_seconds", current_progress["watched_seconds"])
    duration_seconds = request_data.get("duration_seconds", current_progress["duration_seconds"])
    try:
        watched_seconds = float(watched_seconds or 0.0)
    except (TypeError, ValueError):
        watched_seconds = current_progress["watched_seconds"]
    try:
        duration_seconds = float(duration_seconds or 0.0)
    except (TypeError, ValueError):
        duration_seconds = current_progress["duration_seconds"]

    watched_percentage = 0.0
    if duration_seconds > 0:
        watched_percentage = min(100.0, max(0.0, (watched_seconds / duration_seconds) * 100))

    updated_progress = _merge_video_progress(
        video_id,
        {
            "answered_checkpoint_ids": answered_checkpoint_ids,
            "watched_seconds": watched_seconds,
            "duration_seconds": duration_seconds,
            "watched_percentage": watched_percentage,
            "completed": bool(request_data.get("completed", current_progress["completed"])),
            "summary_shown": bool(request_data.get("summary_shown", current_progress["summary_shown"])),
        },
    )
    return jsonify({"progress": updated_progress, "selected_video_id": video_id})


    
@app.route('/quiz/<int:chapter_id>')
def quiz_page(chapter_id):
    chapter_ref = db.collection('chapters').document(str(chapter_id))
    chapter_data = chapter_ref.get().to_dict()

    if not chapter_data:
        return "Chapter not found", 404

    questions = chapter_data.get("questions", [])
    correct_answers = chapter_data.get("correct_answers", [])
    incorrect_answers = chapter_data.get("incorrect_answers", [])
    hints = chapter_data.get("hints", [])
    question_concepts = chapter_data.get("question_concepts", [])

    return render_template(
        'quiz.html',
        chapter_id=chapter_id,
        questions=questions,
        correct_answers=correct_answers,
        incorrect_answers=incorrect_answers,
        hints=hints,
        question_concepts=question_concepts,
        zip=zip 
    )


@app.route('/quiz_result', methods=['POST'])
def quiz_result():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session['user_id']
    user_ref = db.collection("Users").document(user_id)
    data = request.get_json(silent=True) or {}
    score = data.get('score', 0)
    total = data.get('total', 0)
    chapter_id = data.get('chapter_id')
    wrong_questions = data.get('wrong_questions', [])

    try:
        logging.info(
            "Quiz submitted by user=%s chapter=%s score=%s/%s",
            user_id,
            chapter_id,
            score,
            total,
        )

        recommended_videos = []
        normalized_chapter_id = None
        if chapter_id is not None:
            try:
                normalized_chapter_id = int(chapter_id)
            except (TypeError, ValueError):
                logging.warning("Quiz result received invalid chapter_id=%s", chapter_id)

        if normalized_chapter_id is not None and score < total:
            chapter_ref = db.collection("chapters").document(str(normalized_chapter_id))
            chapter_doc = chapter_ref.get()
            chapter_data = chapter_doc.to_dict() if chapter_doc.exists else {}
            recommended_videos = get_recommended_videos(
                chapter_data=chapter_data or {},
                chapter_id=normalized_chapter_id,
                wrong_questions=wrong_questions if isinstance(wrong_questions, list) else [],
            )
        logging.info(
            "Recommended videos returned for chapter=%s: %s",
            chapter_id,
            len(recommended_videos),
        )

        user_doc = user_ref.get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            current_attempted = user_data.get('quizzes_attempted', 0)
            user_ref.update({'quizzes_attempted': current_attempted + 1})

            if score == total:
                current_completed = user_data.get('quizzes_completed', 0)
                user_ref.update({'quizzes_completed': current_completed + 1})

            return jsonify(
                {
                    "message": "Quiz result recorded",
                    "score": score,
                    "total": total,
                    "recommended_videos": recommended_videos,
                }
            ), 200
        else:
            # If user doc doesn't exist, create it with default fields
            # e.g. if user somehow wasn't created in signup
            new_data = {
                'quizzes_attempted': 1, 
                'quizzes_completed': 1 if score == total else 0
            }
            user_ref.set(new_data, merge=True)
            return jsonify(
                {
                    "message": "User doc created and quiz result recorded",
                    "score": score,
                    "total": total,
                    "recommended_videos": recommended_videos,
                }
            ), 200

    except Exception as e:
        logging.error("Quiz result handling failed: %s", e)
        return jsonify({"error": str(e)}), 500


def _get_current_user_ref():
    user_id = session.get("user_id")
    if not user_id:
        return None, None, (jsonify({"error": "Unauthorized"}), 401)
    user_ref = db.collection("Users").document(user_id)
    return user_id, user_ref, None


def _load_adaptive_flashcard_context(chapter_id: int):
    chapter_ref = db.collection("chapters").document(str(chapter_id))
    chapter_doc = chapter_ref.get()
    if not chapter_doc.exists:
        abort(404)

    chapter_data = chapter_doc.to_dict() or {}
    adaptive_flashcards = chapter_data.get("adaptive_flashcards")
    if not adaptive_flashcards:
        return chapter_data, None, None, "Adaptive flashcards have not been generated for this chapter yet."

    flashcards_by_level = validate_adaptive_flashcards(adaptive_flashcards)
    _, user_ref, error_response = _get_current_user_ref()
    if error_response:
        return chapter_data, None, None, error_response

    user_doc = user_ref.get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    chapter_progress = get_user_flashcard_progress(user_data, chapter_id)
    chapter_progress = sync_progress_with_flashcards(chapter_progress, flashcards_by_level)
    user_ref.set(build_progress_update_payload(user_data, chapter_id, chapter_progress), merge=True)

    return chapter_data, flashcards_by_level, chapter_progress, None


def _get_section_config(section_key: str):
    return next((config for config in SECTION_CONFIGS if config["key"] == section_key), None)


def _aggregate_section_flashcards(section_key: str):
    section_config = _get_section_config(section_key)
    if not section_config:
        abort(404)

    section_chapter_docs = []
    aggregated_flashcards = {level: [] for level in LEVELS}

    for chapter_id in section_config["chapter_ids"]:
        chapter_doc = db.collection("chapters").document(str(chapter_id)).get()
        if not chapter_doc.exists:
            continue
        chapter_data = chapter_doc.to_dict() or {}
        section_chapter_docs.append({"id": chapter_id, **chapter_data})
        chapter_flashcards = chapter_data.get("adaptive_flashcards")
        if not chapter_flashcards:
            continue

        validated_flashcards = validate_adaptive_flashcards(chapter_flashcards)
        chapter_title = chapter_data.get("title", f"Chapter {chapter_id}")
        for level, cards in validated_flashcards.items():
            for card in cards:
                section_card = dict(card)
                section_card["id"] = f"{chapter_id}:{card['id']}"
                section_card["concept"] = f"{chapter_title} - {card.get('concept', '')}".strip(" -")
                aggregated_flashcards[level].append(section_card)

    if not any(aggregated_flashcards[level] for level in LEVELS):
        return section_config, section_chapter_docs, None

    return section_config, section_chapter_docs, aggregated_flashcards


def _select_section_flashcard_subset(
    aggregated_flashcards: dict[str, list[dict]],
    section_progress: dict,
) -> tuple[dict[str, list[dict]], dict]:
    selected_flashcards = {level: [] for level in LEVELS}

    for level in LEVELS:
        level_cards = aggregated_flashcards.get(level, [])
        if not level_cards:
            section_progress["levels"][level]["selected_ids"] = []
            continue

        target_count = min(SECTION_LEVEL_CARD_COUNT, len(level_cards))
        level_state = section_progress["levels"][level]
        card_by_id = {card["id"]: card for card in level_cards}

        selected_ids = [card_id for card_id in level_state.get("selected_ids", []) if card_id in card_by_id]
        if len(selected_ids) > target_count:
            selected_ids = selected_ids[:target_count]

        if len(selected_ids) < target_count:
            remaining_cards = [card for card in level_cards if card["id"] not in selected_ids]
            random.shuffle(remaining_cards)
            selected_ids.extend(card["id"] for card in remaining_cards[: target_count - len(selected_ids)])

        level_state["selected_ids"] = selected_ids
        selected_flashcards[level] = [card_by_id[card_id] for card_id in selected_ids if card_id in card_by_id]

    return selected_flashcards, section_progress


def _load_adaptive_flashcard_section_context(section_key: str):
    section_config, section_chapter_docs, flashcards_by_level = _aggregate_section_flashcards(section_key)
    if flashcards_by_level is None:
        return section_config, None, None, "Adaptive flashcards have not been generated for this section yet."

    _, user_ref, error_response = _get_current_user_ref()
    if error_response:
        return section_config, None, None, error_response

    user_doc = user_ref.get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    adaptive_section_progress = user_data.get("adaptive_flashcard_section_progress", {})
    section_progress = normalize_progress_state(adaptive_section_progress.get(section_key))
    flashcards_by_level, section_progress = _select_section_flashcard_subset(flashcards_by_level, section_progress)
    section_progress = sync_progress_with_flashcards(section_progress, flashcards_by_level)

    adaptive_section_progress[section_key] = section_progress
    user_ref.set({"adaptive_flashcard_section_progress": adaptive_section_progress}, merge=True)

    return section_config, flashcards_by_level, section_progress, None


def _build_flashcard_page_context(chapter_id: int):
    chapter_data, flashcards_by_level, chapter_progress, error_state = _load_adaptive_flashcard_context(chapter_id)
    if isinstance(error_state, tuple):
        return error_state

    if flashcards_by_level is None or chapter_progress is None:
        return {
            "chapter_id": chapter_id,
            "chapter_title": chapter_data.get("title", f"Chapter {chapter_id}"),
            "entity_label": "Chapter",
            "current_level": "easy",
            "level_statuses": get_level_statuses(default_progress_state(), {level: [] for level in LEVELS}),
            "current_flashcard": None,
            "level_progress_text": "0/5 completed",
            "path_completed": False,
            "error_message": error_state,
            "route_base": url_for("adaptive_flashcards_page", chapter_id=chapter_id),
            "reset_endpoint": url_for("adaptive_flashcards_reset", chapter_id=chapter_id),
            "answer_endpoint": url_for("adaptive_flashcards_answer", chapter_id=chapter_id),
        }

    current_level = chapter_progress["current_level"]
    current_flashcard = choose_next_flashcard(flashcards_by_level, chapter_progress)
    completed_count = len(chapter_progress["levels"][current_level]["completed_ids"])
    total_count = len(flashcards_by_level.get(current_level, []))
    return {
        "chapter_id": chapter_id,
        "chapter_title": chapter_data.get("title", f"Chapter {chapter_id}"),
        "entity_label": "Chapter",
        "current_level": current_level,
        "level_statuses": get_level_statuses(chapter_progress, flashcards_by_level),
        "current_flashcard": current_flashcard,
        "level_progress_text": f"{completed_count}/{total_count} completed",
        "path_completed": chapter_progress.get("path_completed", False),
        "error_message": None,
        "route_base": url_for("adaptive_flashcards_page", chapter_id=chapter_id),
        "reset_endpoint": url_for("adaptive_flashcards_reset", chapter_id=chapter_id),
        "answer_endpoint": url_for("adaptive_flashcards_answer", chapter_id=chapter_id),
    }


def _build_flashcard_section_page_context(section_key: str):
    section_config, flashcards_by_level, section_progress, error_state = _load_adaptive_flashcard_section_context(section_key)
    if isinstance(error_state, tuple):
        return error_state

    if flashcards_by_level is None or section_progress is None:
        return {
            "chapter_id": section_key,
            "chapter_title": section_config["title"],
            "entity_label": "Section",
            "current_level": "easy",
            "level_statuses": get_level_statuses(default_progress_state(), {level: [] for level in LEVELS}),
            "current_flashcard": None,
            "level_progress_text": "0/0 completed",
            "path_completed": False,
            "error_message": error_state,
            "route_base": url_for("adaptive_flashcards_section_page", section_key=section_key),
            "reset_endpoint": url_for("adaptive_flashcards_section_reset", section_key=section_key),
            "answer_endpoint": url_for("adaptive_flashcards_section_answer", section_key=section_key),
        }

    current_level = section_progress["current_level"]
    current_flashcard = choose_next_flashcard(flashcards_by_level, section_progress)
    completed_count = len(section_progress["levels"][current_level]["completed_ids"])
    total_count = len(flashcards_by_level.get(current_level, []))
    return {
        "chapter_id": section_key,
        "chapter_title": section_config["title"],
        "entity_label": "Section",
        "current_level": current_level,
        "level_statuses": get_level_statuses(section_progress, flashcards_by_level),
        "current_flashcard": current_flashcard,
        "level_progress_text": f"{completed_count}/{total_count} completed",
        "path_completed": section_progress.get("path_completed", False),
        "error_message": None,
        "route_base": url_for("adaptive_flashcards_section_page", section_key=section_key),
        "reset_endpoint": url_for("adaptive_flashcards_section_reset", section_key=section_key),
        "answer_endpoint": url_for("adaptive_flashcards_section_answer", section_key=section_key),
    }


@app.route("/adaptive_flashcards/<int:chapter_id>")
def adaptive_flashcards_page(chapter_id):
    page_context = _build_flashcard_page_context(chapter_id)
    if isinstance(page_context, tuple):
        return page_context
    return render_template("adaptive_flashcards.html", **page_context)


@app.route("/adaptive_flashcards/section/<section_key>")
def adaptive_flashcards_section_page(section_key):
    page_context = _build_flashcard_section_page_context(section_key)
    if isinstance(page_context, tuple):
        return page_context
    return render_template("adaptive_flashcards.html", **page_context)


@app.route("/adaptive_flashcards/<int:chapter_id>/answer", methods=["POST"])
def adaptive_flashcards_answer(chapter_id):
    _, user_ref, error_response = _get_current_user_ref()
    if error_response:
        return error_response

    request_data = request.get_json(silent=True) or request.form
    flashcard_id = str(request_data.get("flashcard_id", "")).strip()
    selected_answer = str(request_data.get("selected_answer", "")).strip()

    if not flashcard_id or not selected_answer:
        return jsonify({"error": "flashcard_id and selected_answer are required"}), 400

    chapter_data, flashcards_by_level, chapter_progress, error_state = _load_adaptive_flashcard_context(chapter_id)
    if isinstance(error_state, tuple):
        return error_state
    if flashcards_by_level is None or chapter_progress is None:
        return jsonify({"error": error_state or "Flashcards unavailable"}), 400

    try:
        updated_progress, answer_result = answer_flashcard(
            flashcards_by_level,
            chapter_progress,
            flashcard_id,
            selected_answer,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    user_doc = user_ref.get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    user_ref.set(build_progress_update_payload(user_data, chapter_id, updated_progress), merge=True)

    current_level = updated_progress["current_level"]
    next_flashcard = choose_next_flashcard(flashcards_by_level, updated_progress)
    completed_count = len(updated_progress["levels"][current_level]["completed_ids"])

    return jsonify(
        {
            "is_correct": answer_result.is_correct,
            "selected_answer": answer_result.selected_answer,
            "correct_answer": answer_result.correct_answer,
            "hint": answer_result.hint,
            "explanation": answer_result.explanation,
            "level_completed": answer_result.level_completed,
            "path_completed": answer_result.path_completed,
            "current_level": current_level,
            "level_progress_text": f"{completed_count}/{len(flashcards_by_level.get(current_level, []))} completed",
            "level_statuses": get_level_statuses(updated_progress, flashcards_by_level),
            "next_flashcard": next_flashcard,
            "chapter_title": chapter_data.get("title", f"Chapter {chapter_id}"),
            "chapter_id": chapter_id,
        }
    )


@app.route("/adaptive_flashcards/section/<section_key>/answer", methods=["POST"])
def adaptive_flashcards_section_answer(section_key):
    _, user_ref, error_response = _get_current_user_ref()
    if error_response:
        return error_response

    request_data = request.get_json(silent=True) or request.form
    flashcard_id = str(request_data.get("flashcard_id", "")).strip()
    selected_answer = str(request_data.get("selected_answer", "")).strip()

    if not flashcard_id or not selected_answer:
        return jsonify({"error": "flashcard_id and selected_answer are required"}), 400

    section_config, flashcards_by_level, section_progress, error_state = _load_adaptive_flashcard_section_context(section_key)
    if isinstance(error_state, tuple):
        return error_state
    if flashcards_by_level is None or section_progress is None:
        return jsonify({"error": error_state or "Section flashcards unavailable"}), 400

    try:
        updated_progress, answer_result = answer_flashcard(
            flashcards_by_level,
            section_progress,
            flashcard_id,
            selected_answer,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    user_doc = user_ref.get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    adaptive_section_progress = user_data.get("adaptive_flashcard_section_progress", {})
    adaptive_section_progress[section_key] = updated_progress
    user_ref.set({"adaptive_flashcard_section_progress": adaptive_section_progress}, merge=True)

    current_level = updated_progress["current_level"]
    next_flashcard = choose_next_flashcard(flashcards_by_level, updated_progress)
    completed_count = len(updated_progress["levels"][current_level]["completed_ids"])

    return jsonify(
        {
            "is_correct": answer_result.is_correct,
            "selected_answer": answer_result.selected_answer,
            "correct_answer": answer_result.correct_answer,
            "hint": answer_result.hint,
            "explanation": answer_result.explanation,
            "level_completed": answer_result.level_completed,
            "path_completed": answer_result.path_completed,
            "current_level": current_level,
            "level_progress_text": f"{completed_count}/{len(flashcards_by_level.get(current_level, []))} completed",
            "level_statuses": get_level_statuses(updated_progress, flashcards_by_level),
            "next_flashcard": next_flashcard,
            "chapter_title": section_config["title"],
            "chapter_id": section_key,
        }
    )


@app.route("/adaptive_flashcards/<int:chapter_id>/reset", methods=["POST"])
def adaptive_flashcards_reset(chapter_id):
    _, user_ref, error_response = _get_current_user_ref()
    if error_response:
        return error_response

    user_doc = user_ref.get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    reset_progress = default_progress_state()
    user_ref.set(build_progress_update_payload(user_data, chapter_id, reset_progress), merge=True)

    return jsonify({"message": "Adaptive flashcard progress reset successfully."})


@app.route("/adaptive_flashcards/section/<section_key>/reset", methods=["POST"])
def adaptive_flashcards_section_reset(section_key):
    _, user_ref, error_response = _get_current_user_ref()
    if error_response:
        return error_response

    user_doc = user_ref.get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    adaptive_section_progress = user_data.get("adaptive_flashcard_section_progress", {})
    adaptive_section_progress[section_key] = default_progress_state()
    user_ref.set({"adaptive_flashcard_section_progress": adaptive_section_progress}, merge=True)

    return jsonify({"message": "Adaptive flashcard section progress reset successfully."})


@app.route("/chat")
def chat():
    return render_template("chat.html")


@app.route("/chat_api", methods=["POST"])
def chat_api():
    request_data = request.get_json(silent=True) or {}
    user_message = request_data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    try:
        chat_state = chat_state_service.load_state()
        logging.info(
            "Chat request received: message='%s' active_question=%s recent_agent=%s",
            user_message,
            bool(chat_state.active_question),
            chat_state.recent_agent_used,
        )

        payload, updated_state = chat_orchestrator.handle_message(user_message, chat_state)
        chat_state_service.save_state(updated_state)

        logging.info("Received message: %s", user_message)
        logging.info(
            "Agent selected=%s concepts=%s has_active_question=%s",
            payload.get("agent_used"),
            payload.get("concepts_touched"),
            payload.get("has_active_question"),
        )
        return jsonify(payload)
    except Exception:
        logging.error("Multi-agent chat error:\n%s", traceback.format_exc())
        global chain
        if chain is None:
            chain = create_chain()
        if chain is None:
            return jsonify({"error": "Assistant initialisation failed"}), 500
        try:
            response = chain.invoke(user_message)
            return jsonify(
                {
                    "message": response,
                    "response_text": response,
                    "agent_used": "explainer",
                    "has_active_question": False,
                    "question_metadata": None,
                    "concepts_touched": [],
                    "suggested_next_action": "You can ask for a quiz if you want practice.",
                    "routing_reason": "legacy_chain_fallback",
                }
            )
        except Exception:
            logging.error("Legacy fallback error:\n%s", traceback.format_exc())
            return jsonify({"error": "Internal server error"}), 500


@app.route("/chat_api/reset", methods=["POST"])
def chat_api_reset():
    state = chat_state_service.load_state()
    state = chat_state_service.clear_active_question(state)
    state = chat_state_service.set_recent_agent(state, "explainer")
    state.concept_tags = []
    state.difficulty = None
    chat_state_service.save_state(state)
    return jsonify({"message": "Chat state reset"})



@app.route('/check_automata_relevance', methods=['POST'])
def check_automata_relevance():
    """
    Check if a question-answer pair is related to automata theory.
    Returns a boolean indicating relevance.
    """
    try:
        data = request.get_json()
        question = data.get('question', '')
        answer = data.get('answer', '')
        
        if not question or not answer:
            return jsonify({"is_relevant": False}), 400
        
        prompt = f"""
You are an expert in automata theory and formal languages. Your task is to determine if a conversation is related to automata theory topics.

Automata theory topics include:
- Finite State Machines (FSM, DFA, NFA)
- Regular expressions and regular languages
- Context-free grammars and pushdown automata
- Turing machines and computability
- Formal languages and language hierarchy
- Automata operations (union, intersection, concatenation, closure)
- Language recognition and acceptance
- State transitions and formal definitions
- Pumping lemmas
- Decidability and complexity theory

Question: {question}

Answer: {answer}

Respond with ONLY "YES" if this conversation is clearly related to automata theory, or "NO" if it is not related or only tangentially related (like general greetings, unrelated topics, etc.).

Response:"""

        # Use the global model instance
        response = model.generate_content(prompt)
        result_text = response.text.strip().upper()
        
        # Determine if relevant based on response
        is_relevant = "YES" in result_text
        
        logging.info(f"Relevance check - Question: '{question[:50]}...', Relevant: {is_relevant}")
        
        return jsonify({"is_relevant": is_relevant}), 200
        
    except Exception as e:
        logging.error(f"Error checking automata relevance: {e}")
        # Default to including the message if there's an error
        return jsonify({"is_relevant": True}), 200

def _generate_regex():
    try:
        # Define the building blocks of our regex
        components = [
            'a', 'b', 'a*', 'b*', 'a+', 'b+', '(a|b)', '(a|b)*', '(a|b)+'
        ]

        num_components = random.randint(1, 2)
        
        body = "".join(random.choices(components, k=num_components))
        
        terminator = random.choice(['a', 'b'])

        if random.random() < 0.2: 
            return terminator
            
        return body + terminator

    except Exception as e:
        logging.error(f"Error generating regex programmatically: {e}")
        return "a*(b|a)b"


@app.route("/drawer")
def drawer():
    mode = request.args.get('mode', 'regex-to-fsm')
    
    if request.args.get('format') == 'json':
        if mode == 'regex-to-fsm':
            new_regex = _generate_regex()
            return jsonify({"regex": new_regex})
        else:  # fsm-to-regex mode
            new_fsm = _generate_fsm()
            return jsonify({"fsm": new_fsm})

    if mode == 'fsm-to-regex':
        initial_fsm = _generate_fsm()
        return render_template("drawer.html", mode=mode, fsm=initial_fsm)
    else:
        initial_regex = _generate_regex()
        return render_template("drawer.html", mode=mode, regex=initial_regex)

def _generate_fsm():
    """Generate a random simple FSM that can be converted to regex, with varied logic and states."""
    try:
        # Randomly map logical placeholders to actual alphabet to increase variety
        # e.g., sometimes L1 is 'a', sometimes 'b'
        l1, l2 = random.sample(['a', 'b'], 2)
        
        patterns = [
            # 1. Exact Sequence (Linear) - 4 States
            # Regex: L1 L2 L1
            {
                "states": ["q0", "q1", "q2", "q3"],
                "start": "q0",
                "final": ["q3"],
                "transitions": [
                    {"from": "q0", "to": "q1", "label": l1},
                    {"from": "q1", "to": "q2", "label": l2},
                    {"from": "q2", "to": "q3", "label": l1}
                ],
                "description": f"Accepts exactly the string '{l1}{l2}{l1}'"
            },
            # 2. One or more L1, then one or more L2 - 3 States
            # Regex: L1 L1* L2 L2* (or L1+ L2+)
            {
                "states": ["q0", "q1", "q2"],
                "start": "q0",
                "final": ["q2"],
                "transitions": [
                    {"from": "q0", "to": "q1", "label": l1},
                    {"from": "q1", "to": "q1", "label": l1},
                    {"from": "q1", "to": "q2", "label": l2},
                    {"from": "q2", "to": "q2", "label": l2}
                ],
                "description": f"Accepts one or more '{l1}'s followed by one or more '{l2}'s"
            },
            # 3. Ends with L1 L2 - 3 States
            # Regex: (a|b)* L1 L2
            {
                "states": ["q0", "q1", "q2"],
                "start": "q0",
                "final": ["q2"],
                "transitions": [
                    {"from": "q0", "to": "q0", "label": l2},
                    {"from": "q0", "to": "q1", "label": l1},
                    {"from": "q1", "to": "q1", "label": l1}, # Stay if we get L1 again
                    {"from": "q1", "to": "q2", "label": l2},
                    {"from": "q2", "to": "q0", "label": l2}, # Reset
                    {"from": "q2", "to": "q1", "label": l1}  # Overlap
                ],
                "description": f"Accepts strings ending in '{l1}{l2}'"
            },
            # 4. Exactly one L1, any number of L2s - 3 States
            # Regex: L2* L1 L2*
            {
                "states": ["q0", "q1", "q2"],
                "start": "q0",
                "final": ["q1"],
                "transitions": [
                    {"from": "q0", "to": "q0", "label": l2},
                    {"from": "q0", "to": "q1", "label": l1},
                    {"from": "q1", "to": "q1", "label": l2},
                    {"from": "q1", "to": "q2", "label": l1}, # Trap state logic
                    {"from": "q2", "to": "q2", "label": l1},
                    {"from": "q2", "to": "q2", "label": l2}
                ],
                "description": f"Accepts strings containing exactly one '{l1}'"
            },
            # 5. Even Length Strings - 2 States
            # Regex: ((a|b)(a|b))*
            {
                "states": ["q0", "q1"],
                "start": "q0",
                "final": ["q0"],
                "transitions": [
                    {"from": "q0", "to": "q1", "label": l1},
                    {"from": "q0", "to": "q1", "label": l2},
                    {"from": "q1", "to": "q0", "label": l1},
                    {"from": "q1", "to": "q0", "label": l2}
                ],
                "description": "Accepts strings of even length"
            },
            # 6. Starts with L1, Ends with L2, anything in middle - 4 States
            # Regex: L1 (a|b)* L2
            {
                "states": ["q0", "q1", "q2", "q3"],
                "start": "q0",
                "final": ["q3"],
                "transitions": [
                    {"from": "q0", "to": "q1", "label": l1},
                    # q1 is the middle state
                    {"from": "q1", "to": "q1", "label": l1},
                    {"from": "q1", "to": "q3", "label": l2},
                    # q3 is final, but if we get another symbol we might have to go back
                    {"from": "q3", "to": "q1", "label": l1},
                    {"from": "q3", "to": "q3", "label": l2} 
                ],
                "description": f"Accepts strings starting with '{l1}' and ending with '{l2}'"
            },
            # 7. Divisible by 3 (Length) - 3 States
            # Regex: ((a|b)(a|b)(a|b))*
            {
                "states": ["q0", "q1", "q2"],
                "start": "q0",
                "final": ["q0"],
                "transitions": [
                    {"from": "q0", "to": "q1", "label": l1}, {"from": "q0", "to": "q1", "label": l2},
                    {"from": "q1", "to": "q2", "label": l1}, {"from": "q1", "to": "q2", "label": l2},
                    {"from": "q2", "to": "q0", "label": l1}, {"from": "q2", "to": "q0", "label": l2}
                ],
                "description": "Accepts strings with length divisible by 3"
            },
            # 8. Contains substring 'L1 L1' - 3 States
            # Regex: (a|b)* L1 L1 (a|b)*
            {
                "states": ["q0", "q1", "q2"],
                "start": "q0",
                "final": ["q2"],
                "transitions": [
                    {"from": "q0", "to": "q1", "label": l1},
                    {"from": "q0", "to": "q0", "label": l2},
                    {"from": "q1", "to": "q2", "label": l1},
                    {"from": "q1", "to": "q0", "label": l2},
                    {"from": "q2", "to": "q2", "label": l1},
                    {"from": "q2", "to": "q2", "label": l2}
                ],
                "description": f"Accepts strings containing the substring '{l1}{l1}'"
            },
            # 9. Simple Branching (Union) - 4 States
            # Regex: L1 L1* | L2 L2*
            {
                "states": ["q0", "q1", "q2", "q_trap"],
                "start": "q0",
                "final": ["q1", "q2"],
                "transitions": [
                    {"from": "q0", "to": "q1", "label": l1},
                    {"from": "q0", "to": "q2", "label": l2},
                    {"from": "q1", "to": "q1", "label": l1},
                    {"from": "q1", "to": "q_trap", "label": l2},
                    {"from": "q2", "to": "q2", "label": l2},
                    {"from": "q2", "to": "q_trap", "label": l1},
                    {"from": "q_trap", "to": "q_trap", "label": l1},
                    {"from": "q_trap", "to": "q_trap", "label": l2}
                ],
                "description": f"Accepts either all '{l1}'s or all '{l2}'s (min length 1)"
            },
             # 10. The "Sandwich" with loops - 4 States
            # Regex: L1 (L2)* L1
            {
                "states": ["q0", "q1", "q2", "q_trap"],
                "start": "q0",
                "final": ["q2"],
                "transitions": [
                    {"from": "q0", "to": "q1", "label": l1},
                    {"from": "q0", "to": "q_trap", "label": l2},
                    {"from": "q1", "to": "q1", "label": l2},
                    {"from": "q1", "to": "q2", "label": l1},
                    {"from": "q2", "to": "q_trap", "label": l1},
                    {"from": "q2", "to": "q_trap", "label": l2},
                ],
                "description": f"Accepts '{l1}', followed by any '{l2}'s, followed by '{l1}'"
            }
        ]
        
        selected = random.choice(patterns)
        return selected
        
    except Exception as e:
        logging.error(f"Error generating FSM: {e}")
        # Default fallback
        return {
            "states": ["q0", "q1"],
            "start": "q0",
            "final": ["q1"],
            "transitions": [
                {"from": "q0", "to": "q1", "label": "a"}
            ],
            "description": "Simple FSM accepting only 'a'"
        }

@app.route('/api/check-regex', methods=['POST'])
def check_regex():
    """Check if a student's regex matches the given FSM"""
    if not request.json or 'fsm' not in request.json or 'student_regex' not in request.json:
        return jsonify({"error": "Missing FSM or regex"}), 400

    data = request.get_json()
    fsm = data['fsm']
    student_regex = data['student_regex']

    # Format the FSM for the prompt
    fsm_description = f"""
States: {{{', '.join(fsm['states'])}}}
Start State: {fsm['start']}
Final States: {{{', '.join(fsm['final'])}}}
Transitions:
"""
    for trans in fsm['transitions']:
        fsm_description += f"- from {trans['from']} to {trans['to']} on '{trans['label']}'\n"

    prompt = f"""
You are an expert in automata theory and formal languages.
Your task is to determine if a given regular expression correctly describes the language accepted by a given Finite State Machine (FSM) over the alphabet {{a, b}}.

**FSM Description:**
{fsm_description}

**Student's Regular Expression:**
`{student_regex}`

**Instructions:**
1. Analyze the FSM to determine what language it accepts.
2. Analyze the student's regular expression to determine what language it describes.
3. On the very first line, respond with a single word: "Correct" or "Incorrect".
4. On a new line, provide a brief and clear explanation for your reasoning.
   - If incorrect, provide a counterexample string that is either:
     * Accepted by the FSM but not matched by the regex, OR
     * Matched by the regex but not accepted by the FSM
   - If correct, briefly explain why the regex correctly describes the FSM's language.
5. After your explanation, on a new line starting with "Expected regex:", provide one possible correct regular expression for this FSM.
"""

    try:
        logging.info("Sending regex check request to Gemini.")
        response = model.generate_content(prompt)
        return jsonify({"result": response.text})
    except Exception as e:
        logging.error(f"Error calling Gemini API for regex check: {e}")
        return jsonify({"error": "Failed to get analysis from the AI model."}), 500

@app.route('/api/check-fsm', methods=['POST'])
def check_fsm():
    """Check if a student's drawn FSM matches the given regex (Regex -> FSM mode)."""
    if not request.is_json:
        return jsonify({"error": "Expected JSON body"}), 400

    data = request.get_json()
    regex = data.get('regex')
    fsm_description = data.get('fsm_description')

    if not regex or not fsm_description:
        return jsonify({"error": "Missing regex or fsm_description"}), 400

    prompt = f"""
You are an expert in automata theory and formal languages.
A student was given a regular expression over the alphabet {{a, b}} and drew a finite state machine (FSM).
Your task is to determine if the student's FSM accepts exactly the same language as the regular expression.

**Given Regular Expression:**
`{regex}`

**Student's FSM Description:**
{fsm_description}

**Instructions:**
1. Decide if the FSM and the regex describe the same language.
2. On the very first line respond with exactly one word: "Correct" or "Incorrect".
3. On the next line give a brief explanation.
4. If incorrect, provide a counterexample string and explain why it fails.
"""

    try:
        logging.info("Sending FSM check request to Gemini.")
        response = model.generate_content(prompt)
        return jsonify({"result": response.text})
    except Exception as e:
        logging.error(f"Error calling Gemini API for FSM check: {e}")
        return jsonify({"error": "Failed to get analysis from the AI model."}), 500


if __name__ == "__main__":
    app.run(debug=True)
