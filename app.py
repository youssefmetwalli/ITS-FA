from flask import Flask, render_template, redirect, url_for, abort, request, session, jsonify
# from manim import *
from dotenv import load_dotenv
import os
import json
import logging
import re
import firebase_admin
from firebase_admin import credentials, firestore, auth, initialize_app
from chatbot import create_chain
import random  # Import random for shuffling
import traceback


# Load environment variables
load_dotenv()

# Firebase setup
credentials_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
if credentials_json:
    creds_dict = json.loads(credentials_json)
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    cred = credentials.Certificate(creds_dict)
    initialize_app(cred)
else:
    raise Exception("Firebase credentials not found in environment variables")
db = firestore.client()

# Flask app setup
app = Flask(__name__)
app.secret_key = os.urandom(24)  # Used for cookies

# Directories for static content
STATIC_DIR = "static"
VIDEOS_DIR = os.path.join(STATIC_DIR, "videos")
CHATS_DIR = "chats"

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Related videos for each lesson
related_videos = {
    1: "https://www.youtube.com/embed/L2leZPkJ65I",
    2: "https://www.youtube.com/embed/5A8C5-Qj7JM",
    3: "https://www.youtube.com/embed/your_video_url_here",
    4: "https://www.youtube.com/embed/your_video_url_here",
}

# Initialize RAG chain
try:
    chain = create_chain()
    if chain is None:
        raise Exception("Failed to initialize the RAG chain")
except Exception as e:
    logging.error(f"Chain initialization error: {e}")
    chain = None  # Prevent crashes

# Custom Jinja2 filter to shuffle a list
def shuffle_list(seq):
    shuffled = list(seq)
    random.shuffle(shuffled)
    return shuffled

app.jinja_env.filters['shuffle'] = shuffle_list

# Routes
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        try:
            user = auth.get_user_by_email(email)
            return render_template("signup.html", error="This email is already in use")
        except auth.UserNotFoundError:
            pass
        password_pattern = r"^(?=.*[A-Z])(?=.*\d)(?=.*[\W_]).{8,}$"
        if not re.match(password_pattern, password):
            return render_template("signup.html", error="Password must be at least 8 characters long, contain at least one number, one uppercase letter, and one symbol. ")

        try:
            # Create user with Firebase Authentication
            user = auth.create_user(email=email, password=password)
            session["user_id"] = user.uid  # Store user session

            # Store user in Firestore
            user_data = {
                "email": email,
                "uid": user.uid,
                "created_at": firestore.SERVER_TIMESTAMP,  # Adds timestamp
                "chapters_read": 0,
                "read_chapters": [],
                "quizzes_attempted": 0,
                "quizzes_completed": 0
            }
            db.collection("Users").document(user.uid).set(user_data)

            return redirect(url_for("index"))  # Redirect to index on successful signup

        except Exception as e:
            logging.error(f"Error signing up: {e}, traceback: {traceback.format_exc()}")
            return render_template("signup.html", error="Unable to signup, please make sure you have a valid email and password.")

    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    firebase_config = {
        'firebase_api_key': os.getenv('FIREBASE_API_KEY'),
        'firebase_auth_domain': os.getenv('FIREBASE_AUTH_DOMAIN'),
        'firebase_project_id': os.getenv('FIREBASE_PROJECT_ID'),
        'firebase_storage_bucket': os.getenv('FIREBASE_STORAGE_BUCKET'),
        'firebase_app_id': os.getenv('FIREBASE_APP_ID'),
    }
    return render_template("login.html", **firebase_config)


@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user_id', None) 
    return redirect(url_for('index'))

@app.route("/validate_token", methods=["POST"])
def validate_token():
    # Get the ID token from the request
    data = request.get_json()
    id_token = data.get('idToken')

    if not id_token:
        return jsonify({"error": "No token provided"}), 400

    try:
        # Verify the ID token
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']

        # Store user session after verifying token
        session["user_id"] = uid
        return jsonify({"success": True})  # User authenticated successfully

    except Exception as e:
        logging.error(f"Error verifying token: {e}")
        return jsonify({"error": "Invalid token or session expired"}), 401

@app.before_request
def before_request():
  if not session.get('user_id') and request.endpoint not in ['login', 'static', 'index','signup', 'validate_token']:
        return redirect(url_for('login'))

