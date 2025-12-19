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