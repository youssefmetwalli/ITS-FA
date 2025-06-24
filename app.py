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
import random 
import traceback
import google.generativeai as genai

load_dotenv()
b64 = os.environ.get("GOOGLE_CREDS_B64")
if not b64:
    raise RuntimeError("Missing GOOGLE_CREDS_B64")

creds_dict = json.loads(base64.b64decode(b64))
initialize_app(credentials.Certificate(creds_dict))
db = firestore.client()
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash-002")
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

def shuffle_list(seq):
    shuffled = list(seq)
    random.shuffle(shuffled)
    return shuffled

app.jinja_env.filters['shuffle'] = shuffle_list

# Routes
@app.route("/")
def index():
    if not session.get('user_id'):
         return redirect(url_for("signup"))
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


@app.route("/signup", methods=["GET", "POST"])
def signup():
    firebase_config = {
        "firebase_api_key": os.getenv("FIREBASE_API_KEY"),
        "firebase_auth_domain": os.getenv("FIREBASE_AUTH_DOMAIN"),
        "firebase_project_id": os.getenv("FIREBASE_PROJECT_ID"),
        "firebase_storage_bucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
        "firebase_app_id": os.getenv("FIREBASE_APP_ID")
    }
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        try:
            user = auth.get_user_by_email(email)
            return render_template("signup.html", error="This email is already in use", **firebase_config)
        except auth.UserNotFoundError:
            pass
        password_pattern = r"^(?=.*[A-Z])(?=.*\d)(?=.*[\W_]).{8,}$"
        if not re.match(password_pattern, password):
            return render_template("signup.html", error="Password must be at least 8 characters long, contain at least one number, one uppercase letter, and one symbol. ", **firebase_config)

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
            return render_template("signup.html", error="Unable to signup, please make sure you have a valid email and password.", **firebase_config)

    return render_template("signup.html", **firebase_config)

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
    chapters_ref = db.collection("chapters")
    chapters = []

    docs = chapters_ref.stream()
    for doc in docs:
        chapter = doc.to_dict()
        # Convert document ID to integer for easier sorting
        chapter["id"] = int(doc.id)
        chapters.append(chapter)
    chapters.sort(key=lambda x: x["id"])

    return render_template(
        "course.html",
        chapters=chapters,
        user_answers=user_answers,
        read_chapters=read_chapters
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
        zip=zip 
    )


@app.route('/quiz_result', methods=['POST'])
def quiz_result():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session['user_id']
    user_ref = db.collection("Users").document(user_id)
    data = request.get_json()
    score = data.get('score', 0)
    total = data.get('total', 0)

    try:
        user_doc = user_ref.get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            current_attempted = user_data.get('quizzes_attempted', 0)
            user_ref.update({'quizzes_attempted': current_attempted + 1})

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

    global chain
    if chain is None:
        chain = create_chain()
        if chain is None:
            return jsonify({"error": "Assistant initialisation failed"}), 500

    try:
        logging.info("Received message: %s", user_message)
        response = chain.invoke(user_message)       
        logging.info("Assistant message: %s", response)

        return jsonify({"message": response})
    except Exception:
        logging.error("Error:\n%s", traceback.format_exc())
        return jsonify({"error": "Internal server error"}), 500


def _generate_regex():
    try:
        prompt = (
            "Generate a single random regular expression over the alphabet {a, b}. "
            "Use +,-,* but make sure the regular expression ends in a or b"
            "Do not include any explanation—just output the regex itself. "
            "Make it relatively simple."
        )
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.9,
                max_output_tokens=30
            )
        )
        regex = response.text.strip()
        if not regex:
            regex = "(a|b)*"
        return regex
    except Exception as e:
        logging.error(f"Gemini API call failed: {e}")
        return "a*(b|a)b*"

@app.route("/drawer")
def drawer():

    if request.args.get('format') == 'json':
        new_regex = _generate_regex()
        return jsonify({"regex": new_regex})

    initial_regex = _generate_regex()
    return render_template("drawer.html", regex=initial_regex)

@app.route('/api/check-fsm', methods=['POST'])
def check_fsm():
    if not request.json or 'regex' not in request.json or 'fsm_description' not in request.json:
        return jsonify({"error": "Missing regex or FSM description"}), 400

    data = request.get_json()
    regex = data['regex']
    fsm_description = data['fsm_description']

    prompt = f"""
You are an expert in automata theory and formal languages.
Your task is to determine if a given Finite State Machine (FSM) correctly accepts the language described by a given regular expression over the alphabet {{a, b}}.

**Regular Expression:**
`{regex}`

**FSM Description:**
{fsm_description}

**Instructions:**
1. Analyze the FSM and the regular expression.
2. On the very first line, respond with a single word: "Correct" or "Incorrect".
3. On a new line, provide a brief and clear explanation for your reasoning.
   - If incorrect, specify a simple string that is either wrongly accepted or wrongly rejected by the FSM. For example: "The FSM incorrectly accepts the string 'b'" or "The FSM fails to accept the valid string 'aba'".
   - If correct, briefly explain why it correctly models the regex.
"""

    try:
        logging.info("Sending FSM check request to Gemini.")
        response = model.generate_content(prompt)
        # send the raw text back, the frontend will format it.
        return jsonify({"result": response.text})
    except Exception as e:
        logging.error(f"Error calling Gemini API for FSM check: {e}")
        return jsonify({"error": "Failed to get analysis from the AI model."}), 500


if __name__ == "__main__":
    app.run(debug=True)