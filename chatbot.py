import logging
import os
import google.generativeai as genai
import PyPDF2
from PyPDF2.errors import PdfReadError
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, HumanMessagePromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Constants 
PDF_FILE_PATH = "Automata Books/AutomataTheoryBook.pdf"
VECTOR_DB_PATH = "automata_vector_db"
VECTOR_STORE_FILE = "index.faiss"
PERSONA = "You are an AI assistant specialized in Automata Theory. Be kind, conversational, and helpful"

# Configure API key from environment variable
load_dotenv
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash-001')


def load_and_chunk_pdf(pdf_path):
    """Loads a PDF, extracts text, and chunks it."""
    text = ""
    try:
        with open(pdf_path, "rb") as pdf_file:
            reader = PyPDF2.PdfReader(pdf_file)
            for page in reader.pages:
                text += page.extract_text() or ""

    except FileNotFoundError:
        logging.error(f"PDF not found at: {pdf_path}")
        return None
    except PdfReadError as e:
        logging.error(f"Error reading PDF: {e}")
        return None


    if not text:
        logging.warning("No text was extracted from the PDF.")
        return None

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    chunks = text_splitter.split_text(text)
    logging.info(f"Extracted and split the PDF into {len(chunks)} chunks.")
    return chunks


def create_embedding_index(chunks):
    """Creates a FAISS embedding index from the text chunks."""
    embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
    vector_store = FAISS.from_texts(texts=chunks, embedding=embeddings)
    logging.info("Embedding index created.")
    return vector_store


def load_or_create_vector_db(overwrite=False):
    """Loads a vector database or creates a new one if it doesn't exist."""
    full_vector_path = os.path.join(VECTOR_DB_PATH,VECTOR_STORE_FILE)
    if os.path.exists(full_vector_path) and not overwrite:
        logging.info("Loading existing vector database.")
        embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
        try:
             vector_store = FAISS.load_local(VECTOR_DB_PATH, embeddings, allow_dangerous_deserialization=True)
             return vector_store
        except Exception as e:
            logging.error(f"Error loading vector database: {e}")
            return None
    else:
        logging.info("Creating new vector database.")
        chunks = load_and_chunk_pdf(PDF_FILE_PATH)
        if chunks:
            vector_store = create_embedding_index(chunks)
            os.makedirs(VECTOR_DB_PATH, exist_ok=True)
            vector_store.save_local(VECTOR_DB_PATH)
            return vector_store
        else:
            return None

# Template for prompt
template = f"""{PERSONA}
Answer the user's question based on the context provided.
Context: {{context}}
Question: {{question}}

If the user's question is not related to Automata Theory, respond with:
"I am designed to answer only questions regarding Automata Theory and cannot answer general questions."
"""

prompt = ChatPromptTemplate.from_messages([
     ("system", template),
     HumanMessagePromptTemplate.from_template("{question}"),
 ])

# Initialize llm
llm = model

def create_chain(overwrite=False):
    vector_db = load_or_create_vector_db(overwrite)
    if vector_db is None:
      logging.error("Failed to load or create a vector database")
      return None
    chain = (
        {"question": RunnablePassthrough(), "context": (RunnableLambda(lambda x : vector_db.similarity_search(x, k=4)) | (lambda x: "\n".join([i.page_content for i in x]))  ) }
        | prompt
        | RunnableLambda(lambda x: llm.generate_content(contents=[i.content for i in x.to_messages()]).text)
        | StrOutputParser()
    )
    return chain

def format_response(text):
   """Formats the response with markdown and adds extra lines for visual structure"""
   return f"\n{text}\n"


if __name__ == '__main__':
    chain = create_chain()
    if chain:
        while True:
            question = input("Ask a question about Automata Theory (or type 'exit' to quit): ")
            if question.lower() == 'exit':
                break
            logging.info(f"User Question: {question}")
            answer = chain.invoke(question)
            logging.info(f"Model Response: {answer}")
            formatted_answer = format_response(answer)
            print(f"Answer: {formatted_answer}")