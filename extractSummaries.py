import os
import google.generativeai as genai
import pdfplumber
import firebase_admin
from firebase_admin import credentials, firestore
import psutil
import time
import random
import io
import base64
from PIL import Image
import markdown
import unicodedata
import html
import re

# Gemini API Key
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash-001')

# Firebase Setup
cred = credentials.Certificate("firebase.json")
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

def print_memory_usage():
    process = psutil.Process()
    print(f"Memory Usage: {process.memory_info().rss / (1024 * 1024):.2f} MB")

def normalize_text(text):
    """Normalize special characters in the text."""
    return unicodedata.normalize('NFKC', text)

def convert_markdown_to_html(markdown_text):
    """Converts markdown text to HTML without additional escaping."""
    return markdown.markdown(markdown_text)

def summarize_text(text, max_retries=5, base_delay=1, max_delay=16):
    """Summarizes the given text using the Gemini API with retry logic."""
    try:
        retries = 0
        while retries <= max_retries:
            try:
                response = model.generate_content(
                    f"""Explain and highlight the concepts and examples in detail.
                    Do not use any language that implies you are summarizing or referring to any text, document, or discussion.
                    Present the information directly as if presenting it for the first time.
                     Do not number the examples, but present them as information.
                    Text:
                    {text}""",
                    generation_config=genai.types.GenerationConfig(max_output_tokens=1500)
                )

                if response.text:
                    return response.text.strip()
                else:
                    print("Warning: Gemini API returned an empty or None response.")
                    return "Summary not available"

            except Exception as e:
                if "429" in str(e):
                    retries += 1
                    delay = min(base_delay * (2 ** retries) + random.uniform(0, 1), max_delay)
                    print(f"Rate limited. Retrying in {delay:.2f} seconds (Retry {retries}/{max_retries}).")
                    time.sleep(delay)
                else:
                    print(f"Error summarizing text: {e}")
                    return "Summary not available"
        else:
            print(f"Max retries exceeded for the text.")
            return "Summary not available"
    except Exception as e:
        print(f"Error summarizing text: {e}")
        return "Summary not available"

def extract_text_and_figures(pdf_path, start_page, end_page):
    """Extracts text and images from the PDF."""
    full_text = ""
    figures = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num in range(start_page, end_page + 1):
            page = pdf.pages[page_num]
            text = page.extract_text() or ""
            full_text += normalize_text(text) + "\n"

            # Process images on the page
            for image in page.images:
                try:
                    image_data = image['stream'].get_data()
                    if not image_data or len(image_data) == 0:
                        print(f"Image on page {page_num + 1} is empty or corrupted.")
                        continue

                    image_base64 = base64.b64encode(image_data).decode('utf-8')
                    figures.append({
                        "page": page_num + 1,
                        "data": f"data:image/png;base64,{image_base64}"
                    })
                except Exception as e:
                    print(f"Error processing image on page {page_num + 1}: {e}")

            del page  # Release memory used by the page

    # Summarize and convert to HTML
    summary = summarize_text(full_text)
    summary_html = convert_markdown_to_html(summary)
    return {"text_summary": summary, "html_summary": summary_html, "figures": figures}

def save_summaries_to_firestore(chapter_id, subchapter_id, summaries):
    """Saves summaries to Firestore, merged into a single document."""
    subchapter_ref = db.collection('subchapters').document(str(subchapter_id))
    data = {
        "chapter_id": chapter_id,
        "summary": {
            "text": summaries["text_summary"],
            "html": summaries["html_summary"],
            "figures": summaries["figures"],
        },
    }
    try:
        subchapter_ref.set(data, merge=True)
        print(f"Successfully saved subchapter {subchapter_id} summaries to Firestore.")
    except Exception as e:
        print(f"Error saving to Firestore: {e}")

def process_chapters(pdf_path, chapters_to_process, batch_size=5):
    """Processes chapters, extracting and summarizing subchapters in batches."""
    for batch_start in range(0, len(chapters_to_process), batch_size):
        batch = chapters_to_process[batch_start:batch_start + batch_size]
        print(f"Processing batch {batch_start} to {batch_start + len(batch)}...")
        for chapter in batch:
            chapter_id = chapter['id']
            print(f"Processing chapter ID: {chapter_id}...")
            subchapters_ref = db.collection('subchapters').where('chapter_id', '==', chapter_id)
            subchapter_docs = subchapters_ref.stream()
            time.sleep(1)  # Delay before processing each chapter

            for subchapter_doc in subchapter_docs:
                subchapter = subchapter_doc.to_dict()
                subchapter_id = subchapter_doc.id
                start_page = subchapter.get('start_page', 0)
                end_page = subchapter.get('end_page', 0)

                print(f"Processing subchapter ID: {subchapter_id} (Pages {start_page}-{end_page})...")
                if start_page and end_page:
                    try:
                        summary = extract_text_and_figures(pdf_path, start_page, end_page)
                        save_summaries_to_firestore(chapter_id, subchapter_id, summary)
                    except Exception as e:
                        print(f"Error processing subchapter {subchapter_id}: {e}")
                else:
                    print(f"Missing page range for subchapter {subchapter_id}, skipping.")
        print_memory_usage()


pdf_path = "Automata Books/AutomataTheoryBook.pdf"

# Fetch first 1 chapter from Firestore
chapters_ref = db.collection('chapters').where('id', '>=', 43).where('id', '<=', 43).order_by('id')
chapters = [doc.to_dict() for doc in chapters_ref.stream()]

print(f"Fetched {len(chapters)} chapters for processing...")
# Process and summarize subchapters for the first 1 chapters
process_chapters(pdf_path, chapters)