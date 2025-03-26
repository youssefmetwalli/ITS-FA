import pdfplumber
import re

pdf_path = "Automata Books/AutomataTheoryBook.pdf"
starting_page = 16

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
                        if not any(module["title"] == line for module in modules):
                            modules.append({"id": len(modules) + 1, "title": line})
    return modules


modules = extract_module_titles(pdf_path)
