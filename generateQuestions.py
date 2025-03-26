import os
import google.generativeai as genai
import time
import random
import firebase_admin
import logging
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
import json
import re

# Load environment variables
load_dotenv()

# Gemini API setup
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash-001')

# Firebase setup
cred = credentials.Certificate("firebase.json")
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def parse_response_to_arrays(response_text):
    """Parses the JSON response text into arrays of questions, correct answers, incorrect answers, and hints."""
    questions, correct_answers, incorrect_answers, hints = [], [], [], []
    try:
        #remove markdown
        response_text = re.sub(r'```json\s*', '', response_text, flags=re.IGNORECASE)
        response_text = re.sub(r'\s*```', '', response_text, flags=re.IGNORECASE)
        
        data = json.loads(response_text)
        for item in data:
            questions.append(item.get("question", ""))
            correct_answers.append(item.get("correct_answer", ""))
            incorrect_answers.append(item.get("incorrect_answers", []))
            hints.append(item.get("hint", ""))

        return questions, correct_answers, incorrect_answers, hints
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON response: {e}")
        return [], [], [], []


def generate_multiple_choice_questions(text, num_questions=10, max_retries=5, base_delay=1, max_delay=16):
    """Generates multiple choice questions with correct answers, incorrect answers, and hints using the Gemini API."""
    retries = 0
    while retries <= max_retries:
        try:
            logging.info(f"Generating Multiple Choice Q&A, Text Preview: {text[:100]}...")
            response = model.generate_content(
                f"""
                Based on the information below, create {num_questions} distinct multiple-choice questions with their respective correct answers, incorrect answers, and hints.
                 Return the response in a JSON array format, where each object contains the question, correct answer, an array of exactly 3 incorrect answers, and a hint. 
                 The keys for the objects should be 'question', 'correct_answer', 'incorrect_answers', and 'hint'.
                 Ensure each question has exactly 3 incorrect answers.
                 Don't use \1 ever.
                Text: {text}
                """,
                generation_config=genai.types.GenerationConfig(max_output_tokens=2000)
            )
            logging.info(f"Gemini Response:\n{response.text}")
            if response.text:
                logging.info("Multiple choice Q&A generation successful.")
                return parse_response_to_arrays(response.text.strip())
            else:
                logging.warning("Gemini API returned an empty response.")
                return [], [], [], []
        except Exception as e:
            if "429" in str(e):
                retries += 1
                delay = min(base_delay * (2 ** retries) + random.uniform(0, 1), max_delay)
                logging.info(f"Rate limited. Retrying in {delay:.2f} seconds (Retry {retries}/{max_retries}).")
                time.sleep(delay)
            else:
                logging.error(f"Error generating Multiple Choice Q&A: {e}")
                return [], [], [], []
    logging.warning("Max retries exceeded for Multiple Choice Q&A generation.")
    return [], [], [], []



def save_questions_and_answers_to_firestore(chapter_id, questions, correct_answers, incorrect_answers, hints):
    """Saves the questions, correct answers, incorrect answers, and hints as separate arrays in Firestore."""
    chapter_ref = db.collection('chapters').document(str(chapter_id))
    try:
        # Debug: Checking data types before saving
        logging.info(f"Data types: Questions: {type(questions)}, Correct Answers: {type(correct_answers)}, Incorrect Answers: {type(incorrect_answers)}, Hints: {type(hints)}")
        logging.info(f"Questions: {questions}")
        logging.info(f"Correct Answers: {correct_answers}")
        logging.info(f"Incorrect Answers: {incorrect_answers}")
        logging.info(f"Hints: {hints}")

        # Check if the lists are actually lists and not empty
        if not isinstance(questions, list) or not questions:
            logging.error(f"Error saving Multiple Choice Q&A for chapter {chapter_id}: 'questions' is not a non-empty list.")
            return
        if not isinstance(correct_answers, list) or not correct_answers:
             logging.error(f"Error saving Multiple Choice Q&A for chapter {chapter_id}: 'correct_answers' is not a non-empty list.")
             return
        if not isinstance(incorrect_answers, list) or not incorrect_answers:
            logging.error(f"Error saving Multiple Choice Q&A for chapter {chapter_id}: 'incorrect_answers' is not a non-empty list.")
            return
        if not isinstance(hints, list) or not hints:
           logging.error(f"Error saving Multiple Choice Q&A for chapter {chapter_id}: 'hints' is not a non-empty list.")
           return

        # Flatten incorrect_answers
        flattened_incorrect_answers = []
        for incorrect_list in incorrect_answers:
            flattened_incorrect_answers.extend(incorrect_list)

        logging.info(f"Flattened Incorrect Answers: {flattened_incorrect_answers}")

        chapter_ref.set({
            "questions": questions,
            "correct_answers": correct_answers,
            "incorrect_answers": flattened_incorrect_answers,
            "hints": hints
        }, merge=True)
        logging.info(f"Successfully saved Multiple Choice Q&A for chapter {chapter_id}.")
    except Exception as e:
        logging.error(f"Error saving Multiple Choice Q&A for chapter {chapter_id}: {e}")


def process_chapters_for_qa(batch_size=5, start_chapter=46, end_chapter=46):
    """Processes chapters, generating and saving Q&A sets in batches."""
    chapters_ref = db.collection('chapters').order_by('id')
    chapters = [doc.to_dict() for doc in chapters_ref.stream() if start_chapter <= int(doc.id) <= end_chapter]

    for batch_start in range(0, len(chapters), batch_size):
        batch = chapters[batch_start:batch_start + batch_size]
        logging.info(f"Processing batch {batch_start + 1} to {batch_start + len(batch)}...")
        
        for chapter in batch:
            chapter_id = chapter['id']
            title = chapter.get('title', '')
            
            if "Summary and References" in title:
                logging.info(f"Skipping chapter {chapter_id}: Title contains 'Summary and References'.")
                continue

            logging.info(f"Processing chapter ID: {chapter_id}...")
            subchapters_ref = db.collection('subchapters').where('chapter_id', '==', chapter_id)
            subchapter_docs = subchapters_ref.stream()
            full_chapter_text = ""

            for subchapter_doc in subchapter_docs:
                subchapter = subchapter_doc.to_dict()
                summary_text = subchapter.get("summary", {}).get("text", "")
                if summary_text:
                    full_chapter_text += summary_text + "\n"
                else:
                    logging.info(f"Skipping subchapter {subchapter_doc.id}: No summary available.")

            if full_chapter_text.strip():
                questions, correct_answers, incorrect_answers, hints = generate_multiple_choice_questions(full_chapter_text)
                logging.info(f"Parsed Questions: {questions}")
                logging.info(f"Parsed Correct Answers: {correct_answers}")
                logging.info(f"Parsed Incorrect Answers: {incorrect_answers}")
                logging.info(f"Parsed Hints: {hints}")
                logging.info(f"Saving to chapter ID {chapter_id} q:{questions}, a:{correct_answers}, i_a:{incorrect_answers} and h:{hints}")
                save_questions_and_answers_to_firestore(chapter_id, questions, correct_answers, incorrect_answers, hints)
            else:
                logging.info(f"Skipping chapter {chapter_id}: No summaries found in subchapters.")


if __name__ == "__main__":
    process_chapters_for_qa()