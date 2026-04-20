import pdfplumber
import re
import firebase_admin
from firebase_admin import credentials, firestore

# Path to the PDF file
pdf_path = "Automata Books/AutomataTheoryBook.pdf"
starting_page = 16

# Firebase setup
firebase_key_path = "firebase.json"
cred = credentials.Certificate(firebase_key_path)
if not firebase_admin._apps:  # Prevent re-initialization
    firebase_admin.initialize_app(cred)
db = firestore.client()  # Firestore client

# Regular expressions and configurations
chapter_regex = re.compile(r"^\d+\s+[A-Z][A-Za-z]+(?:\s+[A-Za-z]+)+")
max_title_length = 60
specific_chapters = {
    "4 Computation",
    "11 Context-Free Grammars",
    "13 Context-Free and Noncontext-Free Languages",
    "15 Context-Free Parsing",
    "32 Logic, Sets, Relations, Functions, and Proof Techniques",
    "40 Networks",
    "41 Security",
}


# Function to extract module titles
def extract_module_titles(pdf_path):
    modules = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num in range(starting_page, len(pdf.pages)):
            page = pdf.pages[page_num]
            text = page.extract_text()
            if text:
                for line in text.split("\n"):
                    line = line.strip()
                    if (
                        (chapter_regex.match(line) or line in specific_chapters)
                        and len(line) <= max_title_length
                        and "[" not in line
                        and "]" not in line
                        and "ProperNoun" not in line
                    ):
                        # Extract chapter number
                        chapter_number = int(line.split()[0])
                        # Avoid duplicate titles
                        if not any(module["title"] == line for module in modules):
                            modules.append(
                                {
                                    "id": len(modules) + 1,  # Assign unique ID to each module
                                    "chapter_number": chapter_number,
                                    "title": line,
                                }
                            )
    return modules


# Function to save modules to Firestore
def save_to_firestore(modules):
    chapters_ref = db.collection("chapters")  # Firestore collection 'chapters'
    for module in modules:
        doc_id = str(module["chapter_number"])  # Document ID as chapter number
        chapter_data = {
            "id": module["id"],  # Add unique ID to the document
            "title": module["title"],
        }
        # Add or update the document in Firestore
        chapters_ref.document(doc_id).set(chapter_data)
        print(f"Saved Chapter {module['chapter_number']}: {module['title']}")


# Extract modules from the PDF
modules = extract_module_titles(pdf_path)

# Save extracted modules to Firestore
save_to_firestore(modules)

print("All chapters have been saved to Firestore successfully.")
