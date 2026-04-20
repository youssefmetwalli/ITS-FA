import pdfplumber
import re
from extractChapters import modules
import firebase_admin
from firebase_admin import credentials, firestore

pdf_path = "Automata Books/AutomataTheoryBook.pdf"
starting_page = 16
subchapter_regex = re.compile(r"^\d+\.\d+\s+[A-Z].*")
max_title_length = 90

# Firebase setup
firebase_key_path = "firebase.json"
cred = credentials.Certificate(firebase_key_path)
if not firebase_admin._apps:  # Prevent re-initialization
    firebase_admin.initialize_app(cred)
db = firestore.client()

ignore_start_phrases = [
    "Delete all",
    "Add to",
    "Remove",
    "Mark off",
    "Scan",
    "Move",
    "If the",
    "Go left",
    "Write",
    "Copy",
    "He",
]
ignore_start_phrases_regex = re.compile(
    r"^(Delete all|Add to|Remove|Mark off|Scan|Move|If the|Go left|Write|Copy|He)",
    re.IGNORECASE,
)

# Convert modules list to a dictionary for faster lookups by chapter_number
modules_dict = {mod["chapter_number"]: mod for mod in modules}

def extract_subchapters_with_content(pdf_path):
    subchapters = []
    current_subchapter = None
    current_content = []
    current_chapter_id = None
    current_start_page = None

    with pdfplumber.open(pdf_path) as pdf:
        for page_num in range(starting_page, len(pdf.pages)):
            page = pdf.pages[page_num]
            text = page.extract_text()
            if text:
                for line in text.split("\n"):
                    line = line.strip()

                    # Check if the line matches the subchapter title regex
                    match = subchapter_regex.match(line)
                    if match:
                        # Save the current subchapter and its content if exists
                        if current_subchapter:
                            subchapters.append(
                                {
                                    "id": len(subchapters) + 1,
                                    "title": current_subchapter,
                                    "chapter_id": current_chapter_id,
                                    "start_page": current_start_page,
                                    "end_page": page_num,  # Last page of the current subchapter
                                    "text": "\n".join(current_content).strip(),
                                }
                            )
                            print(f"Processed subchapter: {current_subchapter}")

                        # Start a new subchapter
                        title_without_number = re.sub(r"^\d+\.\d+\s+", "", line)

                        # Skip subchapters with titles that match ignore phrases or contain 'Exercises'
                        if (
                            ignore_start_phrases_regex.match(title_without_number)
                            or "Exercises" in title_without_number
                        ):
                            print(f"Skipping subchapter: {title_without_number}")
                            continue

                        # Extract chapter number from subchapter title and find the corresponding chapter
                        chapter_number = int(line.split(".")[0])
                        chapter = modules_dict.get(chapter_number)

                        if chapter:
                            current_chapter_id = chapter["id"]
                            current_subchapter = line
                            current_content = []  # Reset content for the new subchapter
                            current_start_page = page_num  # Set the start page of the new subchapter
                        else:
                            print(f"Warning: No chapter found for subchapter {line}")

                    elif current_subchapter:
                        # Add the line to the current subchapter content
                        current_content.append(line)

        # Append the last subchapter if it exists
        if current_subchapter:
            subchapters.append(
                {
                    "id": len(subchapters) + 1,
                    "title": current_subchapter,
                    "chapter_id": current_chapter_id,
                    "start_page": current_start_page,
                    "end_page": len(pdf.pages) - 1,  # Last page of the document
                    "text": "\n".join(current_content).strip(),
                }
            )
            print(f"Processed last subchapter: {current_subchapter}")

    return subchapters

def save_subchapters_to_firestore(subchapters):
    subchapters_ref = db.collection("subchapters")

    for subchapter in subchapters:
        # Extract subchapter number from the title
        subchapter_number = re.match(r"^\d+\.\d+", subchapter["title"])
        if not subchapter_number:
            continue  # Skip invalid subchapter titles

        doc_id = subchapter_number.group(0)  # Use subchapter number as document ID

        subchapter_data = {
            "title": subchapter["title"],
            "chapter_id": subchapter["chapter_id"],
            "start_page": subchapter["start_page"],
            "end_page": subchapter["end_page"],
        }

        subchapters_ref.document(doc_id).set(subchapter_data)

subchapters = extract_subchapters_with_content(pdf_path)
save_subchapters_to_firestore(subchapters)
print("Subchapters extracted and saved successfully.")