@app.route('/save_answer', methods=['POST'])
def save_answer():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session['user_id']
    try:
        data = request.get_json()  # Changed to get_json()
        print(f"Received data: {data}")  # Added printing data
        question_id = data.get("questionId")
        answer = data.get("answer")

        if not question_id or answer is None:
            return jsonify({"error": "Invalid data: questionId and answer are required"}), 400

    except Exception as e:
        print(f"Error parsing JSON: {e}")
        return jsonify({"error": f"Invalid JSON: {str(e)}"}), 400  # More specific error message

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
    """
    Increments the user's 'chapters_read' count by 1
    and adds 'chapter_id' to 'read_chapters' list if not already present.
    """
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session['user_id']
    user_ref = db.collection("Users").document(user_id)

    try:
        user_doc = user_ref.get()
        if user_doc.exists:
            user_data = user_doc.to_dict()

            # Increment the overall chapters_read
            current_count = user_data.get('chapters_read', 0)
            user_ref.update({'chapters_read': current_count + 1})

            # Ensure read_chapters is a list
            read_chapters = user_data.get('read_chapters', [])
            if chapter_id not in read_chapters:
                read_chapters.append(chapter_id)
                user_ref.update({"read_chapters": read_chapters})

        else:
            # If user doc doesn't exist for some reason, create it
            # Initialize both fields
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
    # 1. Check for logged-in user
    if 'user_id' not in session:
        return redirect(url_for('login'))  # or however you handle unauthorized access

    user_id = session['user_id']

    # 2. Fetch user data from Firestore
    user_doc = db.collection("Users").document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    user_answers = user_data.get("answers", {})
    read_chapters = user_data.get("read_chapters", [])

    # 3. Fetch all chapters
    chapters_ref = db.collection("chapters")
    chapters = []

    docs = chapters_ref.stream()
    for doc in docs:
        chapter = doc.to_dict()
        # Convert document ID to integer for easier sorting
        chapter["id"] = int(doc.id)
        chapters.append(chapter)

    # Sort chapters numerically by ID
    chapters.sort(key=lambda x: x["id"])

    # 4. Pass both the chapters and the user's answers into the template
    return render_template(
        "course.html",
        chapters=chapters,
        user_answers=user_answers,
        read_chapters=read_chapters
    )



@app.route("/module/<int:module_id>")
def module_detail(module_id):
    # Fetch the selected chapter
    chapter_ref = db.collection("chapters").document(str(module_id))
    chapter = chapter_ref.get()

    if not chapter.exists:
        abort(404)  # If chapter doesn't exist, return 404

    chapter_data = chapter.to_dict()
    chapter_data["id"] = module_id

    # Fetch subchapters for the selected chapter
    subchapters_ref = db.collection("subchapters").where("chapter_id", "==", module_id)
    subchapters = []

    subchapter_docs = subchapters_ref.stream()
    for doc in subchapter_docs:
        subchapter = doc.to_dict()
        subchapter["id"] = doc.id
        subchapters.append(subchapter)

    # Render the module details page
    return render_template(
        "module_detail.html", module=chapter_data, subchapters=subchapters
    )


    
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

    return render_template(
        'quiz.html',
        chapter_id=chapter_id,
        questions=questions,
        correct_answers=correct_answers,
        incorrect_answers=incorrect_answers,
        hints=hints,
        zip=zip  # Pass the zip function to the template
    )


@app.route('/quiz_result', methods=['POST'])
def quiz_result():
    # 1. Check for logged-in user
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session['user_id']
    user_ref = db.collection("Users").document(user_id)

    # 2. Parse JSON from the request
    data = request.get_json()
    score = data.get('score', 0)
    total = data.get('total', 0)

    try:
        user_doc = user_ref.get()
        if user_doc.exists:
            user_data = user_doc.to_dict()

            # 3. Increment quizzes_attempted
            current_attempted = user_data.get('quizzes_attempted', 0)
            user_ref.update({'quizzes_attempted': current_attempted + 1})

            # 4. If perfect score, increment quizzes_completed
            if score == total:
                current_completed = user_data.get('quizzes_completed', 0)
                user_ref.update({'quizzes_completed': current_completed + 1})

            return jsonify({"message": "Quiz result recorded"}), 200
        else:
            # If user doc doesn't exist, create it with default fields
            # e.g. if user somehow wasn't created in signup
            new_data = {
                'quizzes_attempted': 1, 
                'quizzes_completed': 1 if score == total else 0
            }
            user_ref.set(new_data, merge=True)
            return jsonify({"message": "User doc created and quiz result recorded"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/chat")
def chat():
    return render_template("chat.html")


@app.route("/chat_api", methods=["POST"])
def chat_api():
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    # Pull existing history from session, or use empty list if none
    chat_history = session.get("chat_history", [])

    # Append this new user turn to chat_history
    chat_history.append({"role": "user", "content": user_message})

    try:
        logging.info(f"Received message: {user_message}")

        # Convert the entire conversation to a single string for the chain
        chat_history_str = ""
        for turn in chat_history:
            role_label = "User" if turn["role"] == "user" else "Assistant"
            chat_history_str += f"{role_label}: {turn['content']}\n"

        # Build the chain input:
        #   "chat_history" is the string containing prior turns
        #   "latest_question" is the newest user message
        chain_inputs = {
            "chat_history": chat_history_str,
            "question": user_message
        }

        # Invoke the chain
        response = chain.invoke(chain_inputs)
        logging.info(f"Assistant message: {response}")

        # Add assistant's response to the in-memory chat history
        chat_history.append({"role": "assistant", "content": response})

        # Save updated chat history in session
        session["chat_history"] = chat_history

        # Return the assistant's text
        return jsonify({"message": response})
    except Exception as e:
        logging.error(f"Error: {e}")
        return jsonify({"error": str(e)}), 500




@app.route("/track_progress")
def track_progress():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user_doc = db.collection("Users").document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    # Default to zero if not found
    chapters_read = user_data.get("chapters_read", 0)
    quizzes_attempted = user_data.get("quizzes_attempted", 0)
    quizzes_completed = user_data.get("quizzes_completed", 0)

    return render_template(
        "trackprogress.html",
        chapters_read=chapters_read,
        quizzes_attempted=quizzes_attempted,
        quizzes_completed=quizzes_completed
    )



if __name__ == "__main__":
    app.run(debug=True)