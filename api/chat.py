import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flask import Flask, render_template, request, jsonify, session
import logging
from chatbot import create_chain

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "templates")
STATIC_DIR = os.path.join(PROJECT_ROOT, "static")

app = Flask(__name__, static_folder=STATIC_DIR, template_folder=TEMPLATE_DIR)
app.secret_key = os.environ.get("SECRET_KEY")
logging.basicConfig(level=logging.INFO)

chain = create_chain()

@app.route("/chat")
def chat():
    return render_template("chat.html")

@app.route("/chat_api", methods=["POST"])
def chat_api():
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    chat_history = session.get("chat_history", [])
    chat_history.append({"role": "user", "content": user_message})

    try:
        logging.info(f"Received message: {user_message}")
        chat_history_str = "\n".join(
            (f"User: {turn['content']}" if turn["role"] == "user" else f"Assistant: {turn['content']}")
            for turn in chat_history
        )
        chain_inputs = {
            "chat_history": chat_history_str,
            "question": user_message
        }
        response = chain.invoke(chain_inputs)
        logging.info(f"Assistant message: {response}")
        chat_history.append({"role": "assistant", "content": response})
        session["chat_history"] = chat_history
        return jsonify({"message": response})
    except Exception as e:
        logging.error(f"Error in chat_api: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
